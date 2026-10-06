# GraphContextLatencyHigh

| | |
|---|---|
| **Alert** | `GraphContextLatencyHigh` |
| **Severity** | warning |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

p95 2-hop dependency context retrieval > 1 s for 10 minutes (REQ-G).

## Symptoms

*Graph context latency p95* above the 1 s line.

## Impact

REQ-G violated; diagnoses slower.

## Diagnosis

```promql
service:graph_context_seconds:p95_5m
```
```bash
docker compose -f infra/docker-compose.yml exec diagnostic-service env | grep GRAPH_
# Neo4j profile
docker compose -f infra/docker-compose.yml --profile neo4j logs --since=15m neo4j
```
With `networkx` this should be milliseconds: suspect CPU starvation or a graph far above 500 nodes.
With Neo4j: missing index on `node_id`, network latency, cold cache.

## Mitigation

1. Neo4j: create an index on the node id property; check heap/page cache.
2. Fall back to `GRAPH_BACKEND=networkx` if Neo4j is unhealthy.
3. Graph > 500 nodes is outside the REQ-G envelope — raise with the architect.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
