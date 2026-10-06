# IngestionInvalidRatioHigh

| | |
|---|---|
| **Alert** | `IngestionInvalidRatioHigh` |
| **Severity** | warning |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

More than 5 % of raw events fail validation and are routed to `raw-events.DLT` (REQ-B).

## Symptoms

*Ingestion throughput by outcome* shows a large yellow `invalid` band; DLT topic grows.

## Impact

Rejected events never reach `normalized-events`, so forecasts and diagnoses miss that data. The ingestion service itself is healthy — it is doing its job (REQ-B).

## Diagnosis

```promql
service:ingestion_events_invalid:ratio_rate10m
sum by (outcome) (rate(ingestion_events_total[5m]))
```
Inspect rejected events and the `x-error-reason` header:
```bash
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic raw-events.DLT --from-beginning --max-messages 20 \
  --property print.headers=true --property print.key=true
docker compose -f infra/docker-compose.yml logs --since=15m ingestion-service | grep -i "invalid\|rejected"
```
Group by `source` / reason: a single producer or a schema change (e.g. new top-level key with
`STRICT_VALIDATION=true`, missing `timestamp` offset) is the usual cause.

## Mitigation

1. Identify the producer (`source` field) and ask its owner to fix/roll back.
2. If the change is legitimate (new optional top-level field), agree a contract change via the
   Software Architect (CONTRACTS §1.1) — **do not** silently disable `STRICT_VALIDATION` in production.
3. After the fix, replay DLT events if they are needed (they are stored unchanged).

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
