"""
audit_publisher.py — anomaly audit trail (SRS v1.0 §4.4, REQ-019).

REQ-019: record all triggered anomalies in a dedicated audit topic.

Publishes to the Kafka topic ``anomaly-audit`` (distinct from ``raw-events`` /
``normalized-events``), keyed by ``event_id``, so anomaly audit records are
retained independently of the primary event flow. Message schema:
``contracts/anomaly-audit.schema.json`` (docs/CONTRACTS.md §1.4).

Delivery: the producer is idempotent with ``acks=all``, so a record the
broker acknowledges is durable. ``publish_anomaly`` never raises into the
scoring path — a broker outage must not stop detection — but every failure
(enqueue or delivery) increments ``anomaly_audit_publish_failures_total``
and is logged with the event id, so it alerts (docs/runbooks/
anomaly-audit-publish-failing.md).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.metrics import ANOMALY_AUDIT_PUBLISH_FAILURES, ANOMALY_AUDIT_PUBLISHED

logger = logging.getLogger(__name__)

AUDIT_SCHEMA_VERSION = "1.0"


class Producer(Protocol):
    """The subset of ``confluent_kafka.Producer`` this module uses."""

    # Mirrors confluent_kafka: the 4th positional parameter is `partition`, so
    # value/key/on_delivery must be passed by keyword.
    def produce(self, topic: str, *, value: bytes, key: bytes, on_delivery: Any) -> None: ...
    def poll(self, timeout: float) -> int: ...
    def flush(self, timeout: float) -> int: ...


def _kafka_producer(bootstrap_servers: str) -> Producer:
    from confluent_kafka import Producer as KafkaProducer  # lazy: keep imports cheap in tests

    producer: Producer = KafkaProducer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "diagnostic-service-audit",
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 5,
            "message.timeout.ms": 30_000,
        }
    )
    return producer


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_audit_record(
    event: dict[str, Any],
    score: float,
    *,
    threshold: float | None,
    model_version: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "audit_id": str(uuid.uuid4()),
        "event_id": str(event.get("event_id") or ""),
        "component_id": event.get("component_id"),
        "source": event.get("source"),
        "type": event.get("type"),
        "event_timestamp": event.get("timestamp"),
        "score": score,
        "threshold": threshold,
        "model_version": model_version,
        "reason": reason,
        "detected_at": _utc_now(),
        "schema_version": AUDIT_SCHEMA_VERSION,
    }


class AuditPublisher:
    """Publishes anomaly events to the Kafka audit topic."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        producer_factory: Callable[[str], Producer] = _kafka_producer,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self._factory = producer_factory
        self._producer: Producer | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None

    def _get_producer(self) -> Producer:
        # Created on first use so the service starts (and /health passes)
        # even while Kafka is still coming up.
        with self._lock:
            if self._producer is None:
                self._producer = self._factory(self.bootstrap_servers)
                # Delivery callbacks only run inside poll(); serve them in the
                # background so the published/failed counters stay current even
                # when anomalies are rare.
                self._poller = threading.Thread(
                    target=self._poll_loop, args=(self._producer,), name="audit-poller", daemon=True
                )
                self._poller.start()
            return self._producer

    def _poll_loop(self, producer: Producer) -> None:
        while not self._stop.wait(0.2):
            try:
                producer.poll(0)
            except Exception:  # - keep serving callbacks
                logger.exception("audit producer poll failed")

    def publish_anomaly(
        self,
        event: dict[str, Any],
        score: float,
        *,
        threshold: float | None = None,
        model_version: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Publish an anomaly audit record for ``event`` scored at ``score`` (REQ-019).

        Returns whether the record was handed to the producer; delivery is
        confirmed asynchronously (failures are counted in the delivery callback).
        """
        record = build_audit_record(
            event, score, threshold=threshold, model_version=model_version, reason=reason
        )
        event_id = record["event_id"]

        def on_delivery(err: Any, _msg: Any) -> None:
            if err is not None:
                ANOMALY_AUDIT_PUBLISH_FAILURES.inc()
                logger.error("event=anomaly_audit_failed event_id=%s error=%s", event_id, err)
            else:
                ANOMALY_AUDIT_PUBLISHED.inc()

        try:
            producer = self._get_producer()
            payload = json.dumps(record, separators=(",", ":")).encode()
            try:
                producer.produce(
                    self.topic, value=payload, key=event_id.encode(), on_delivery=on_delivery
                )
            except BufferError:
                # Local queue full: serve delivery callbacks to drain it, retry once.
                producer.poll(1.0)
                producer.produce(
                    self.topic, value=payload, key=event_id.encode(), on_delivery=on_delivery
                )
            producer.poll(0)
        except Exception as exc:  # - never break scoring on an audit failure
            ANOMALY_AUDIT_PUBLISH_FAILURES.inc()
            logger.error("event=anomaly_audit_failed event_id=%s error=%s", event_id, exc)
            return False
        return True

    def close(self, timeout: float = 10.0) -> None:
        """Flush outstanding records and release the producer."""
        with self._lock:
            producer, self._producer = self._producer, None
            poller, self._poller = self._poller, None
        if producer is None:
            return
        self._stop.set()
        if poller is not None:
            poller.join(timeout=1.0)
        remaining = producer.flush(timeout)
        if remaining:
            ANOMALY_AUDIT_PUBLISH_FAILURES.inc(remaining)
            logger.error("event=anomaly_audit_unflushed remaining=%d", remaining)
