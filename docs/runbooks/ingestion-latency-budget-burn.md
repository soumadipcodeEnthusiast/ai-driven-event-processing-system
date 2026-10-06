# IngestionLatencyBudgetBurn

| | |
|---|---|
| **Alert** | `IngestionLatencyBudgetBurn` |
| **Severity** | critical / warning |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/slo-burn-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Too many events take ≥ 500 ms to process (REQ-A latency SLO, 99 %).

## Symptoms

*Processing latency p95/p99* above the 500 ms line; consumer lag usually grows too.

## Impact

Events reach `normalized-events` late; REQ-A acceptance criterion at risk; forecasts see stale data.

## Diagnosis

```promql
service:ingestion_processing_seconds:p99_5m
slo:sli_error:ratio_rate5m{slo="ingestion-latency"}
sum(rate(ingestion_events_total[5m]))                  # load spike?
rate(jvm_gc_pause_seconds_sum{service="ingestion-service"}[5m])
```
```bash
docker compose -f infra/docker-compose.yml stats ingestion-service kafka
kubectl -n event-processor top pods -l app.kubernetes.io/name=ingestion-service
kubectl -n event-processor get hpa ingestion-service-hpa
```

## Mitigation

1. Load spike → scale out (`kubectl -n event-processor scale deploy/ingestion-service --replicas=3`; max useful
   replicas × concurrency = partition count, 3). Check the HPA isn't at max.
2. GC pressure → see [ingestion-jvm-heap-high](ingestion-jvm-heap-high.md).
3. Slow broker (produce latency) → investigate Kafka.
4. Regression from a deploy → roll back.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
