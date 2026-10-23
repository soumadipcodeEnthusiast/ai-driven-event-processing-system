# ADR-0009: Standalone Kafka → S3 archiver for REQ-028

## Status
Accepted (2026-10-20)

## Context
REQ-028 requires archiving raw and processed events in Amazon S3, with a
retention policy (REQ-029) and atomic writes (REQ-030). `storage/archive_client`
existed as a stub, with the open question of who calls it (ARCHITECTURE.md
§10.7): ingestion-service is closest to the data, but it is Java while the
client is Python, and it sits on the latency-sensitive synchronous path
(REQ-001/REQ-004: lag < 500 ms at 10k events/s).

Options considered:

1. **Archive inline in ingestion-service** (AWS SDK for Java, per batch).
   An S3 PUT costs tens to hundreds of milliseconds; doing it before acking
   would blow the REQ-004 budget, and doing it after acking risks losing the
   archive copy on a crash. An S3 outage would also stall ingestion.
2. **Kafka Connect S3 sink.** Production-grade, but adds a Connect cluster,
   its own config/ops surface and a JVM image, which is heavy for this stack
   and has no local equivalent in compose without more moving parts.
3. **A small dedicated consumer** (`event-archiver`) using the existing
   Python `ArchiveClient`, in its own consumer group.

## Decision
Option 3. `storage/archiver.py` consumes `raw-events` and `normalized-events`
in group `event-archiver`, batches per (topic, partition) up to
`ARCHIVE_BATCH_SIZE` records or `ARCHIVE_FLUSH_SECONDS`, writes each batch as
one gzipped NDJSON object, and commits that partition's offset only after
the PUT succeeds. On a failed PUT it rewinds the partition to the batch's
first offset. Object keys are `<topic>-p<partition>-o<first offset>`, so a
replayed batch overwrites its own object (no duplicates; S3 versioning keeps
the old version until the lifecycle rule expires it).

Retention is a bucket lifecycle rule (Terraform `s3.tf`, mirrored by
`minio-init` locally), not code. On EKS the archiver authenticates with IRSA
using a write-only policy on `<bucket>/events/*`.

## Consequences
- \+ Ingestion latency and availability are independent of S3.
- \+ At-least-once to S3 with no duplicate objects; no extra infrastructure.
- \+ Hive-style `dt=/hour=` partitions are directly queryable by Athena/Glue.
- \+ Same code against MinIO (local) and S3 (AWS): only `S3_ENDPOINT_URL` differs.
- − The archive lags the topics by up to `ARCHIVE_FLUSH_SECONDS`; if the
  archiver is down longer than Kafka retention, events are missing from the
  archive (`EventArchiveStalled` alerts well before that).
- − Batches are cut by size *or* time, so a replayed batch can be larger than
  the original (it overwrites its own key with a superset) and then overlap
  the next, already-written object. Consumers of the archive should
  de-duplicate on `(topic, partition, offset)`, which every line carries.
- − Scaling is bounded by the partition count (6 per topic), the same as ingestion.
