"""Anomaly detection (REQ-016 to REQ-020): scorer, thresholds, audit, consumer and API."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import jsonschema
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth
from app.anomaly_scorer import AnomalyScorer, numeric_fields
from app.audit_publisher import AuditPublisher
from app.config import Settings
from app.detector import AnomalyDetector
from app.event_consumer import EventStreamScorer
from app.main import create_app
from app.threshold_manager import ThresholdManager
from tests.conftest import FakeMessage, FakeProducer, event

AUDIT_SCHEMA = (
    Path(__file__).resolve().parents[2] / "contracts" / "anomaly-audit.schema.json"
)


def warm(scorer: AnomalyScorer, n: int = 60, **kw: Any) -> None:
    for i in range(n):
        scorer.score_event(event(i, 100.0 + (i % 7) - 3, **kw))


# ── REQ-016: scoring ──────────────────────────────────────────────────────────


def test_score_event_returns_score_in_expected_range() -> None:
    scorer = AnomalyScorer("v1")
    for i in range(50):
        s = scorer.score_event(event(i, 100.0 + (i % 5)))
        assert 0.0 <= s.score <= 1.0
        assert s.model_version == "v1"
        assert s.event_id == f"evt-{i}"


def test_warm_up_scores_zero_until_baseline_exists() -> None:
    scorer = AnomalyScorer("v1", min_samples=10)
    scores = [scorer.score_event(event(i, 100.0 * (i + 1))).score for i in range(9)]
    assert scores == [0.0] * 9


def test_outlier_scores_high_and_normal_scores_low() -> None:
    scorer = AnomalyScorer("v1")
    warm(scorer)
    normal = scorer.score_event(event(1000, 101.0))
    outlier = scorer.score_event(event(1001, 400.0))
    assert normal.score < 0.5
    assert outlier.score > 0.95
    assert outlier.reason == "latency_ms"


def test_streams_are_independent() -> None:
    scorer = AnomalyScorer("v1")
    warm(scorer, source="checkout")
    warm(scorer, source="search")
    # 100 ms is normal for both; a 400 ms checkout doesn't make search anomalous
    scorer.score_event(event(1, 400.0, source="checkout"))
    assert scorer.score_event(event(2, 100.0, source="search")).score < 0.5


def test_novel_event_type_is_anomalous() -> None:
    scorer = AnomalyScorer("v1", min_samples=10)
    for i in range(200):
        scorer.score_event(event(i, 100.0, type_="request"))
    s = scorer.score_event(event(999, 100.0, type_="kernel_panic"))
    assert s.reason == "type"
    assert s.score > 0.8


def test_numeric_fields_flattens_and_skips_bools_and_nan() -> None:
    payload = {"a": 1, "b": True, "c": {"d": 2.5}, "e": "x", "f": math.nan}
    assert numeric_fields(payload) == {"a": 1.0, "c.d": 2.5}


def test_scorer_model_version_swap_without_downtime() -> None:
    scorer = AnomalyScorer("v1")
    warm(scorer)
    warm_new = scorer.with_version("v2", warm_start=True)
    cold_new = scorer.with_version("v3", warm_start=False)
    assert warm_new.model_version == "v2"
    # warm start keeps baselines → outlier detected immediately
    assert warm_new.score_event(event(1, 400.0)).score > 0.95
    # cold start has no baseline yet → warm-up
    assert cold_new.score_event(event(1, 400.0)).score == 0.0
    # the old scorer is untouched by the copy
    assert scorer.model_version == "v1"


def test_scorer_bounds_memory() -> None:
    scorer = AnomalyScorer("v1", max_streams=5)
    for i in range(20):
        scorer.score_event(event(i, 1.0, source=f"svc-{i}"))
    assert scorer.stream_count == 5


# ── REQ-017 / REQ-020: thresholds ─────────────────────────────────────────────


def test_threshold_evaluate_above_and_below_boundary() -> None:
    tm = ThresholdManager(0.8)
    assert tm.evaluate(0.81)
    assert not tm.evaluate(0.8)  # must strictly exceed
    assert not tm.evaluate(0.79)


def test_threshold_set_threshold_updates_runtime_value() -> None:
    tm = ThresholdManager(0.8)
    tm.set_threshold(0.5, changed_by="admin")
    assert tm.threshold == 0.5
    assert tm.evaluate(0.6)
    assert tm.history[-1].old == 0.8 and tm.history[-1].changed_by == "admin"


@pytest.mark.parametrize("bad", [-0.1, 1.5, math.nan, math.inf])
def test_threshold_rejects_invalid_values(bad: float) -> None:
    tm = ThresholdManager(0.8)
    with pytest.raises(ValueError):
        tm.set_threshold(bad)
    assert tm.threshold == 0.8


# ── REQ-019: audit ────────────────────────────────────────────────────────────


def test_audit_publisher_publishes_on_threshold_breach() -> None:
    producer = FakeProducer()
    audit = AuditPublisher("bs", "anomaly-audit", lambda _: producer)
    scorer = AnomalyScorer("v1")
    warm(scorer)
    detector = AnomalyDetector(scorer, ThresholdManager(0.8), audit)

    assert not detector.process(event(500, 100.0)).anomalous
    assert producer.sent == []

    d = detector.process(event(501, 500.0))
    assert d.anomalous and d.audited
    topic, value, key = producer.sent[0]
    record = json.loads(value)
    assert topic == "anomaly-audit"
    assert key == b"evt-501"
    assert record["event_id"] == "evt-501"
    assert record["threshold"] == 0.8
    assert record["model_version"] == "v1"
    assert record["score"] == d.score
    assert record["schema_version"] == "1.0"
    jsonschema.validate(record, json.loads(AUDIT_SCHEMA.read_text()))


def test_audit_failures_never_raise() -> None:
    def broken(_: str) -> Any:
        raise RuntimeError("no broker")

    audit = AuditPublisher("bs", "anomaly-audit", broken)
    assert audit.publish_anomaly(event(1, 1.0), 0.99) is False
    audit.close()  # nothing was created; must not raise


def test_audit_publisher_with_real_kafka_client() -> None:
    """The real confluent_kafka.Producer enqueues locally, so no broker is needed —
    this pins the produce() call to the library's actual signature."""
    audit = AuditPublisher("127.0.0.1:1", "anomaly-audit")
    assert audit.publish_anomaly(event(1, 1.0), 0.99, threshold=0.8, model_version="v1")
    audit.close(timeout=0.1)  # undeliverable → counted as unflushed, must not raise


