# ForecastLoopStalled

| | |
|---|---|
| **Alert** | `ForecastLoopStalled` |
| **Severity** | critical |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

forecasting-service is up but recorded no forecast runs at all for 10 minutes.

## Symptoms

`forecast_runs_total` flat; no new alerts on the REQ-K dashboard even under load.

## Impact

**Silent failure of the whole predictive layer**: no PredictiveAlerts can be emitted.

## Diagnosis

```promql
sum(increase(forecast_runs_total[10m]))
up{job="forecasting-service"}
```
```bash
docker compose -f infra/docker-compose.yml logs --since=15m forecasting-service | tail -100    # background loop crashed? exception?
docker compose -f infra/docker-compose.yml exec forecasting-service env | grep -E "FORECAST_INTERVAL|COMPONENTS_CONFIG"
```
Causes: the background task died with an unhandled exception, an empty/invalid `components.yaml`,
or a forecast that hangs forever (no timeout on the Prometheus client).

## Mitigation

1. `docker compose -f infra/docker-compose.yml restart forecasting-service` — the loop restarts with the process.
2. Validate `components.yaml` (YAML syntax, at least one component).
3. Escalate to the AI Engineer: the loop must survive per-component exceptions.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
