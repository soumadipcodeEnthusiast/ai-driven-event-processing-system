# Runbooks

One runbook per Prometheus alert in `infra/prometheus/rules/`. Each alert's `runbook_url` annotation points here. SLO definitions and the error-budget policy: [../SLO.md](../SLO.md).

Commands assume the repo root as working directory and the local stack (`docker compose -f infra/docker-compose.yml`); Kubernetes equivalents use namespace `event-processor`.

`DiagnosticFallbackRatioHigh`, `LLMRequestFailures`, `DiagnosisErrorRateHigh`,
`DiagnosisLatencyHigh` and `GraphContextLatencyHigh` are retired along with
the GraphRAG/LLM/playbook design (docs/adr/0008); `AnomalyAuditPublishFailing`
replaces them for the rescoped anomaly-detection feature.

| Alert | Severity | Service | Runbook |
|---|---|---|---|
| `ServiceDown` | critical | any application service (incl. event-archiver) | [service-down.md](service-down.md) |
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
| `AnomalyAuditPublishFailing` | warning | diagnostic-service | [anomaly-audit-publish-failing.md](anomaly-audit-publish-failing.md) |
| `EventArchiveUploadFailing` | warning | event-archiver | [event-archive-failing.md](event-archive-failing.md) |
| `EventArchiveStalled` | critical | event-archiver | [event-archive-failing.md](event-archive-failing.md) |
| `PredictiveAlertCritical` | critical | forecasting-service | [predictive-alert-critical.md](predictive-alert-critical.md) |
| `PredictiveAlertsUnacknowledged` | warning | forecasting-service | [predictive-alerts-unacknowledged.md](predictive-alerts-unacknowledged.md) |

## Severity levels

| Severity | Meaning | Response |
|---|---|---|
| critical | User-visible impact now or SLO fast burn | Page; acknowledge within 5 min |
| warning | Degradation or slow budget burn | Ticket; handle within the business day |

## Ownership

(These rows are informal repo-maintenance ownership, distinct from SRS v1.0
§2.3's user classes. "SRE" isn't one of the SRS actors, though, so that row
is retitled "DevOps Engineer" — the closest SRS fit for Prometheus/Grafana/
SLO ownership.)

| Service | Owner |
|---|---|
| ingestion-service, Kafka topics | Data Engineer |
| forecasting-service, diagnostic-service | AI Engineer |
| event-archiver, S3 archive bucket | Data Engineer |
| docker-compose, k8s, Terraform, CI | DevOps |
| Prometheus, Grafana, SLOs, runbooks | DevOps Engineer |

## Useful links

- Prometheus alerts: http://localhost:9090/alerts · targets: http://localhost:9090/targets
- Grafana: http://localhost:9030 (AIOps folder)
- Validate rules: `promtool check rules infra/prometheus/rules/*.yml && promtool test rules infra/prometheus/rules/tests/*.yml`
