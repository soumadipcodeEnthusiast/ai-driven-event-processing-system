# ForecastLatencyBudgetBurn

| | |
|---|---|
| **Alert** | `ForecastLatencyBudgetBurn` |
| **Severity** | critical / warning |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/slo-burn-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Too many forecasts take ≥ 2 s (REQ-D latency SLO, 99 %).

## Symptoms

*Forecast latency p95* above the 2 s line.

## Impact

REQ-D acceptance criterion violated; the forecast loop (every 60 s for all components) may start overrunning its interval, delaying alerts and eating lead time.

## Diagnosis

```promql
service:forecast_latency_seconds:p95_5m
max by (forecaster, model_version) (forecaster_info)      # TFT vs statistical?
rate(process_cpu_seconds_total{service="forecasting-service"}[5m])
histogram_quantile(0.95, sum by (le) (rate(prometheus_http_request_duration_seconds_bucket{handler="/api/v1/query_range"}[5m])))
```
```bash
docker compose -f infra/docker-compose.yml stats forecasting-service prometheus
time curl -s -XPOST localhost:8081/forecast -H 'content-type: application/json' \
  -d '{"component_id":"ingestion-service","forecast_window_minutes":60}' >/dev/null
```

## Mitigation

1. CPU-starved TFT inference → give the container more CPU or scale out
   (`kubectl -n event-processor get hpa forecasting-service-hpa`).
2. Slow Prometheus `query_range` → reduce `PROMETHEUS_STEP` resolution / window, or fix Prometheus load.
3. New model is slower → roll back `TFT_MODEL_VERSION` or set `FORECASTER=statistical`.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
