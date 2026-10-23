# AnomalyAuditPublishFailing

| | |
|---|---|
| **Alert** | `AnomalyAuditPublishFailing` |
| **Severity** | warning |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [Service Health](http://localhost:9030/d/aiops-service-health) · [SLOs](http://localhost:9030/d/aiops-slo) |

diagnostic-service failed to publish one or more anomaly records to the Kafka audit topic (REQ-019).

## Symptoms

`anomaly_audit_publish_failures_total` increasing; "Audit-publish failures" panel on Service Health is non-zero.

## Impact

Anomalies that crossed the detection threshold are not recorded to the `anomaly-audit` topic (REQ-019). Detection itself keeps working — `AuditPublisher` never raises into the scoring path, so `/score/{event_id}` still answers (with `"audited": false` when the record couldn't even be enqueued) and the `normalized-events` consumer keeps scoring. Every failed record is logged as `event=anomaly_audit_failed event_id=...`, so the gap can be backfilled from the logs.

## Diagnosis

```promql
service:anomaly_audit_publish_failures:increase30m
```
```bash
docker compose -f infra/docker-compose.yml logs --since=30m diagnostic-service | grep anomaly_audit_failed
docker compose -f infra/docker-compose.yml exec diagnostic-service env | grep -i KAFKA
```

Check Kafka reachability from diagnostic-service (`KAFKA_BOOTSTRAP_SERVERS`) and whether the `anomaly-audit` topic exists.

## Mitigation

1. Confirm Kafka is up (`ServiceDown`/broker health) and the `anomaly-audit` topic exists.
2. Check `ANOMALY_AUDIT_TOPIC` / `KAFKA_BOOTSTRAP_SERVERS` are set correctly on diagnostic-service.
3. Restart diagnostic-service if the producer connection appears stuck.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the DevOps Engineer on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
