from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from app.alerts import AlertSeverity, AlertStatus, AlertStore, PredictiveAlert, severity_for
from app.components import ComponentConfigStore, UnknownMetricError
from app.diagnostic_client import DiagnosticClient
from app.forecasters import (
    FallbackForecaster,
    ForecasterError,
    StatisticalForecaster,
    build_forecaster,
)
from app.forecasting_engine import ForecastingEngine
from app.metric_client import MetricClient, MetricClientError, parse_duration_seconds
from tests.conftest import (
    DIAG,
    NOW,
    PROM,
    FakePrometheus,
    flat,
    ingestion_breach_series,
    rising,
)

SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "predictive-alert.schema.json"


# ─── severity ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.80, AlertSeverity.LOW),  # 0 %
        (0.879, AlertSeverity.LOW),  # 9.9 %
        (0.88, AlertSeverity.MEDIUM),  # 10 %
        (0.999, AlertSeverity.MEDIUM),  # 24.9 %
        (1.00, AlertSeverity.HIGH),  # 25 %
        (1.199, AlertSeverity.HIGH),  # 49.9 %
        (1.20, AlertSeverity.CRITICAL),  # 50 %
        (5.0, AlertSeverity.CRITICAL),
    ],
)
def test_severity_bands(value: float, expected: AlertSeverity) -> None:
    assert severity_for(value, 0.80) is expected


def test_severity_zero_threshold_is_critical() -> None:
    assert severity_for(0.0001, 0.0) is AlertSeverity.CRITICAL
    assert severity_for(0.0, 0.0) is AlertSeverity.LOW


# ─── statistical forecaster ─────────────────────────────────────────────────


def test_statistical_forecaster_predicts_breach_on_rising_series() -> None:
    minutes = np.arange(-179, 1, dtype=float)
    y = rising(0.65, 0.003)(minutes)
    fc = StatisticalForecaster().predict(y, 60)
    assert fc.point.shape == (60,) and fc.upper_90.shape == (60,)
    assert np.all(fc.upper_90 >= fc.point)
    assert fc.point[-1] > 0.80  # trend extrapolated: ~0.83 after 60 min
    first = int(np.nonzero(fc.upper_90 > 0.80)[0][0])
    assert 20 <= first <= 55  # true crossing ~50 min; p90 crosses a bit earlier


def test_statistical_forecaster_flat_series_no_breach() -> None:
    y = flat(0.40)(np.arange(-179, 1, dtype=float))
    fc = StatisticalForecaster().predict(y, 60)
    assert fc.upper_90.max() < 0.45
    assert abs(fc.point[-1] - 0.40) < 0.02


def test_statistical_forecaster_band_widens() -> None:
    rng = np.random.default_rng(1)
    y = 0.5 + rng.normal(0, 0.05, 180)
    fc = StatisticalForecaster().predict(y, 30)
    width = fc.upper_90 - fc.point
    assert width[-1] >= width[0] > 0


def test_statistical_forecaster_needs_data() -> None:
    with pytest.raises(ForecasterError):
        StatisticalForecaster().predict(np.array([1.0, 2.0]), 10)


def test_forecaster_selection_without_ml(tmp_path: Path) -> None:
    assert build_forecaster("statistical", "", "v").name == "statistical"
    assert build_forecaster("auto", str(tmp_path / "missing.ckpt"), "v").name == "statistical"
    assert build_forecaster("tft", str(tmp_path / "missing.ckpt"), "v").name == "statistical"


def test_fallback_forecaster_uses_secondary_on_error() -> None:
    class Broken(StatisticalForecaster):
        name = "tft"

        def predict(self, *a: object, **k: object):
            raise RuntimeError("boom")

    fb = FallbackForecaster(Broken(), StatisticalForecaster())
    fc = fb.predict(np.linspace(0, 1, 50), 5)
    assert fc.forecaster == "statistical"


def test_forecast_latency_under_two_seconds(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom)
    t0 = time.perf_counter()
    engine.run_cycle("ingestion-service")
    assert time.perf_counter() - t0 < 2.0


# ─── engine: lead time, dedup, hand-off ─────────────────────────────────────


def test_alert_emitted_with_sufficient_lead(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom)
    res = engine.run_cycle("ingestion-service")
    assert res["alert_emitted"] is True
    alert = res["alert"]
    assert alert["metric_name"] == "cpu_utilisation"
    assert alert["lead_time_minutes"] >= 10
    assert alert["predicted_value"] > alert["threshold"] == 0.8
    assert alert["status"] == "open"
    assert alert["model_version"] == "statistical-holt-damped-1.0"
    assert res["details"]["metrics"]["memory_utilisation"]["outcome"] == "no_breach"
    engine.wait_for_handoffs()
    stored = engine.alerts.get(alert["alert_id"])
    assert stored is not None and stored.playbook_id == "pb-ingestion-service-0123abcd"


