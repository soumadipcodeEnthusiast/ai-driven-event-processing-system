# Runbooks

One runbook per Prometheus alert in `infra/prometheus/rules/`. Each alert's `runbook_url` annotation points here. SLO definitions and the error-budget policy: [../SLO.md](../SLO.md).

Commands assume the repo root as working directory and the local stack (`docker compose -f infra/docker-compose.yml`); Kubernetes equivalents use namespace `event-processor`.

| Alert | Severity | Service | Runbook |
|---|---|---|---|
| `ServiceDown` | critical | any application service | [service-down.md](service-down.md) |
| `GrafanaDown` | warning | grafana | [grafana-down.md](grafana-down.md) |
| `IngestionInvalidRatioHigh` | warning | ingestion-service | [ingestion-invalid-ratio-high.md](ingestion-invalid-ratio-high.md) |
| `IngestionErrorBudgetBurn` | critical / warning | ingestion-service | [ingestion-error-budget-burn.md](ingestion-error-budget-burn.md) |
| `IngestionLatencyBudgetBurn` | critical / warning | ingestion-service | [ingestion-latency-budget-burn.md](ingestion-latency-budget-burn.md) |
| `IngestionConsumerLagHigh` | critical (> 5000 for 5m) / warning (> 1000 for 15m) | ingestion-service | [ingestion-consumer-lag-high.md](ingestion-consumer-lag-high.md) |
| `IngestionConsumerStalled` | critical | ingestion-service | [ingestion-consumer-stalled.md](ingestion-consumer-stalled.md) |
| `IngestionJvmHeapHigh` | warning | ingestion-service | [ingestion-jvm-heap-high.md](ingestion-jvm-heap-high.md) |
| `ForecastErrorBudgetBurn` | critical / warning | forecasting-service | [forecast-error-budget-burn.md](forecast-error-budget-burn.md) |
| `ForecastLatencyBudgetBurn` | critical / warning | forecasting-service | [forecast-latency-budget-burn.md](forecast-latency-budget-burn.md) |
| `ForecastComponentFailing` | warning | forecasting-service | [forecast-component-failing.md](forecast-component-failing.md) |
| `ForecastLoopStalled` | critical | forecasting-service | [forecast-loop-stalled.md](forecast-loop-stalled.md) |
| `DiagnosticFallbackRatioHigh` | warning | diagnostic-service | [diagnostic-fallback-ratio-high.md](diagnostic-fallback-ratio-high.md) |
| `LLMRequestFailures` | warning | diagnostic-service | [llm-request-failures.md](llm-request-failures.md) |
| `DiagnosisErrorRateHigh` | critical | diagnostic-service | [diagnosis-error-rate-high.md](diagnosis-error-rate-high.md) |
| `DiagnosisLatencyHigh` | warning | diagnostic-service | [diagnosis-latency-high.md](diagnosis-latency-high.md) |
| `GraphContextLatencyHigh` | warning | diagnostic-service | [graph-context-latency-high.md](graph-context-latency-high.md) |
| `PredictiveAlertCritical` | critical | forecasting-service | [predictive-alert-critical.md](predictive-alert-critical.md) |
| `PredictiveAlertsUnacknowledged` | warning | forecasting-service | [predictive-alerts-unacknowledged.md](predictive-alerts-unacknowledged.md) |

## Severity levels

| Severity | Meaning | Response |
|---|---|---|
| critical | User-visible impact now or SLO fast burn | Page; acknowledge within 5 min |
| warning | Degradation or slow budget burn | Ticket; handle within the business day |

## Ownership

| Service | Owner |
|---|---|
| ingestion-service, Kafka topics | Data Engineer |
| forecasting-service, diagnostic-service, LLM | AI Engineer |
| docker-compose, k8s, CI | DevOps |
| Prometheus, Grafana, SLOs, runbooks | SRE |

## Useful links

- Prometheus alerts: http://localhost:9090/alerts · targets: http://localhost:9090/targets
- Grafana: http://localhost:3000 (AIOps folder)
- Validate rules: `promtool check rules infra/prometheus/rules/*.yml && promtool test rules infra/prometheus/rules/tests/*.yml`
