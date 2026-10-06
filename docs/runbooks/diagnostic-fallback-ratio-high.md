# DiagnosticFallbackRatioHigh

| | |
|---|---|
| **Alert** | `DiagnosticFallbackRatioHigh` |
| **Severity** | warning |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

More than 50 % of diagnoses used the rule-based fallback while an LLM provider is configured (LLM degraded).

## Symptoms

*Diagnostic fallback ratio* stat orange/red; playbooks show `generated_by = fallback:rule-based`.

## Impact

Playbooks are still produced (≥ 3 steps) but are generic, graph-derived steps instead of LLM-reasoned remediation. Slower MTTR, no outage.

## Diagnosis

```promql
service:diagnoses_fallback:ratio_rate30m
sum by (provider) (increase(llm_failures_total[30m]))
provider:llm_request_seconds:p95_5m
```
```bash
docker compose -f infra/docker-compose.yml exec diagnostic-service env | grep LLM_          # provider, URL, model, timeout
docker compose -f infra/docker-compose.yml logs --since=30m diagnostic-service | grep -i llm
# Ollama profile
curl -s localhost:11434/api/tags | jq '.models[].name'   # is LLM_MODEL pulled?
```

## Mitigation

1. Ollama: `docker compose -f infra/docker-compose.yml --profile llm up -d ollama ollama-pull`; ensure the model is pulled.
2. Hosted provider: check API key / quota / provider status page.
3. Timeouts: raise `LLM_TIMEOUT_SECONDS` modestly (watch [diagnosis-latency-high](diagnosis-latency-high.md)).
4. If the LLM will be down for a long time, set `LLM_PROVIDER=none` explicitly — the alert is
   suppressed when no LLM is configured, and fallback playbooks are the documented behaviour.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
