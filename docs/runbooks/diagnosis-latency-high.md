# DiagnosisLatencyHigh

| | |
|---|---|
| **Alert** | `DiagnosisLatencyHigh` |
| **Severity** | warning |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

p95 end-to-end diagnosis latency > 30 s for 15 minutes.

## Symptoms

*Diagnosis latency p95* above 30 s; alerts show `playbook_id = null` for longer after creation.

## Impact

Playbooks reach on-call late, eating the ≥ 10 min lead time (REQ-E). Forecasting's call to `/diagnose` may time out and exhaust its 3 retries.

## Diagnosis

```promql
service:diagnosis_latency_seconds:p95_5m
provider:llm_request_seconds:p95_5m        # LLM is the usual culprit
service:graph_context_seconds:p95_5m
```

## Mitigation

1. LLM slow → smaller/faster model or provider; lower `LLM_TIMEOUT_SECONDS` so fallback kicks in sooner.
2. Graph slow → see [graph-context-latency-high](graph-context-latency-high.md).
3. CPU starvation (Ollama on the same host) → separate the LLM host.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
