from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.anomaly_scorer import AnomalyScorer
from app.audit_publisher import AuditPublisher
from app.config import Settings
from app.diagnostic_engine import DiagnosticEngine
from app.llm_client import LLMClient
from app.main import create_app
from app.threshold_manager import ThresholdManager

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = SERVICE_ROOT.parent / "contracts"
LLM_URL = "http://llm.test"
ALERT_ID = "al-0123456789ab"

VALID_LLM_JSON = """{
  "summary": "CPU saturation predicted on ingestion-service",
  "root_cause_hypothesis": "Consumer backlog on kafka drives CPU",
  "steps": [
    {"rank": 2, "title": "Check consumer lag", "command": "kafka-consumer-groups --describe",
     "expected_outcome": "Lag identified"},
    {"rank": 1, "title": "Check pod CPU", "command": "kubectl top pods",
     "expected_outcome": "Hot pods found"},
    {"rank": 3, "title": "Scale out", "command": "kubectl scale deploy/ingestion-service",
     "expected_outcome": "CPU below threshold"}
  ]
}"""


class FakeMessage:
    def __init__(self, value: bytes, error: Any = None) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes:
        return self._value

    def error(self) -> Any:
        return self._error

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return 0


class FakeProducer:
    """In-memory stand-in for confluent_kafka.Producer; delivers on poll/flush.

    ``ack_delay`` models the broker round trip: a record's delivery callback
    only fires on a poll() at least that many seconds after produce().
    Thread-safe, like the real client (AuditPublisher polls from a thread).
    """

    def __init__(self, fail_delivery: bool = False, ack_delay: float = 0.0) -> None:
        self.fail_delivery = fail_delivery
        self.ack_delay = ack_delay
        self.sent: list[tuple[str, bytes, bytes]] = []
        self._pending: list[tuple[float, Any]] = []
        self._lock = threading.Lock()
        self.flushed = False

    def produce(self, topic: str, *, value: bytes, key: bytes, on_delivery: Any) -> None:
        with self._lock:
            self.sent.append((topic, value, key))
            self._pending.append((time.monotonic() + self.ack_delay, on_delivery))

    def poll(self, timeout: float, *, force: bool = False) -> int:
        now = time.monotonic()
        with self._lock:
            ready = [cb for at, cb in self._pending if force or at <= now]
            self._pending = [(at, cb) for at, cb in self._pending if not (force or at <= now)]
        for cb in ready:
            cb("broker down" if self.fail_delivery else None, None)
        return len(ready)

    def flush(self, timeout: float) -> int:
        self.flushed = True
        self.poll(timeout, force=True)
        return 0


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        model_version="test-v1",
        anomaly_threshold=0.8,
        scorer_min_samples=10,
        playbook_store_dir=str(tmp_path / "playbooks"),
        llm_provider="none",
        llm_backoff_seconds=0,
        incident_auto_diagnose=False,
    )


@pytest.fixture
def make_engine(settings: Settings) -> Callable[..., DiagnosticEngine]:
    def factory(provider: str = "none") -> DiagnosticEngine:
        llm = None
        if provider != "none":
            llm = LLMClient(provider, LLM_URL, "test-model", api_key="k", sleep=lambda _s: None)
        return DiagnosticEngine(settings, llm_client=llm)

    return factory


@pytest.fixture
def producer() -> FakeProducer:
    return FakeProducer()


@pytest.fixture
def audit(settings: Settings, producer: FakeProducer) -> AuditPublisher:
    return AuditPublisher(
        settings.kafka_bootstrap_servers, settings.anomaly_audit_topic, lambda _bs: producer
    )


@pytest.fixture
def client(settings: Settings, audit: AuditPublisher) -> Iterator[TestClient]:
    app = create_app(
        settings,
        scorer=AnomalyScorer(settings.model_version, min_samples=settings.scorer_min_samples),
        thresholds=ThresholdManager(settings.anomaly_threshold),
        audit=audit,
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_factory(
    settings: Settings, audit: AuditPublisher, make_engine: Callable[..., DiagnosticEngine]
) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(provider: str = "none") -> TestClient:
        c = TestClient(create_app(settings, audit=audit, engine=make_engine(provider)))
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def event(
    i: int, latency: float, *, source: str = "checkout", type_: str = "request"
) -> dict[str, Any]:
    return {
        "event_id": f"evt-{i}",
        "timestamp": "2026-10-20T12:00:00Z",
        "source": source,
        "type": type_,
        "component_id": source,
        "payload": {"latency_ms": latency, "ok": True},
        "schema_version": "1.0",
        "ingested_at": "2026-10-20T12:00:00.100Z",
    }