def test_audit_delivery_callbacks_served_in_background() -> None:
    """The published counter must move without a later publish/close (rare anomalies)."""
    from app.metrics import ANOMALY_AUDIT_PUBLISHED

    # The ack arrives after publish_anomaly's own poll(0), as with a real broker.
    producer = FakeProducer(ack_delay=0.1)
    audit = AuditPublisher("bs", "t", lambda _: producer)
    before = ANOMALY_AUDIT_PUBLISHED._value.get()
    audit.publish_anomaly(event(1, 1.0), 0.9)
    deadline = time.time() + 2
    while ANOMALY_AUDIT_PUBLISHED._value.get() == before and time.time() < deadline:
        time.sleep(0.05)
    assert ANOMALY_AUDIT_PUBLISHED._value.get() == before + 1
    audit.close()


def test_audit_close_flushes() -> None:
    producer = FakeProducer()
    audit = AuditPublisher("bs", "t", lambda _: producer)
    audit.publish_anomaly(event(1, 1.0), 0.9)
    audit.close()
    assert producer.flushed


# ── REQ-016: Kafka consumer path ──────────────────────────────────────────────


class FakeConsumer:
    def __init__(self) -> None:
        self.stored: list[Any] = []

    def subscribe(self, topics: list[str]) -> None: ...
    def poll(self, timeout: float) -> Any:
        time.sleep(0.01)
        return None

    def store_offsets(self, message: Any) -> None:
        self.stored.append(message)

    def close(self) -> None: ...


