# ForecastErrorBudgetBurn

| | |
|---|---|
| **Alert** | `ForecastErrorBudgetBurn` |
| **Severity** | critical / warning |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/slo-burn-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Forecast runs (`forecast_runs_total{outcome="error"}`) are failing fast enough to exhaust the 99 % budget.

## Symptoms

*Forecast runs by component / outcome* shows `error` series; SLO dashboard budget for `forecast-availability` drops.

## Impact

Components without successful forecasts get **no predictive alerts** (REQ-E) — silent risk.

## Diagnosis

```promql
sum by (component, outcome) (rate(forecast_runs_total[15m]))
up{job="prometheus"}                          # forecasting reads metrics from Prometheus
max by (forecaster, model_version) (forecaster_info)
```
```bash
docker compose -f infra/docker-compose.yml logs --since=15m forecasting-service | grep -iE "error|exception|timeout"
curl -s localhost:8081/ready
curl -s 'localhost:9090/api/v1/query?query=up'   # Prometheus reachable & answering?
```
Typical causes: Prometheus unreachable/slow (`PROMETHEUS_URL`), a component in `components.yaml`
whose metric query returns no data, broken TFT model artefact after a model version bump.

## Mitigation

1. All components failing + Prometheus issue → fix Prometheus.
2. Started after a model change → set `FORECASTER=statistical` (or previous `TFT_MODEL_VERSION`) and
   restart: `docker compose -f infra/docker-compose.yml up -d forecasting-service`.
3. One component failing → see [forecast-component-failing](forecast-component-failing.md).

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
