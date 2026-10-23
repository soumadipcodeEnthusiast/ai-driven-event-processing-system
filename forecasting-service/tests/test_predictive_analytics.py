"""Predictive analytics (REQ-021 to REQ-025): ML model, confidence, prediction store, API."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.forecasters import MLForecaster, build_forecaster
from app.forecasting_engine import ForecastingEngine
from app.main import create_app
from app.predictive_analytics import (
    DEFAULT_MODEL_KEY,
    PredictionStore,
    PredictiveAnalyticsModel,
    band_confidence,
)
from tests.conftest import NOW, FakePrometheus, ingestion_breach_series
from training import train_forecast_model


def seasonal(n: int, *, level: float = 0.5, amp: float = 0.1, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return level + amp * np.sin(2 * np.pi * t / 60) + rng.normal(0, 0.005, n)


@pytest.fixture
def trained(tmp_path: Path) -> tuple[PredictiveAnalyticsModel, Path]:
    model = PredictiveAnalyticsModel("forecast-test-1", lags=30)
    model.train({"cpu": [seasonal(600, seed=s) for s in range(3)]})
    path = tmp_path / "model.json"
    model.save(path)
    return model, path


def test_train_fits_per_metric_and_pooled_models(
    trained: tuple[PredictiveAnalyticsModel, Path],
) -> None:
    model, _ = trained
    assert set(model.weights) == {"cpu", DEFAULT_MODEL_KEY}
    assert len(model.weights["cpu"]["coef"]) == 30


def test_model_beats_naive_on_seasonal_series(
    trained: tuple[PredictiveAnalyticsModel, Path],
) -> None:
    model, _ = trained
    series = seasonal(400, seed=99)
    hist, actual = series[:-30], series[-30:]
    point, upper = model.forecast("cpu", hist, 30)
    naive = np.full(30, hist[-1])
    assert np.mean(np.abs(point - actual)) < np.mean(np.abs(naive - actual))
    assert np.all(upper >= point)


def test_predict_flags_low_confidence(trained: tuple[PredictiveAnalyticsModel, Path]) -> None:
    model, _ = trained
    steady = model.predict("c1", "cpu", 10, seasonal(100, seed=1))
    assert steady.model_version == "forecast-test-1"
    assert len(steady.values) == 10 and not steady.low_confidence
    noisy = np.random.default_rng(3).normal(0.05, 0.5, 100)  # noise ≫ level
    assert model.predict("c1", "cpu", 10, noisy).low_confidence


def test_save_load_roundtrip(trained: tuple[PredictiveAnalyticsModel, Path]) -> None:
    model, path = trained
    loaded = PredictiveAnalyticsModel.load(path)
    assert loaded.model_version == "forecast-test-1"
    hist = seasonal(100)
    expected = model.forecast("cpu", hist, 5)[0]
    np.testing.assert_allclose(loaded.forecast("cpu", hist, 5)[0], expected)
    # unknown metric falls back to the pooled model
    assert len(loaded.forecast("memory", hist, 5)[0]) == 5


def test_load_rejects_foreign_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "pickle"}))
    with pytest.raises(ValueError):
        PredictiveAnalyticsModel.load(bad)


def test_untrained_and_short_history_errors(trained: tuple[PredictiveAnalyticsModel, Path]) -> None:
    with pytest.raises(ValueError):
        PredictiveAnalyticsModel("v").forecast("cpu", seasonal(100), 5)
    with pytest.raises(ValueError):
        trained[0].forecast("cpu", seasonal(10), 5)


def test_band_confidence() -> None:
    assert band_confidence([1.0], [1.0], 1.0) == 1.0
    assert band_confidence([1.0], [2.0], 1.0) == 0.5
    assert band_confidence([], [], 1.0) == 0.0


def test_ml_forecaster_selected_when_model_exists(
    trained: tuple[PredictiveAnalyticsModel, Path],
) -> None:
    _, path = trained
    fc = build_forecaster("auto", str(path), "")
    assert fc.name == "ml" and fc.model_version == "forecast-test-1"
    out = fc.predict(seasonal(100), 10, metric_name="cpu")
    assert out.forecaster == "ml" and len(out.point) == 10
    # explicit version override (ML_MODEL_VERSION)
    assert MLForecaster(path, "pinned-2").model_version == "pinned-2"


def test_ml_forecaster_falls_back_on_short_history(
    trained: tuple[PredictiveAnalyticsModel, Path],
) -> None:
    fc = build_forecaster("ml", str(trained[1]), "")
    assert fc.predict(seasonal(15), 5, metric_name="cpu").forecaster == "statistical"


def test_prediction_store_versions_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    store = PredictionStore(path)
    for v in ("m1", "m2"):
        store.record({"component_id": "c", "metric_name": "cpu", "model_version": v})
    store.record({"component_id": "d", "metric_name": "cpu", "model_version": "m2"})
    assert [p["model_version"] for p in store.latest("c")] == ["m2"]
    assert len(store.history(model_version="m2")) == 2
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 3 and all("prediction_id" in p for p in lines)


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(settings: Settings, mock_http: respx.MockRouter) -> Iterator[TestClient]:
    engine = ForecastingEngine(settings, clock=lambda: NOW)
    with TestClient(create_app(settings, engine)) as c:
        yield c


def test_forecast_records_queryable_predictions(client: TestClient, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    body = client.post("/forecast", json={"component_id": "ingestion-service"}).json()
    cpu = body["details"]["metrics"]["cpu_utilisation"]
    assert 0.0 < cpu["confidence"] <= 1.0 and "low_confidence" in cpu

    preds = client.get("/predictions", params={"component_id": "ingestion-service"}).json()
    assert {p["metric_name"] for p in preds} >= {"cpu_utilisation"}
    p = next(p for p in preds if p["metric_name"] == "cpu_utilisation")
    assert p["model_version"] == "statistical-holt-damped-1.0"
    assert len(p["point"]) == len(p["timestamps"]) == len(p["upper_90"])
    hist = client.get("/predictions/history", params={"limit": 1}).json()
    assert len(hist) == 1


# ── training CLI ─────────────────────────────────────────────────────────────


def test_training_cli_from_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "history.csv"
    with src.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "metric", "timestamp", "value"])
        for s in range(2):
            for i, v in enumerate(seasonal(500, seed=s)):
                w.writerow([f"svc-{s}", "cpu_utilisation", 1_760_000_000 + 60 * i, v])
    out = tmp_path / "model.json"
    rc = train_forecast_model.main(
        ["--csv", str(src), "--output", str(out), "--version", "fm-1", "--require-better"]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["backtest_mae"]["model"] < report["backtest_mae"]["baseline"]
    assert PredictiveAnalyticsModel.load(out).model_version == "fm-1"