def test_consumer_scores_events_and_skips_malformed() -> None:
    producer = FakeProducer()
    scorer = AnomalyScorer("v1")
    warm(scorer)
    audit = AuditPublisher("b", "t", lambda _: producer)
    detector = AnomalyDetector(scorer, ThresholdManager(0.8), audit)
    consumer = FakeConsumer()
    stream = EventStreamScorer(detector, "b", "normalized-events", "g", consumer=consumer)

    assert stream.handle(FakeMessage(json.dumps(event(1, 900.0)).encode()))
    assert not stream.handle(FakeMessage(b"not json"))
    assert not stream.handle(FakeMessage(b"[1,2]"))
    assert len(consumer.stored) == 3  # malformed records don't block the partition
    assert len(producer.sent) == 1


# ── API ───────────────────────────────────────────────────────────────────────


def test_app_starts_and_reports_healthy(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    body = client.get("/metrics").text
    assert "anomaly_threshold 0.8" in body
    assert 'anomaly_model_info{model_version="test-v1"} 1.0' in body


def test_score_endpoint_flags_anomaly_and_audits(
    client: TestClient, producer: FakeProducer
) -> None:
    for i in range(40):
        r = client.post(f"/score/evt-{i}", json={"event": event(i, 100.0 + (i % 5))})
        assert r.status_code == 200
        assert r.json()["anomalous"] is False
    r = client.post("/score/spike", json={"event": event(0, 1000.0)})
    body = r.json()
    assert body["event_id"] == "spike"  # path id wins
    assert body["anomalous"] is True
    assert body["audited"] is True
    assert body["score"] > body["threshold"]
    assert json.loads(producer.sent[-1][1])["event_id"] == "spike"


def test_threshold_endpoints(client: TestClient) -> None:
    assert client.get("/thresholds").json() == {"threshold": 0.8}
    assert client.put("/thresholds", json={"threshold": 0.6}).json() == {"threshold": 0.6}
    assert client.put("/thresholds", json={"threshold": 2}).status_code == 422
    assert client.get("/thresholds").json() == {"threshold": 0.6}


def test_model_swap_endpoint(client: TestClient) -> None:
    client.post("/score/a", json={"event": event(1, 1.0)})
    r = client.put("/model", json={"model_version": "v2"})
    assert r.json() == {"model_version": "v2", "streams": 1}
    assert client.get("/model").json()["model_version"] == "v2"
    assert client.post("/score/b", json={"event": event(2, 1.0)}).json()["model_version"] == "v2"


# ── REQ-046: OAuth 2.0 ────────────────────────────────────────────────────────


@pytest.fixture
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def secured_client(
    monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey, audit: AuditPublisher
) -> Any:
    monkeypatch.setattr(auth, "_signing_key", lambda token, url: rsa_key.public_key())
    s = Settings(
        oauth2_enabled=True,
        oauth2_jwks_url="https://idp.test/jwks",
        oauth2_issuer="https://idp.test",
        oauth2_audience="event-processor",
    )
    with TestClient(create_app(s, audit=audit)) as c:
        yield c


def _token(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims = {
        "sub": "ops-admin",
        "iss": "https://idp.test",
        "aud": "event-processor",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256")


def test_oauth2_rejects_missing_or_bad_tokens(
    secured_client: TestClient, rsa_key: rsa.RSAPrivateKey
) -> None:
    assert secured_client.get("/health").status_code == 200  # ops stay open
    assert secured_client.get("/thresholds").status_code == 401
    bad_aud = {"Authorization": f"Bearer {_token(rsa_key, aud='other')}"}
    assert secured_client.get("/thresholds", headers=bad_aud).status_code == 401
    expired = {"Authorization": f"Bearer {_token(rsa_key, exp=int(time.time()) - 3600)}"}
    assert secured_client.get("/thresholds", headers=expired).status_code == 401
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = {"Authorization": f"Bearer {_token(other_key)}"}
    assert secured_client.get("/thresholds", headers=forged).status_code == 401


def test_oauth2_accepts_valid_token(secured_client: TestClient, rsa_key: rsa.RSAPrivateKey) -> None:
    headers = {"Authorization": f"Bearer {_token(rsa_key)}"}
    assert secured_client.get("/thresholds", headers=headers).status_code == 200
    r = secured_client.put("/thresholds", json={"threshold": 0.7}, headers=headers)
    assert r.json() == {"threshold": 0.7}