def test_alert_matches_contract_schema(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    if not SCHEMA.exists():
        pytest.skip("contracts/ not available")
    ingestion_breach_series(prom)
    res = engine.run_cycle("ingestion-service")
    engine.wait_for_handoffs()
    schema = json.loads(SCHEMA.read_text())
    jsonschema.validate(res["alert"], schema)
    jsonschema.validate(engine.alerts.get(res["alert"]["alert_id"]).to_dict(), schema)  # type: ignore[union-attr]


def test_late_breach_still_alerts_and_is_flagged(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom, now_cpu=0.79)  # crosses 0.80 within ~3 min
    res = engine.run_cycle("ingestion-service")
    assert res["alert_emitted"] is True
    cpu = res["details"]["metrics"]["cpu_utilisation"]
    assert cpu["outcome"] == "alert_created"
    assert cpu["late"] is True
    assert 0 <= res["alert"]["lead_time_minutes"] < 10


def test_lowered_threshold_on_breaching_metric_alerts_immediately(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom)
    engine.config.set_thresholds("ingestion-service", {"cpu_utilisation": 0.01})
    res = engine.run_cycle("ingestion-service")
    assert res["alert_emitted"] is True
    alert = res["alert"]
    assert alert["metric_name"] == "cpu_utilisation"
    assert alert["lead_time_minutes"] == 0
    assert alert["severity"] == "critical"  # peak p90 is far above 0.01


def test_severity_uses_peak_of_horizon(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    res = engine.run_cycle("ingestion-service")
    cpu = res["details"]["metrics"]["cpu_utilisation"]
    assert res["alert"]["predicted_value"] == pytest.approx(cpu["max_upper_90"], rel=1e-6)


def test_dedup_open_and_acknowledged(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    first = engine.run_cycle("ingestion-service")["alert"]["alert_id"]
    second = engine.run_cycle("ingestion-service")
    assert second["alert_emitted"] is False
    assert second["details"]["metrics"]["cpu_utilisation"]["outcome"] == "deduplicated"
    engine.alerts.transition(first, AlertStatus.ACKNOWLEDGED)
    assert engine.run_cycle("ingestion-service")["alert_emitted"] is False
    engine.alerts.transition(first, AlertStatus.RESOLVED)
    third = engine.run_cycle("ingestion-service")
    assert third["alert_emitted"] is True and third["alert"]["alert_id"] != first
    assert len(engine.alerts.list()) == 2
    assert len(engine.alerts.list(status=AlertStatus.OPEN)) == 1


def test_threshold_override_applies_to_next_cycle(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom)
    engine.config.set_thresholds("ingestion-service", {"cpu_utilisation": 5.0})
    res = engine.run_cycle("ingestion-service")
    assert res["alert_emitted"] is False
    assert res["details"]["metrics"]["cpu_utilisation"]["outcome"] == "no_breach"


def test_breach_time_is_in_future(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    alert = engine.evaluate_threshold(
        "ingestion-service", engine.forecast(engine.poll_metrics("ingestion-service"))
    )
    assert alert is not None
    assert alert.predicted_breach_time - NOW >= timedelta(minutes=10)


def test_poll_metrics_shape(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    obs = engine.poll_metrics("ingestion-service")
    names = {o["metric_name"] for o in obs}
    assert names == {"cpu_utilisation", "memory_utilisation", "error_rate"}
    assert all(set(o) == {"metric_name", "timestamp", "value"} for o in obs)


def test_missing_series_is_reported_as_no_data(
    engine: ForecastingEngine, prom: FakePrometheus
) -> None:
    prom.series = {"process_cpu_usage": flat(0.3)}
    res = engine.run_cycle("ingestion-service")
    assert res["details"]["metrics"]["error_rate"]["outcome"] == "no_data"


def test_run_all_never_raises(engine: ForecastingEngine, prom: FakePrometheus) -> None:
    prom.fail = True
    outcomes = engine.run_all()
    assert set(outcomes.values()) == {"error"}
    assert "zookeeper" not in outcomes  # forecast-disabled


# ─── diagnostic hand-off retries ────────────────────────────────────────────


def _alert() -> PredictiveAlert:
    return PredictiveAlert(
        "al-000000000001",
        "kafka",
        "consumer_lag",
        NOW,
        1.0,
        0.5,
        20.0,
        AlertSeverity.CRITICAL,
        NOW,
        "v",
    )


@respx.mock
def test_handoff_retries_on_5xx_then_succeeds() -> None:
    route = respx.post(f"{DIAG}/diagnose/kafka").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"playbook_id": "pb-kafka-deadbeef"}),
        ]
    )
    client = DiagnosticClient(DIAG, sleep=lambda _s: None)
    assert client.diagnose("kafka", "al-000000000001", [_alert().anomaly()]) == "pb-kafka-deadbeef"
    assert route.call_count == 2
    body = json.loads(route.calls[0].request.content)
    assert body["alert_id"] == "al-000000000001"
    assert set(body["anomalies"][0]) == {
        "metric_name",
        "predicted_value",
        "threshold",
        "predicted_breach_time",
        "severity",
    }


@respx.mock
def test_handoff_never_retries_4xx() -> None:
    route = respx.post(f"{DIAG}/diagnose/kafka").mock(return_value=httpx.Response(404))
    assert DiagnosticClient(DIAG, sleep=lambda _s: None).diagnose("kafka", "al-1", []) is None
    assert route.call_count == 1


@respx.mock
def test_handoff_three_attempts_total_on_network_error() -> None:
    route = respx.post(f"{DIAG}/diagnose/kafka").mock(side_effect=httpx.ConnectError("x"))
    sleeps: list[float] = []
    assert DiagnosticClient(DIAG, sleep=sleeps.append).diagnose("kafka", "al-1", []) is None
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


# ─── alert store ────────────────────────────────────────────────────────────


def test_alert_store_transitions() -> None:
    from app.alerts import InvalidTransitionError

    store = AlertStore()
    a = _alert()
    assert store.add_if_absent(a)
    assert store.transition(a.alert_id, AlertStatus.ACKNOWLEDGED).status is AlertStatus.ACKNOWLEDGED  # type: ignore[union-attr]
    assert store.transition(a.alert_id, AlertStatus.RESOLVED).status is AlertStatus.RESOLVED  # type: ignore[union-attr]
    with pytest.raises(InvalidTransitionError):
        store.transition(a.alert_id, AlertStatus.ACKNOWLEDGED)
    assert store.transition("al-missing", AlertStatus.RESOLVED) is None


# ─── components config hot reload (REQ-F) ───────────────────────────────────


def _bump(path: Path, text: str) -> None:
    path.write_text(text)
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))


