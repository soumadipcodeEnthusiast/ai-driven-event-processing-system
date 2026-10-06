# LLMRequestFailures

| | |
|---|---|
| **Alert** | `LLMRequestFailures` |
| **Severity** | warning |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

More than 3 LLM request failures (`llm_failures_total{provider}`) in 15 minutes.

## Symptoms

Errors in diagnostic-service logs; fallback ratio rising.

## Impact

Playbooks degrade to rule-based fallbacks (see [diagnostic-fallback-ratio-high](diagnostic-fallback-ratio-high.md)).

## Diagnosis

```promql
sum by (provider) (increase(llm_failures_total[15m]))
sum by (provider) (increase(llm_request_seconds_count[15m]))
provider:llm_request_seconds:p95_5m
```
```bash
docker compose -f infra/docker-compose.yml logs --since=15m diagnostic-service | grep -iE "llm|timeout|401|429|5[0-9][0-9]"
```
Distinguish: timeouts (latency near `LLM_TIMEOUT_SECONDS`), auth (401/403), rate limits (429),
provider 5xx, unparseable output (model returned invalid JSON/steps).

## Mitigation

1. Auth → rotate `LLM_API_KEY`. Rate limit → reduce concurrency or upgrade quota.
2. Timeouts on Ollama → check host CPU/GPU, use a smaller model.
3. Parse failures after a model change → roll back `LLM_MODEL`.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
