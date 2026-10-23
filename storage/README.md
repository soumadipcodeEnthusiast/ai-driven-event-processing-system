# storage — event-archiver (REQ-028 to REQ-030)

Archives raw and processed events to Amazon S3 (SRS v1.0 §4.6 Data Persistence).

- `archive_client/` — `ArchiveClient`: S3 (or MinIO) writes via boto3.
  `archive_batch(records, dataset, batch_id)` writes one gzipped NDJSON
  object; `archive(record)` writes a single JSON object.
- `archiver.py` — the `event-archiver` service: a Kafka consumer (group
  `event-archiver`) over `raw-events` and `normalized-events` that batches
  per partition and uploads with `ArchiveClient`. See docs/adr/0009.

## Guarantees

| | |
|---|---|
| No loss (at-least-once) | Offsets are committed only after the batch object is in S3. A failed upload rewinds the partition to the batch start. |
| Atomic (REQ-030) | One `PutObject` per batch: readers see the whole object or nothing. |
| No duplicates on replay | Keys are deterministic: `<topic>-p<partition>-o<first offset>`. A replayed batch overwrites its own object. |
| Retention (REQ-029) | Bucket lifecycle rule (Terraform `infra/terraform/s3.tf`; MinIO: `minio-init`), not code. |
| Isolation | Separate deployment, so S3 latency or outages never slow or fail ingestion. |

## Layout

```
s3://<bucket>/events/<topic>/dt=YYYY-MM-DD/hour=HH/<topic>-p<partition>-o<offset:020d>.jsonl.gz
```

Each line: `{"topic", "partition", "offset", "key", "kafka_timestamp_ms", "archived_at", "value" | "value_text"}`.
Hive-style `dt=`/`hour=` partitions can be queried by Athena/Glue as-is.

## Configuration

| Variable | Default | |
|---|---|---|
| `S3_ARCHIVE_BUCKET` / `S3_ARCHIVE_REGION` | — (required) | Target bucket |
| `S3_ARCHIVE_PREFIX` | `events` | Key prefix (must match the IAM policy in `s3.tf`) |
| `S3_ENDPOINT_URL` | unset | MinIO URL locally; unset/empty = real AWS S3 |
| `S3_ARCHIVE_KMS_KEY_ID` | unset | SSE-KMS key; unset = bucket default encryption |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | |
| `ARCHIVE_TOPICS` | `raw-events,normalized-events` | |
| `ARCHIVE_BATCH_SIZE` / `ARCHIVE_FLUSH_SECONDS` | `1000` / `60` | Flush when either is reached |
| `METRICS_PORT` | `8083` | Prometheus `archive_*` metrics |

AWS credentials come from the standard chain: IRSA on EKS (role from
`terraform output archiver_role_arn`), or env vars / `~/.aws` elsewhere. To
use your real bucket from the local stack, see the README section
"AWS: attaching your S3 bucket" (`infra/docker-compose.aws.yml`).

## Run / test

```bash
cd storage
pip install -r requirements-dev.txt
pytest && ruff check . && mypy archive_client archiver.py
# run against the compose Kafka + MinIO:
cd .. && KAFKA_BOOTSTRAP_SERVERS=localhost:9092 S3_ARCHIVE_BUCKET=event-archive S3_ARCHIVE_REGION=us-east-1 \
  S3_ENDPOINT_URL=http://localhost:9000 AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
  python -m storage.archiver
```
