"""Anomaly → GraphRAG diagnosis → incident-playbooks topic (app/incident_trigger.py)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.diagnostic_engine import DiagnosticEngine
from app.incident_trigger import IncidentTrigger, PlaybookPublisher, anomaly_context
from tests.conftest import FakeProducer, event


def _trigger(
    engine: DiagnosticEngine, producer: FakeProducer, clock: Callable[[], float]
) -> IncidentTrigger:
    publisher = PlaybookPublisher("kafka:9092", "incident-playbooks", lambda _bs: producer)
    return IncidentTrigger(engine, publisher, cooldown_seconds=300, batch_seconds=0, clock=clock)


def _anomaly(e: dict[str, Any]) -> dict[str, Any]:
    return anomaly_context(e, 0.999, 0.8, "latency_ms")


def test_anomaly_context_shape() -> None:
    a = _anomaly(event(1, 950, source="checkout-api"))
    assert a["kind"] == "event_anomaly"
    assert a["metric_name"] == "latency_ms" and a["observed_value"] == 950
    assert a["severity"] == "critical"


def test_burst_is_one_diagnosis_then_cooldown(make_engine: Callable[..., DiagnosticEngine]) -> None:
    now = [1000.0]
    producer = FakeProducer()
    trig = _trigger(make_engine(), producer, lambda: now[0])
    e = event(1, 950, source="checkout-api")
    assert trig.submit(e, _anomaly(e)) == "queued"
    assert trig.submit(e, _anomaly(e)) == "batched"
    result = trig.diagnose("checkout-api")
    assert result is not None and result.outcome == "fallback"
    assert trig.submit(e, _anomaly(e)) == "cooldown"
    now[0] += 301
    assert trig.submit(e, _anomaly(e)) == "queued"

    topic, value, key = producer.sent[0]
    record = json.loads(value)
    assert topic == "incident-playbooks" and key == b"checkout-api"
    assert record["trigger"] == "anomaly" and len(record["anomalies"]) == 2
    assert len(record["steps"]) >= 3
    assert "latency_ms anomaly detected on checkout-api" in record["summary"]
    assert trig.recent[0]["playbook_id"] == record["playbook_id"]


def test_unknown_component_is_skipped(make_engine: Callable[..., DiagnosticEngine]) -> None:
    producer = FakeProducer()
    trig = _trigger(make_engine(), producer, lambda: 0.0)
    e = event(1, 950, source="not-in-graph")
    trig.submit(e, _anomaly(e))
    assert trig.diagnose("not-in-graph") is None
    assert producer.sent == []


def test_detector_hands_anomalies_to_the_trigger(
    settings: Any, audit: Any, make_engine: Callable[..., DiagnosticEngine]
) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    producer = FakeProducer()
    trig = _trigger(make_engine(), producer, lambda: 0.0)
    with TestClient(create_app(settings, audit=audit, trigger=trig)) as c:
        for i in range(30):
            c.post(f"/score/b{i}", json={"event": event(i, 100 + i % 5, source="payments-api")})
        r = c.post("/score/spike", json={"event": event(99, 5000, source="payments-api")})
        assert r.json()["anomalous"] is True
        # the trigger's worker thread (started by the app) runs the diagnosis
        deadline = time.monotonic() + 5
        while not trig.recent and time.monotonic() < deadline:
            time.sleep(0.05)
        assert c.get("/incidents").json()["recent"][0]["component_id"] == "payments-api"
        ctx = c.get("/graph/payments-api").json()
        assert {d["node_id"] for d in ctx["dependencies"]} >= {"auth-service", "kafka"}
