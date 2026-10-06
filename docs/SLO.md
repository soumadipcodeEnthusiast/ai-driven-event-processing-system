# Service Level Objectives

Owner: SRE. Implementation: `infra/prometheus/rules/slo-recording.yml` (SLIs, budgets),
`infra/prometheus/rules/slo-burn-alerts.yml` (burn-rate alerts), Grafana dashboard
**AIOps SLOs & Error Budgets** (`http://localhost:3000/d/aiops-slo`).
All metric names come from [CONTRACTS.md §7](CONTRACTS.md#7-prometheus-metrics-names-are-fixed--dashboards-and-alert-rules-depend-on-them).

Every SLO uses a **rolling 30-day window**. SLIs are ratios of good events to total
events, so they are request-weighted and cheap to compute.

## 1. SLO catalogue

| SLO id (`slo` label) | Requirement | SLI (good / total) | Objective | Budget (30d) |
|---|---|---|---|---|
| `ingestion-availability` | REQ-A, REQ-B | events with `outcome != "error"` / all `ingestion_events_total` | **99.9 %** | 0.1 % of events |
| `ingestion-latency` | REQ-A | `ingestion_processing_seconds_bucket{le="0.5"}` / `ingestion_processing_seconds_count` | **99 %** < 500 ms | 1 % of events |
| `forecast-availability` | REQ-D, REQ-E | `forecast_runs_total{outcome="ok"}` / all runs | **99 %** | 1 % of runs |
| `forecast-latency` | REQ-D | `forecast_latency_seconds_bucket{le="2.0"}` / `_count` | **99 %** < 2 s | 1 % of runs |
| `diagnosis-availability` | REQ-H, REQ-I | `diagnoses_total{outcome=~"ok\|fallback"}` / all diagnoses | **99 %** | 1 % of diagnoses |
| `graph-context-latency` | REQ-G | `graph_context_seconds_bucket{le="1.0"}` / `_count` | **99 %** < 1 s | 1 % of lookups |
| `service-availability` | all | `avg_over_time(up{job=~"ingestion-service\|forecasting-service\|diagnostic-service"}[30d])` | **99.5 %** | ~3.6 h down / 30d |

Why these choices:

- **Invalid events count as good for ingestion availability.** REQ-B says malformed
  events must be *rejected* to the DLT; doing that correctly is success. Only
  `outcome="error"` (unexpected failure after 3 retries) burns budget. A high
  invalid ratio is a *producer* problem and has its own alert
  (`IngestionInvalidRatioHigh`).
- **Fallback playbooks count as good for diagnosis availability.** CONTRACTS §5:
  the system must always produce a playbook. A fallback still has ≥ 3 ranked steps
  (REQ-H); it is a quality degradation, watched by `DiagnosticFallbackRatioHigh`,
  not an outage.
- Latency SLOs use **histogram-bucket ratios** rather than percentiles so they can be
  aggregated and budgeted. The thresholds are the REQ acceptance criteria.

### Requirements verified outside the SLO framework

| Requirement | How it is measured |
|---|---|
| REQ-A "lag < 500 ms at 10 000 ev/s" | Records lag is the metric available (`kafka_consumer_fetch_manager_records_lag_max`). At 10 000 ev/s, 500 ms = **5 000 records**. `IngestionConsumerLagHigh` pages at > 5 000 for 5 min and warns at > 1 000 for 15 min. Processing time is covered by the `ingestion-latency` SLO. |
| REQ-B rejection correctness | Functional (unit/integration tests). Operationally: invalid ratio (`service:ingestion_events_invalid:ratio_rate10m`) and DLT throughput on the Service Health dashboard. |
| REQ-E alert lead time ≥ 10 min | Every predicted breach raises an alert carrying its real lead time (0 if the metric is already breaching), so late warnings are visible rather than suppressed. Each alert's `lead_time_minutes` records the warning it gave; a value below 10 means the forecaster saw that breach too late. The AIOps Overview **Time to next predicted breach** panel (live, from `/alerts/summary`) turns red below 10 min. `ForecastLoopStalled` and `forecast-availability` protect the precondition that forecasts run at all. |
| REQ-H playbook has ≥ 3 steps | Enforced by the contract and tests. The overview dashboard shows the ranked steps of any selected playbook. |
| REQ-K alert visible within 5 s | The overview dashboard refreshes every **5 s** and reads `/alerts` directly (no intermediate store), so worst-case display latency = 5 s + one HTTP round-trip. Grafana availability is watched by `GrafanaDown`. |

## 2. Error budgets

`slo:error_budget:remaining_ratio{slo}` = `1 − (error ratio over 30d / (1 − objective))`.
1 = untouched, 0 = exhausted, negative = SLO breached.

| Objective | Allowed bad events per 1 M | Allowed full-outage time / 30d |
|---|---|---|
| 99.9 % | 1 000 | 43 min |
| 99.5 % | 5 000 | 3 h 36 min |
| 99 % | 10 000 | 7 h 12 min |

## 3. Burn-rate alerting (multi-window, multi-burn-rate)

Burn rate = error ratio ÷ (1 − objective). A burn rate of 1 spends exactly the
whole budget in 30 days. We alert on two windows at once — a long window for
significance and a short window (1/12 of it) so the alert resets quickly after
recovery:

| Severity | Long window | Short window | Burn rate | Budget consumed when it fires | Response |
|---|---|---|---|---|---|
| critical (page) | 1 h | 5 m | 14.4× | 2 % | immediate |
| critical (page) | 6 h | 30 m | 6× | 5 % | immediate |
| warning (ticket) | 1 d | 2 h | 3× | 10 % | next business day |
| warning (ticket) | 3 d | 6 h | 1× | 10 % | next business day |

Applied to `ingestion-availability`, `ingestion-latency`, `forecast-availability` and
`forecast-latency` (alerts `IngestionErrorBudgetBurn`, `IngestionLatencyBudgetBurn`,
`ForecastErrorBudgetBurn`, `ForecastLatencyBudgetBurn`).

The diagnostic SLOs are **low-traffic** (one diagnosis per predictive alert), where
burn-rate alerts are noisy. They use threshold alerts with a minimum-volume guard
instead (`DiagnosisErrorRateHigh`, `GraphContextLatencyHigh`) and their budget is
tracked on the dashboard only.

## 4. Error-budget policy

| Budget remaining (30d) | Policy |
|---|---|
| > 50 % | Ship normally. Chaos experiments and risky migrations allowed. |
| 25 – 50 % | Ship normally; every change needs a rollback plan. Review top burn contributors weekly. |
| 0 – 25 % | Only low-risk changes and reliability fixes. No model (TFT) promotions without canary. |
| < 0 (breached) | **Feature freeze** for the owning service until the budget is positive; a postmortem is written; the top action item is prioritised over roadmap work. |

Exceptions: security fixes always ship. Budget burned by a dependency outside our
control (e.g. a hosted LLM provider) is still counted but discussed in the postmortem.

## 5. Review

SLOs and thresholds are reviewed monthly and after every SEV-1/SEV-2 incident.
Change an objective only together with the recording rule (`slo:objective:ratio`)
and this document.

## 6. Instrumentation requirements (for service owners)

For the latency SLIs to work, the histograms must have a bucket boundary exactly at the
SLO threshold:

| Metric | Required bucket | Note |
|---|---|---|
| `ingestion_processing_seconds` | `le="0.5"` | Micrometer: `management.metrics.distribution.slo.ingestion.processing=500ms` (percentile-histogram buckets alone do not contain 0.5). |
| `forecast_latency_seconds` | `le="2.0"` (or `2`) | prometheus_client default buckets do **not** include 2 — set `buckets=(…, 1.0, 2.0, 5.0, …)`. |
| `graph_context_seconds` | `le="1.0"` (or `1`) | default buckets are fine. |
| `diagnosis_latency_seconds`, `llm_request_seconds` | any, ideally up to 60 s | p95 is computed with `histogram_quantile`; add 15, 30, 60 s buckets for LLM calls. |
