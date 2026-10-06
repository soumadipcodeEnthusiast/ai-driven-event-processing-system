from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.forecasting_engine import ForecastingEngine
from app.main import create_app
from tests.conftest import NOW, FakePrometheus, ingestion_breach_series


@pytest.fixture
def client(settings: Settings, mock_http: respx.MockRouter) -> Iterator[TestClient]:
    engine = ForecastingEngine(settings, clock=lambda: NOW)
    with TestClient(create_app(settings, engine)) as c:
        yield c


def test_ops_endpoints(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    client.get("/alerts")
    body = client.get("/metrics").text
    for name in ("forecaster_info{", "predictive_alerts_open", "http_requests_total{"):
        assert name in body
    assert 'forecaster="statistical"' in body
    assert 'model_version="statistical-holt-damped-1.0"' in body


def test_forecast_and_alert_lifecycle(client: TestClient, prom: FakePrometheus) -> None:
    ingestion_breach_series(prom)
    r = client.post("/forecast", json={"component_id": "ingestion-service"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"component_id", "alert_emitted", "alert", "details"}
    assert body["alert_emitted"] is True
    alert_id = body["alert"]["alert_id"]
    app_engine: ForecastingEngine = client.app.state.engine  # type: ignore[attr-defined]
    app_engine.wait_for_handoffs()

    alerts = client.get("/alerts", params={"status": "open"}).json()
    assert isinstance(alerts, list) and alerts[0]["alert_id"] == alert_id
    assert alerts[0]["playbook_id"] == "pb-ingestion-service-0123abcd"
    assert alerts[0]["created_at"].endswith("Z")
    assert client.get("/alerts", params={"component_id": "kafka"}).json() == []
    assert client.get(f"/alerts/{alert_id}").json()["alert_id"] == alert_id
    assert client.get("/alerts/al-000000000000").status_code == 404

    assert client.post(f"/alerts/{alert_id}/ack").json()["status"] == "acknowledged"
    assert client.get("/alerts", params={"status": "open"}).json() == []
    assert client.post(f"/alerts/{alert_id}/resolve").json()["status"] == "resolved"
    assert client.post(f"/alerts/{alert_id}/ack").status_code == 409
    assert client.post("/alerts/al-000000000000/resolve").status_code == 404
    assert "predictive_alerts_total{" in client.get("/metrics").text
    assert (
        'forecast_runs_total{component="ingestion-service",outcome="ok"}'
        in client.get("/metrics").text
    )


def test_forecast_errors(client: TestClient, prom: FakePrometheus) -> None:
    assert client.post("/forecast", json={"component_id": "nope"}).status_code == 404
    assert client.post("/forecast", json={}).status_code == 422
    prom.fail = True
    r = client.post("/forecast", json={"component_id": "kafka"})
    assert r.status_code == 503
    assert 'forecast_runs_total{component="kafka",outcome="error"}' in client.get("/metrics").text


def test_alert_list_filters(client: TestClient) -> None:
    assert client.get("/alerts", params={"status": "bogus"}).status_code == 422
    assert client.get("/alerts", params={"limit": 0}).status_code == 422


def test_thresholds_endpoints(client: TestClient, prom: FakePrometheus) -> None:
    th = client.get("/thresholds").json()
    assert th["ingestion-service"]["cpu_utilisation"] == 0.8
    assert th["zookeeper"] == {}

    r = client.put("/thresholds/ingestion-service", json={"cpu_utilisation": 0.95})
    assert r.status_code == 200 and r.json()["cpu_utilisation"] == 0.95
    assert client.get("/thresholds").json()["ingestion-service"]["cpu_utilisation"] == 0.95

    # takes effect without restart: no alert with the raised threshold
    ingestion_breach_series(prom)
    assert (
        client.post("/forecast", json={"component_id": "ingestion-service"}).json()["alert_emitted"]
        is False
    )

    assert client.put("/thresholds/nope", json={"cpu_utilisation": 1}).status_code == 404
    assert client.put("/thresholds/kafka", json={"cpu": 1}).status_code == 422
    assert client.put("/thresholds/kafka", json={}).status_code == 422
    assert client.put("/thresholds/kafka", json={"consumer_lag": "x"}).status_code == 422


def test_background_loop_runs_all_components(
    settings: Settings, mock_http: respx.MockRouter, prom: FakePrometheus
) -> None:
    import time

    ingestion_breach_series(prom)
    settings.forecast_interval_seconds = 0.05
    engine = ForecastingEngine(settings, clock=lambda: NOW)
    with TestClient(create_app(settings, engine)):
        deadline = time.time() + 5
        while time.time() < deadline and not engine.alerts.list():
            time.sleep(0.05)
    assert engine.alerts.list(component_id="ingestion-service")


def test_alerts_summary_is_live(client: TestClient, prom: FakePrometheus) -> None:
    empty = client.get("/alerts/summary").json()
    assert empty["active"] == 0 and empty["min_minutes_to_breach"] is None

    ingestion_breach_series(prom)
    alert = client.post("/forecast", json={"component_id": "ingestion-service"}).json()["alert"]
    summary = client.get("/alerts/summary").json()
    assert summary["active"] == summary["open"] == 1
    assert summary["by_severity"][alert["severity"]] == 1
    assert summary["next_breach_alert_id"] == alert["alert_id"]
    # Computed from predicted_breach_time at request time (engine clock = NOW here).
    assert summary["min_minutes_to_breach"] == pytest.approx(alert["lead_time_minutes"], abs=0.01)

    client.post(f"/alerts/{alert['alert_id']}/ack")
    assert client.get("/alerts/summary").json()["acknowledged"] == 1
    client.post(f"/alerts/{alert['alert_id']}/resolve")
    assert client.get("/alerts/summary").json()["active"] == 0
