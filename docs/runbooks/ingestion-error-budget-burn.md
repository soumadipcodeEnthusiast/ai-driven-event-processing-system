# IngestionErrorBudgetBurn

| | |
|---|---|
| **Alert** | `IngestionErrorBudgetBurn` |
| **Severity** | critical / warning |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/slo-burn-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Events ending in `outcome="error"` (unexpected failure after 3 retries → DLT) are burning the 99.9 % ingestion availability error budget. `critical` = fast burn (page), `warning` = slow burn (ticket).

## Symptoms

Red `error` band on *Ingestion throughput by outcome*; *Error budget remaining* for `ingestion-availability` drops on the SLO dashboard.

## Impact

Valid events are not normalised. Data used for forecasting has gaps.

## Diagnosis

```promql
slo:sli_error:ratio_rate5m{slo="ingestion-availability"}
slo:burn_rate:ratio_rate1h{slo="ingestion-availability"}
sum by (outcome) (rate(ingestion_events_total[5m]))
```
```bash
docker compose -f infra/docker-compose.yml logs --since=15m ingestion-service | grep -iE "error|exception" | head -50
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --describe --topic normalized-events
```
Typical causes: producer to `normalized-events` failing (Kafka broker issue, send timeout 30 s),
serialization bug introduced by a deploy, broker disk full.

## Mitigation

1. If it started with a deploy → roll back (`kubectl -n event-processor rollout undo deploy/ingestion-service`).
2. Kafka unhealthy → `docker compose -f infra/docker-compose.yml ps kafka`, check broker logs/disk, restart the broker.
3. Once fixed, replay failed events from `raw-events.DLT` (they carry the original bytes).
4. Budget exhausted → apply the error-budget policy in [SLO.md](../SLO.md#4-error-budget-policy).

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
