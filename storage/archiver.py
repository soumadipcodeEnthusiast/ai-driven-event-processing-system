"""
archiver.py — Kafka → S3 event archiver (SRS v1.0 §4.6, REQ-028).

Consumes ``raw-events`` and ``normalized-events`` (``ARCHIVE_TOPICS``) in
consumer group ``event-archiver`` and writes them to S3 via ``ArchiveClient``
as gzipped NDJSON, one object per (topic, partition) batch. It runs as its
own deployment so archival never adds latency to, or fails, the ingestion
hot path (docs/adr/0009).

Delivery: at-least-once, no loss. A partition's offsets are committed only
after its batch object is in S3. If a write still fails after the client's
retries, the batch is dropped from memory and the partition is rewound to
the batch's first offset, so the records are re-read and re-archived later;
the batch key is deterministic, so the rewrite replaces rather than
duplicates (REQ-030).

A batch is flushed when it reaches ``ARCHIVE_BATCH_SIZE`` records or is
``ARCHIVE_FLUSH_SECONDS`` old, and on partition revocation and shutdown.

Each archived line::

    {"topic", "partition", "offset", "key", "kafka_timestamp_ms", "archived_at",
     "value" (parsed JSON) | "value_text" (non-JSON bytes, e.g. a malformed raw event)}

Run: ``python -m storage.archiver`` (Prometheus metrics on ``METRICS_PORT``, default 8083).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from storage.archive_client import ArchiveClient
from storage.archive_client.client import ArchiveConfig

logger = logging.getLogger("storage.archiver")

ARCHIVED_RECORDS = Counter(
    "archive_records_total", "Records written to the S3 archive (REQ-028).", ["topic"]
)
ARCHIVED_OBJECTS = Counter(
    "archive_objects_total", "Objects (batches) written to the S3 archive.", ["topic"]
)
ARCHIVE_FAILURES = Counter(
    "archive_failures_total",
    "Batches that failed to reach S3 (records are rewound and retried).",
    ["topic"],
)
ARCHIVE_FLUSH_SECONDS = Histogram(
    "archive_flush_seconds",
    "Latency of one batch upload to S3.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
ARCHIVE_LAST_SUCCESS = Gauge(
    "archive_last_success_timestamp_seconds",
    "Unix time of the last successful batch upload.",
)


@dataclass(frozen=True)
class ArchiverSettings:
    bootstrap_servers: str
    topics: list[str]
    group_id: str = "event-archiver"
    batch_size: int = 1000
    flush_seconds: float = 60.0
    metrics_port: int = 8083

    @classmethod
    def from_env(cls) -> ArchiverSettings:
        return cls(
            bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
            topics=[
                t.strip()
                for t in os.environ.get("ARCHIVE_TOPICS", "raw-events,normalized-events").split(",")
                if t.strip()
            ],
            group_id=os.environ.get("ARCHIVE_GROUP_ID", "event-archiver"),
            batch_size=int(os.environ.get("ARCHIVE_BATCH_SIZE", "1000")),
            flush_seconds=float(os.environ.get("ARCHIVE_FLUSH_SECONDS", "60")),
            metrics_port=int(os.environ.get("METRICS_PORT", "8083")),
        )


@dataclass
class Batch:
    topic: str
    partition: int
    first_offset: int
    started: float = field(default_factory=time.monotonic)
    records: list[dict[str, Any]] = field(default_factory=list)
    last_offset: int = -1


def to_record(message: Any) -> dict[str, Any]:
    raw: bytes | None = message.value()
    key: bytes | None = message.key()
    _ts_type, ts_ms = message.timestamp()
    record: dict[str, Any] = {
        "topic": message.topic(),
        "partition": message.partition(),
        "offset": message.offset(),
        "key": key.decode("utf-8", "replace") if key is not None else None,
        "kafka_timestamp_ms": ts_ms if ts_ms and ts_ms > 0 else None,
        "archived_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        record["value"] = json.loads(raw) if raw is not None else None
    except ValueError:
        record["value_text"] = raw.decode("utf-8", "replace") if raw is not None else None
    return record


class Archiver:
    def __init__(self, settings: ArchiverSettings, client: ArchiveClient, consumer: Any) -> None:
        self.settings = settings
        self.client = client
        self.consumer = consumer
        self.batches: dict[tuple[str, int], Batch] = {}
        self._running = True

    # ── consumer callbacks ─────────────────────────────────────────────────

    def on_revoke(self, _consumer: Any, partitions: list[Any]) -> None:
        for tp in partitions:
            batch = self.batches.get((tp.topic, tp.partition))
            if batch is not None:
                self.flush(batch)

    # ── core ───────────────────────────────────────────────────────────────

    def add(self, message: Any) -> None:
        key = (message.topic(), message.partition())
        batch = self.batches.get(key)
        if batch is None:
            batch = Batch(message.topic(), message.partition(), message.offset())
            self.batches[key] = batch
        batch.records.append(to_record(message))
        batch.last_offset = message.offset()
        if len(batch.records) >= self.settings.batch_size:
            self.flush(batch)

    def flush_due(self, force: bool = False) -> None:
        now = time.monotonic()
        for batch in list(self.batches.values()):
            if force or now - batch.started >= self.settings.flush_seconds:
                self.flush(batch)

    def flush(self, batch: Batch) -> bool:
        from confluent_kafka import TopicPartition

        self.batches.pop((batch.topic, batch.partition), None)
        if not batch.records:
            return True
        batch_id = f"{batch.topic}-p{batch.partition}-o{batch.first_offset:020d}"
        start = time.perf_counter()
        try:
            key = self.client.archive_batch(batch.records, batch.topic, batch_id)
        except Exception as exc:
            ARCHIVE_FAILURES.labels(topic=batch.topic).inc()
            logger.error(
                "event=archive_failed topic=%s partition=%d offsets=%d-%d error=%s; rewinding",
                batch.topic,
                batch.partition,
                batch.first_offset,
                batch.last_offset,
                exc,
            )
            try:
                self.consumer.seek(TopicPartition(batch.topic, batch.partition, batch.first_offset))
            except Exception:  # partition revoked meanwhile: its new owner resumes from commit
                logger.warning("could not rewind %s-%d", batch.topic, batch.partition)
            return False
        finally:
            ARCHIVE_FLUSH_SECONDS.observe(time.perf_counter() - start)

        self.consumer.commit(
            offsets=[TopicPartition(batch.topic, batch.partition, batch.last_offset + 1)],
            asynchronous=False,
        )
        ARCHIVED_RECORDS.labels(topic=batch.topic).inc(len(batch.records))
        ARCHIVED_OBJECTS.labels(topic=batch.topic).inc()
        ARCHIVE_LAST_SUCCESS.set(time.time())
        logger.info(
            "event=archived topic=%s partition=%d records=%d key=%s",
            batch.topic,
            batch.partition,
            len(batch.records),
            key,
        )
        return True

    def stop(self, *_: Any) -> None:
        self._running = False

    def run(self) -> None:
        self.consumer.subscribe(self.settings.topics, on_revoke=self.on_revoke)
        logger.info(
            "archiving %s → s3://%s/%s",
            ",".join(self.settings.topics),
            self.client.config.bucket,
            self.client.config.prefix,
        )
        try:
            while self._running:
                message = self.consumer.poll(1.0)
                if message is not None:
                    if message.error() is not None:
                        logger.warning("consumer error: %s", message.error())
                    else:
                        self.add(message)
                self.flush_due()
            self.flush_due(force=True)
        finally:
            self.consumer.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    from confluent_kafka import Consumer

    settings = ArchiverSettings.from_env()
    client = ArchiveClient(ArchiveConfig.from_env())
    consumer = Consumer(
        {
            "bootstrap.servers": settings.bootstrap_servers,
            "group.id": settings.group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            # A batch may sit for flush_seconds plus an upload with retries.
            "max.poll.interval.ms": int(max(300.0, settings.flush_seconds * 3) * 1000),
        }
    )
    archiver = Archiver(settings, client, consumer)
    signal.signal(signal.SIGTERM, archiver.stop)
    signal.signal(signal.SIGINT, archiver.stop)
    start_http_server(settings.metrics_port)
    archiver.run()


if __name__ == "__main__":
    main()
