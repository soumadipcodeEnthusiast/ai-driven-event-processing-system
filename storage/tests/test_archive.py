"""S3 archival (REQ-028 to REQ-030): ArchiveClient and the Kafka → S3 archiver."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from storage.archive_client import ArchiveClient
from storage.archive_client.client import ArchiveConfig
from storage.archiver import Archiver, ArchiverSettings, to_record


class FakeS3:
    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.objects: dict[str, dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("S3 unavailable")
        self.objects[kwargs["Key"]] = kwargs
        return {}


class FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int, value: bytes | None) -> None:
        self._t, self._p, self._o, self._v = topic, partition, offset, value

    def topic(self) -> str:
        return self._t

    def partition(self) -> int:
        return self._p

    def offset(self) -> int:
        return self._o

    def value(self) -> bytes | None:
        return self._v

    def key(self) -> bytes | None:
        return f"k{self._o}".encode()

    def timestamp(self) -> tuple[int, int]:
        return (1, 1_760_000_000_000)

    def error(self) -> None:
        return None


class FakeConsumer:
    def __init__(self) -> None:
        self.commits: list[tuple[str, int, int]] = []
        self.seeks: list[tuple[str, int, int]] = []

    def commit(self, offsets: list[Any], asynchronous: bool) -> None:
        self.commits += [(tp.topic, tp.partition, tp.offset) for tp in offsets]

    def seek(self, tp: Any) -> None:
        self.seeks.append((tp.topic, tp.partition, tp.offset))


CONFIG = ArchiveConfig(bucket="event-archive", region="ap-south-1", endpoint_url=None)


def read_object(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in gzip.decompress(obj["Body"]).decode().splitlines()]


# ── ArchiveClient ─────────────────────────────────────────────────────────────


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ARCHIVE_BUCKET", "my-bucket")
    monkeypatch.setenv("S3_ARCHIVE_REGION", "ap-south-1")
    monkeypatch.setenv("S3_ARCHIVE_PREFIX", "/archive/")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    cfg = ArchiveConfig.from_env()
    assert (cfg.bucket, cfg.region, cfg.endpoint_url, cfg.prefix) == (
        "my-bucket",
        "ap-south-1",
        None,
        "archive",
    )


def test_config_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_ARCHIVE_BUCKET", raising=False)
    with pytest.raises(ValueError):
        ArchiveConfig.from_env()


def test_archive_single_record() -> None:
    s3 = FakeS3()
    key = ArchiveClient(CONFIG, s3).archive({"event_id": "e1", "x": 1}, dataset="predictions")
    assert key.startswith("events/predictions/dt=")
    assert key.endswith("/e1.json")
    obj = s3.objects[key]
    assert obj["Bucket"] == "event-archive"
    assert json.loads(obj["Body"]) == {"event_id": "e1", "x": 1}
    assert "ServerSideEncryption" not in obj  # bucket default encryption applies


def test_archive_batch_layout_and_kms() -> None:
    s3 = FakeS3()
    cfg = ArchiveConfig("b", "ap-south-1", None, kms_key_id="alias/archive")
    when = datetime(2026, 10, 20, 13, 5, tzinfo=timezone.utc)
    key = ArchiveClient(cfg, s3).archive_batch([{"a": 1}, {"a": 2}], "raw-events", "id1", when)
    assert key == "events/raw-events/dt=2026-10-20/hour=13/id1.jsonl.gz"
    obj = s3.objects[key]
    assert read_object(obj) == [{"a": 1}, {"a": 2}]
    assert obj["Metadata"] == {"records": "2"}
    assert obj["SSEKMSKeyId"] == "alias/archive"


def test_archive_batch_rejects_empty() -> None:
    with pytest.raises(ValueError):
        ArchiveClient(CONFIG, FakeS3()).archive_batch([], "raw-events", "x")


# ── Archiver ─────────────────────────────────────────────────────────────────


def make_archiver(s3: FakeS3, batch_size: int = 3) -> tuple[Archiver, FakeConsumer]:
    settings = ArchiverSettings("bs", ["raw-events", "normalized-events"], batch_size=batch_size)
    consumer = FakeConsumer()
    return Archiver(settings, ArchiveClient(CONFIG, s3), consumer), consumer


def test_to_record_keeps_invalid_raw_bytes() -> None:
    ok = to_record(FakeMessage("raw-events", 0, 5, b'{"event_id":"e"}'))
    bad = to_record(FakeMessage("raw-events", 0, 6, b"{not json"))
    assert ok["value"] == {"event_id": "e"} and ok["offset"] == 5 and ok["key"] == "k5"
    assert bad["value_text"] == "{not json" and "value" not in bad


def test_batch_flushes_at_size_and_commits_after_upload() -> None:
    s3 = FakeS3()
    archiver, consumer = make_archiver(s3, batch_size=3)
    for off in range(10, 13):
        archiver.add(FakeMessage("raw-events", 2, off, b'{"n":%d}' % off))
    (key,) = s3.objects
    assert key.endswith("raw-events-p2-o00000000000000000010.jsonl.gz")
    assert [r["value"]["n"] for r in read_object(s3.objects[key])] == [10, 11, 12]
    assert consumer.commits == [("raw-events", 2, 13)]  # next offset to read
    assert archiver.batches == {}


def test_partitions_batch_separately_and_force_flush() -> None:
    s3 = FakeS3()
    archiver, consumer = make_archiver(s3, batch_size=100)
    archiver.add(FakeMessage("raw-events", 0, 1, b"{}"))
    archiver.add(FakeMessage("normalized-events", 0, 7, b"{}"))
    archiver.add(FakeMessage("raw-events", 1, 3, b"{}"))
    assert s3.objects == {}
    archiver.flush_due(force=True)
    assert len(s3.objects) == 3
    assert sorted(consumer.commits) == [
        ("normalized-events", 0, 8),
        ("raw-events", 0, 2),
        ("raw-events", 1, 4),
    ]


def test_failed_upload_rewinds_without_commit() -> None:
    s3 = FakeS3(fail=1)
    archiver, consumer = make_archiver(s3, batch_size=2)
    archiver.add(FakeMessage("raw-events", 0, 40, b"{}"))
    archiver.add(FakeMessage("raw-events", 0, 41, b"{}"))
    assert consumer.commits == []
    assert consumer.seeks == [("raw-events", 0, 40)]
    # re-delivered records produce the same deterministic key → no duplicate object
    archiver.add(FakeMessage("raw-events", 0, 40, b"{}"))
    archiver.add(FakeMessage("raw-events", 0, 41, b"{}"))
    assert len(s3.objects) == 1
    assert consumer.commits == [("raw-events", 0, 42)]


def test_revoke_flushes_owned_partitions() -> None:
    s3 = FakeS3()
    archiver, consumer = make_archiver(s3, batch_size=100)
    archiver.add(FakeMessage("raw-events", 4, 9, b"{}"))

    class TP:
        topic, partition = "raw-events", 4

    archiver.on_revoke(None, [TP()])
    assert consumer.commits == [("raw-events", 4, 10)]
