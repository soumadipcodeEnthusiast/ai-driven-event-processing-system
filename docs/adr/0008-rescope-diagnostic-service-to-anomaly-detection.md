# ADR-0008: Rescope diagnostic-service to anomaly detection, drop GraphRAG/Neo4j/LLM/playbooks

## Status
Accepted (2026-10-11)

## Context
`diagnostic-service` was built against an earlier, unfinalized design: a 2-hop
dependency-graph lookup (networkx or Neo4j, ADR-0003), an LLM prompted with
that graph context with a rule-based fallback (ADR-0004), and a file-based
JSON store for the resulting `IncidentPlaybook`s (ADR-0005).

The finalized SRS (`AI_Event_Processing_SRS_V2.pdf`, Version 1.0) does not
contain this feature. Its System Features chapter (§4) specifies, for this
service's domain, only **4.4 Anomaly Detection** (REQ-016 to REQ-020): compute
an anomaly score, trigger an alert above a configurable threshold, support
model swaps without downtime, record triggered anomalies to an audit topic,
and let administrators adjust the threshold at runtime. GraphRAG is named
explicitly in **Appendix C: Future Enhancements** as "relationship-aware
fraud and anomaly reasoning" — i.e., out of v1.0 scope. (Note: SRS §3.3,
Software Interfaces, still lists Neo4j; that line appears to be a holdover
from the same earlier design and is inconsistent with §4.4 and Appendix C. We
treat §4.4 + Appendix C as authoritative for scope, since they're specific
to this feature, and flag the §3.3 mention as a likely SRS inconsistency
rather than a mandate to keep Neo4j.)

## Decision
Remove the dependency-graph client, LLM client, rule-based fallback generator,
playbook generator, and the graph seed data / Neo4j config / Jinja2 prompt
template entirely, rather than keeping them disabled behind a flag. Replace
the service's content with three REQ-016–020-mapped stub modules
(`anomaly_scorer.py`, `threshold_manager.py`, `audit_publisher.py`) and a
`main.py` exposing scoring and threshold endpoints. ADR-0001, ADR-0003,
ADR-0004 and ADR-0005 are marked superseded rather than deleted, to preserve
the design history; this ADR is their replacement. ADR-0001's synchronous
forecasting → diagnostic HTTP hand-off is removed from forecasting-service
too (`diagnostic_client.py`, `PredictiveAlert.playbook_id`): there is no
`/diagnose` endpoint left to call, and SRS v1.0 has no hand-off between
Predictive Analytics (§4.5) and Anomaly Detection (§4.4) — they are
independent features.

## Consequences
- + The service's dependencies, config surface and Docker image shrink
  (no Neo4j, no Jinja2, no LLM provider wiring).
- + Scope now matches the SRS the rest of the system is being aligned to.
- − The GraphRAG/dependency-graph/playbook work is discarded, not archived
  as code; it is only recoverable from git history prior to this commit.
- − `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md`, the Grafana dashboards and
  Prometheus rules that reference the old `REQ-G`/`REQ-H`/`REQ-I` metrics
  (`graph_context_seconds`, `llm_request_seconds`, `playbooks_stored_total`,
  etc.) need a follow-up pass to match the new `anomaly_score`,
  `anomalies_detected_total`, `anomaly_scoring_latency_seconds` metrics —
  tracked separately, not fully done in this change.
- − If GraphRAG is revisited per Appendix C, it restarts from this ADR's
  predecessors rather than resuming the deleted code.
