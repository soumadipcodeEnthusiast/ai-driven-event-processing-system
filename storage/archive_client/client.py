"""
client.py — S3 archival client (SRS v1.0 §4.6 Data Persistence, REQ-028 to REQ-030).

REQ-028: the system shall archive raw and processed events in Amazon S3.
REQ-029: retention is enforced by the bucket's lifecycle rules (Terraform:
         infra/terraform/s3.tf; MinIO: infra/docker-compose.yml minio-init),
         not by this client, so the policy lives in one place.
REQ-030: every write is one S3 ``PutObject`` — atomic: readers see the whole
         object or nothing. Keys are deterministic for a given Kafka batch
         (topic/partition/first offset), so a retried or replayed batch
         overwrites its own object instead of creating a duplicate.

Bucket name and region come from environment variables, never hardcoded, so
the same code runs against real S3 in production and against the local
MinIO stand-in (infra/docker-compose.yml) in dev — MinIO speaks the S3 API,
so no separate client is needed, only a different endpoint_url.

Credentials follow the standard AWS chain (env vars, ~/.aws, IRSA web
identity on EKS, instance profile); nothing here reads secrets directly.

Object layout (Hive-style partitions, queryable by Athena/Glue as-is)::

    s3://<bucket>/<prefix>/<topic>/dt=YYYY-MM-DD/hour=HH/<topic>-p<partition>-o<offset>.jsonl.gz
"""

from __future__ import annotations

import gzip
import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ArchiveConfig:
    bucket: str
    region: str
    endpoint_url: str | None  # set for MinIO/local; None selects real AWS S3
    prefix: str = "events"
    # Optional SSE-KMS key; when unset the bucket's default encryption applies.
    kms_key_id: str | None = None

    @classmethod
    def from_env(cls) -> ArchiveConfig:
        bucket = os.environ.get("S3_ARCHIVE_BUCKET", "")
        region = os.environ.get("S3_ARCHIVE_REGION", "") or os.environ.get("AWS_REGION", "")
        if not bucket or not region:
            raise ValueError("S3_ARCHIVE_BUCKET and S3_ARCHIVE_REGION must be set")
        return cls(
            bucket=bucket,
            region=region,
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            prefix=os.environ.get("S3_ARCHIVE_PREFIX", "events").strip("/"),
            kms_key_id=os.environ.get("S3_ARCHIVE_KMS_KEY_ID") or None,
        )


def _s3_client(config: ArchiveConfig) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=config.region,
        endpoint_url=config.endpoint_url,
        config=Config(
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            # MinIO needs path-style addressing; real S3 is fine with either.
            s3={"addressing_style": "path" if config.endpoint_url else "auto"},
        ),
    )


def encode_ndjson_gz(records: Iterable[dict[str, Any]]) -> bytes:
    lines = (json.dumps(r, separators=(",", ":"), default=str) for r in records)
    return gzip.compress(("\n".join(lines) + "\n").encode(), compresslevel=6)


class ArchiveClient:
    """Archives records to S3 (or an S3-compatible store, e.g. MinIO)."""

    def __init__(self, config: ArchiveConfig, s3: Any = None) -> None:
        self.config = config
        self._s3 = s3 if s3 is not None else _s3_client(config)

    def object_key(
        self, dataset: str, when: datetime, name: str, suffix: str = ".jsonl.gz"
    ) -> str:
        when = when.astimezone(timezone.utc)
        parts = [
            self.config.prefix,
            dataset,
            f"dt={when:%Y-%m-%d}",
            f"hour={when:%H}",
            f"{name}{suffix}",
        ]
        return "/".join(p for p in parts if p)

    def _put(self, key: str, body: bytes, content_type: str, metadata: dict[str, str]) -> None:
        extra: dict[str, Any] = {}
        if self.config.kms_key_id:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.config.kms_key_id}
        self._s3.put_object(
            Bucket=self.config.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ContentEncoding="gzip" if key.endswith(".gz") else "identity",
            Metadata=metadata,
            **extra,
        )

    def archive(self, record: dict[str, Any], dataset: str = "records") -> str:
        """Persist one ``record`` as its own object (REQ-028); returns the S3 key.

        For streams use ``archive_batch`` — one object per record is fine for
        occasional writes (e.g. a stored prediction), not for event volume.
        """
        when = datetime.now(timezone.utc)
        name = str(record.get("event_id") or record.get("id") or uuid.uuid4())
        key = self.object_key(dataset, when, name, ".json")
        body = json.dumps(record, separators=(",", ":"), default=str).encode()
        self._put(key, body, "application/json", {"records": "1"})
        return key

    def archive_batch(
        self,
        records: list[dict[str, Any]],
        dataset: str,
        batch_id: str,
        when: datetime | None = None,
    ) -> str:
        """Write ``records`` as one gzipped NDJSON object (REQ-028, REQ-030); returns the key.

        ``batch_id`` must be deterministic for the batch (e.g. partition + first
        offset) so a retry overwrites rather than duplicates.
        """
        if not records:
            raise ValueError("refusing to archive an empty batch")
        key = self.object_key(dataset, when or datetime.now(timezone.utc), batch_id)
        self._put(
            key,
            encode_ndjson_gz(records),
            "application/x-ndjson",
            {"records": str(len(records))},
        )
        return key
