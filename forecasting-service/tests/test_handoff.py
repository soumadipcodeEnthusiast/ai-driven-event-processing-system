"""Predictive alert → diagnostic-service GraphRAG playbook hand-off."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import respx
from fastapi.testclient import TestClient

from app.alerts import AlertSeverity, PredictiveAlert
from app.config import Settings
from app.diagnostic_client import DiagnosticClient
from app.forecasting_engine import ForecastingEngine
from app.main import create_app
from tests.conftest import DIAG, NOW, FakePrometheus, ingestion_breach_series


def _alert() -> PredictiveAlert:
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return PredictiveAlert(
        alert_id="al-000000000001",
        component_id="kafka",
        metric_name="consumer_lag",
        predicted_breach_time=now,
        predicted_value=12000.0,
        threshold=10000.0,
        lead_time_minutes=25.0,
        severity=AlertSeverity.MEDIUM,
        created_at=now,
        model_version="tft-test",
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
    assert body["anomalies"][0]["kind"] == "predicted_breach"
    assert body["anomalies"][0]["metric_name"] == "consumer_lag"


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


def test_new_alert_is_linked_to_its_playbook(
    settings: Settings, mock_http: respx.MockRouter, prom: FakePrometheus
) -> None:
    ingestion_breach_series(prom)
    engine = ForecastingEngine(settings, clock=lambda: NOW)
    with TestClient(create_app(settings, engine)) as client:
        body = client.post("/forecast", json={"component_id": "ingestion-service"}).json()
        assert body["alert_emitted"] is True
        engine.wait_for_handoffs()
        alert = client.get(f"/alerts/{body['alert']['alert_id']}").json()
    assert alert["playbook_id"] == "pb-ingestion-service-0123abcd"
    assert mock_http.calls.last.request.url.path == "/diagnose/ingestion-service"
