# EventArchiveUploadFailing / EventArchiveStalled

| | |
|---|---|
| **Alerts** | `EventArchiveUploadFailing` (warning), `EventArchiveStalled` (critical) |
| **Service** | event-archiver (`storage/archiver.py`) |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [Service Health → Archival](http://localhost:9030/d/aiops-service-health) |

event-archiver can't write raw/normalized event batches to the S3 archive bucket (REQ-028).

## Symptoms

- `archive_failures_total` increasing (`EventArchiveUploadFailing`).
- `archive_last_success_timestamp_seconds` more than 15 min old while ingestion is processing events (`EventArchiveStalled`).
- Logs: `event=archive_failed topic=... partition=... offsets=A-B error=...; rewinding`.

## Impact

No data loss yet: offsets are committed only after a batch object is in S3, and a failed batch is rewound and re-read. The archive falls behind, and Kafka retention is the deadline: records that age out of `raw-events`/`normalized-events` before they are archived are lost from the archive.

## Diagnosis

```promql
service:archive_failures:increase15m
time() - max(archive_last_success_timestamp_seconds)
topic:archive_records:rate5m
```
```bash
docker compose -f infra/docker-compose.yml logs --since=30m event-archiver | grep -E 'archive_failed|consumer error'
kubectl -n event-processor logs deploy/event-archiver --since=30m | grep archive_failed
```

Match the `error=` text:

| Error | Likely cause |
|---|---|
| `AccessDenied` | IAM: the role (IRSA `archiver_role_arn`) or keys lack `s3:PutObject` on `<bucket>/events/*`; with SSE-KMS also `kms:GenerateDataKey`. |
| `NoSuchBucket` | `S3_ARCHIVE_BUCKET` wrong or bucket deleted. |
| `InvalidAccessKeyId` / `ExpiredToken` | Static keys rotated, or exported session credentials expired (compose + `docker-compose.aws.yml`). |
| `Could not connect to the endpoint URL` | Network/NAT path to S3 (EKS private subnets need a NAT gateway or an S3 VPC endpoint); locally, MinIO down. |
| no `archive_failed`, but stalled | Consumer not receiving: check Kafka (`ServiceDown`), and the group `event-archiver` lag. |

```bash
# Check the identity and bucket access from the archiver's point of view (AWS):
kubectl -n event-processor exec deploy/event-archiver -- python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
# Consumer-group lag:
docker compose -f infra/docker-compose.yml exec kafka kafka-consumer-groups --bootstrap-server kafka:29092 --describe --group event-archiver
```

## Mitigation

1. Fix the cause above (IAM policy / bucket name / credentials / network). The archiver retries on its own; no restart needed once S3 accepts writes.
2. If the backlog approaches Kafka retention, temporarily raise topic retention (`kafka-configs --alter --entity-type topics --entity-name raw-events --add-config retention.ms=...`) and scale the archiver up to the partition count.
3. Duplicates aren't a concern: batch keys are deterministic (`<topic>-p<partition>-o<first offset>`), so replays overwrite.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. `EventArchiveStalled` not acknowledged within 15 min → DevOps Engineer on-call.
