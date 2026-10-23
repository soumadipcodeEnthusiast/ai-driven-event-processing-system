"""
event_consumer.py — score every normalized event off Kafka (REQ-016).

REQ-016 asks for a score for *each processed event*, not only those posted to
``/score``. When ``EVENT_CONSUMER_ENABLED=true`` a background thread consumes
``normalized-events`` (consumer group ``diagnostic-anomaly-scorer``) and runs
each record through the same ``AnomalyDetector`` as the HTTP path.

Delivery: at-least-once. Offsets are stored only after an event has been
processed (``enable.auto.offset.store=false``) and committed by the client's
periodic auto-commit, so a crash re-scores at most one commit interval of
events. Malformed records are logged, counted and skipped — they were
already validated by ingestion-service, so retrying them can't help.

Scaling: any number of replicas can join the group (up to the topic's
partition count). Each replica learns baselines from the share of events it
sees; since ``normalized-events`` is keyed by ``event_id``, every replica
sees a uniform sample of every stream.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Protocol

from app.detector import AnomalyDetector
from app.metrics import CONSUMER_ERRORS

logger = logging.getLogger(__name__)


class Consumer(Protocol):
    def subscribe(self, topics: list[str]) -> None: ...
    def poll(self, timeout: float) -> Any: ...
    def store_offsets(self, message: Any) -> None: ...
    def close(self) -> None: ...


def _kafka_consumer(bootstrap_servers: str, group_id: str) -> Consumer:
    from confluent_kafka import Consumer as KafkaConsumer

    consumer: Consumer = KafkaConsumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "enable.auto.offset.store": False,
            "auto.commit.interval.ms": 5000,
        }
    )
    return consumer


class EventStreamScorer:
    def __init__(
        self,
        detector: AnomalyDetector,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        consumer: Consumer | None = None,
    ) -> None:
        self.detector = detector
        self.topic = topic
        self._consumer = consumer or _kafka_consumer(bootstrap_servers, group_id)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._consumer.subscribe([self.topic])
        self._thread = threading.Thread(target=self._run, name="event-consumer", daemon=True)
        self._thread.start()
        logger.info("consuming %s for anomaly scoring", self.topic)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._consumer.close()

    def handle(self, message: Any) -> bool:
        """Process one Kafka message; returns whether it was scored."""
        if message.error() is not None:
            CONSUMER_ERRORS.labels(reason="kafka").inc()
            logger.warning("consumer error: %s", message.error())
            return False
        try:
            event = json.loads(message.value())
            if not isinstance(event, dict):
                raise ValueError("event is not a JSON object")
            self.detector.process(event, source="kafka")
            return True
        except ValueError as exc:  # json.JSONDecodeError is a ValueError
            CONSUMER_ERRORS.labels(reason="malformed").inc()
            logger.warning(
                "skipping malformed record partition=%s offset=%s: %s",
                message.partition(),
                message.offset(),
                exc,
            )
            return False
        finally:
            # Stored even when skipped: a malformed record must not block the partition.
            self._consumer.store_offsets(message)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._consumer.poll(1.0)
                if message is not None:
                    self.handle(message)
            except Exception:
                CONSUMER_ERRORS.labels(reason="unexpected").inc()
                logger.exception("anomaly consumer iteration failed")
