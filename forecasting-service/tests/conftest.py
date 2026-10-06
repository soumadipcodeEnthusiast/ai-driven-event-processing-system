from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from app.config import Settings
from app.forecasting_engine import ForecastingEngine

SERVICE_ROOT = Path(__file__).resolve().parent.parent
PROM = "http://prom.test"
DIAG = "http://diag.test"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

SeriesFn = Callable[[np.ndarray], np.ndarray]  # minutes-relative-to-now → values


def rising(now_value: float, slope_per_min: float, noise: float = 0.002) -> SeriesFn:
    def fn(minutes: np.ndarray) -> np.ndarray:
        rng = np.random.default_rng(7)
        return now_value + slope_per_min * minutes + rng.normal(0, noise, len(minutes))

    return fn


def flat(value: float, noise: float = 0.001) -> SeriesFn:
    return rising(value, 0.0, noise)


class FakePrometheus:
    """Serves synthetic matrices; series chosen by a substring of the PromQL."""

    def __init__(self) -> None:
        self.series: dict[str, SeriesFn] = {}
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(
                503, json={"status": "error", "errorType": "unavailable", "error": "down"}
            )
        q = request.url.params["query"]
        start = float(request.url.params["start"])
        end = float(request.url.params["end"])
        step = 60.0
        ts = np.arange(start, end + 1, step)
        for key, fn in self.series.items():
            if key in q:
                minutes = (ts - end) / 60.0
                vals = fn(minutes)
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {},
                                    "values": [
                                        [float(t), str(v)] for t, v in zip(ts, vals, strict=True)
                                    ],
                                }
                            ],
                        },
                    },
                )
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
        )


@pytest.fixture
def components_file(tmp_path: Path) -> Path:
    dst = tmp_path / "components.yaml"
    shutil.copy(SERVICE_ROOT / "config" / "components.yaml", dst)
    return dst


@pytest.fixture
def settings(components_file: Path) -> Settings:
    return Settings(
        prometheus_url=PROM,
        diagnostic_url=DIAG,
        components_config=str(components_file),
        forecaster="statistical",
        forecast_interval_seconds=0,
        diagnostic_backoff_seconds=0,
        alert_min_lead_minutes=10,
    )


@pytest.fixture
def prom() -> FakePrometheus:
    return FakePrometheus()


@pytest.fixture
def mock_http(prom: FakePrometheus) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{PROM}/api/v1/query_range").mock(side_effect=prom)
        router.post(url__regex=rf"{DIAG}/diagnose/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "component_id": "x",
                    "playbook_id": "pb-ingestion-service-0123abcd",
                    "summary": "s",
                    "details": {},
                },
            )
        )
        yield router


@pytest.fixture
def engine(settings: Settings, mock_http: respx.MockRouter) -> Iterator[ForecastingEngine]:
    eng = ForecastingEngine(settings, clock=lambda: NOW)
    yield eng
    eng.close()


def ingestion_breach_series(prom: FakePrometheus, now_cpu: float = 0.65) -> None:
    """CPU rising 0.003/min (crosses 0.80 in ~50 min); memory and errors flat."""
    prom.series = {
        "process_cpu_usage": rising(now_cpu, 0.003),
        "jvm_memory": flat(0.40),
        "http_server_requests": flat(0.001, 0.0001),
    }


def as_dict(obj: Any) -> dict[str, Any]:
    assert isinstance(obj, dict)
    return obj