def test_components_hot_reload(components_file: Path) -> None:
    store = ComponentConfigStore(components_file)
    assert store.thresholds()["ingestion-service"]["cpu_utilisation"] == 0.8
    _bump(
        components_file,
        components_file.read_text().replace("threshold: 0.80", "threshold: 0.70", 1),
    )
    assert store.thresholds()["ingestion-service"]["cpu_utilisation"] == 0.7


def test_override_survives_reload_and_invalid_file_keeps_config(components_file: Path) -> None:
    store = ComponentConfigStore(components_file)
    store.set_thresholds("kafka", {"consumer_lag": 500})
    _bump(components_file, components_file.read_text() + "\n# touched\n")
    assert store.thresholds()["kafka"]["consumer_lag"] == 500
    _bump(components_file, "components: [this is: invalid")
    assert store.thresholds()["kafka"]["consumer_lag"] == 500
    assert "ingestion-service" in store.components()
    with pytest.raises(UnknownMetricError):
        store.set_thresholds("kafka", {"nope": 1})


def test_all_contract_component_ids_present(components_file: Path) -> None:
    ids = set(ComponentConfigStore(components_file).components(include_disabled=True))
    assert ids == {
        "ingestion-service",
        "forecasting-service",
        "diagnostic-service",
        "kafka",
        "zookeeper",
        "prometheus",
        "graph-db",
        "llm",
    }


# ─── metric client ──────────────────────────────────────────────────────────


@respx.mock
def test_metric_client_errors_and_instant() -> None:
    respx.get(f"{PROM}/api/v1/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "0.5"]}]},
            },
        )
    )
    respx.get(f"{PROM}/api/v1/query_range").mock(
        return_value=httpx.Response(
            400, json={"status": "error", "errorType": "bad_data", "error": "parse error"}
        )
    )
    c = MetricClient(PROM)
    assert c.query_instant("up")[0]["value"][1] == "0.5"
    with pytest.raises(MetricClientError, match="bad_data"):
        c.query_range("up{", NOW, NOW)


@respx.mock
def test_metric_client_network_error() -> None:
    respx.get(f"{PROM}/api/v1/query").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(MetricClientError):
        MetricClient(PROM).query_instant("up")


def test_parse_duration() -> None:
    assert parse_duration_seconds("60s") == 60
    assert parse_duration_seconds("1m") == 60
    assert parse_duration_seconds("15") == 15
    with pytest.raises(ValueError):
        parse_duration_seconds("abc")


@respx.mock
def test_handoff_retries_429_honouring_retry_after() -> None:
    route = respx.post(f"{DIAG}/diagnose/kafka").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"playbook_id": "pb-kafka-deadbeef"}),
        ]
    )
    sleeps: list[float] = []
    client = DiagnosticClient(DIAG, sleep=sleeps.append)
    assert client.diagnose("kafka", "al-1", []) == "pb-kafka-deadbeef"
    assert route.call_count == 2
    assert sleeps == [7.0]
