# IngestionConsumerStalled

| | |
|---|---|
| **Alert** | `IngestionConsumerStalled` |
| **Severity** | critical |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

The consumer has lag but processed zero events for 5 minutes.

## Symptoms

*Ingestion throughput* flat at 0 while *Kafka consumer lag* > 0 and rising.

## Impact

Ingestion is fully stopped. No new data reaches forecasting.

## Diagnosis

```promql
sum(rate(ingestion_events_total[5m]))
service:kafka_consumer_records_lag:max
up{job="ingestion-service"}
```
```bash
docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group ingestion-consumer-group     # any members? CONSUMER-ID empty = no consumer
docker compose -f infra/docker-compose.yml logs --tail=300 ingestion-service
# thread dump (stuck listener?)
curl -s localhost:8080/actuator/threaddump | jq '.threads[] | select(.threadName|test("kafka|listener"))'
```
Likely causes: a poison message that is retried forever, listener thread blocked on a send to
`normalized-events` / `raw-events.DLT`, lost broker connection.

## Mitigation

1. `docker compose -f infra/docker-compose.yml restart ingestion-service` (k8s: `kubectl -n event-processor rollout restart deploy/ingestion-service`).
2. Poison message: the offset in `kafka-consumer-groups --describe` does not move — inspect that
   record; if it is invalid but not reaching the DLT, that is a bug: escalate to the Data Engineer.
   Last resort: `kafka-consumer-groups --reset-offsets --to-offset <n+1> --execute` (data loss for
   one event; record it in the incident).
3. Broker connectivity: fix Kafka first.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
