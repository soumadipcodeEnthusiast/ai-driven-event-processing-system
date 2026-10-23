# ADR-0010: Reinstate GraphRAG root-cause analysis (Neo4j + LLM) and the TFT forecaster

## Status
Accepted (2026-10-22). Supersedes the GraphRAG part of ADR-0008 and reactivates
ADR-0001 (alert → diagnostic hand-off), ADR-0003 (graph backend, now Neo4j by
default), ADR-0004 (pluggable LLM with rule-based fallback) and ADR-0005
(file-based playbook store). Changes the default of ADR-0002 from Holt to TFT.

## Context
ADR-0008 removed GraphRAG and the TFT to match SRS v1.0 §4.4, which lists them
only as Appendix C future work. The project owner has since decided that both
are core parts of the system and must run in the live pipeline, not as
optional extras: GraphRAG explains *why* an anomaly or predicted breach is
happening, and the TFT is the forecasting model the project is built around.

## Decision
**Pipeline.** Two AI stages are added to the event flow:

```
normalized-events → anomaly scoring → anomaly-audit
                                    ↘ IncidentTrigger ─┐
forecasting (TFT) → predictive alert → POST /diagnose ─┤
                                                       ▼
            GraphRAG: Neo4j 2-hop context → prompt → LLM (Ollama) → IncidentPlaybook
                                                       ▼
                       incident-playbooks topic  +  /data/playbooks  +  alert.playbook_id
```

- **Anomaly → playbook.** `diagnostic-service` hands every anomalous event to
  `app/incident_trigger.py`. Per component, the first anomaly queues one
  diagnosis, anomalies in the next `INCIDENT_BATCH_SECONDS` (5 s) join it, and
  the component then cools down for `INCIDENT_COOLDOWN_SECONDS` (600 s in
  compose). One worker thread runs diagnoses so a local LLM is never asked
  for more than one answer at a time. Playbooks go to the new Kafka topic
  `incident-playbooks` (3 partitions, keyed by component).
- **Predictive alert → playbook.** `forecasting-service` posts each new alert
  to `POST /diagnose/{component}` in the background (ADR-0001's client,
  restored) and stores the returned `playbook_id` on the alert.
- **Retrieval.** Neo4j 5 (community) holds the dependency graph:
  `(:Component)` nodes, `CALLS / DEPENDS_ON / PUBLISHES_TO / CONSUMES_FROM`
  relationships. diagnostic-service seeds an empty database from
  `diagnostic-service/data/dependency_graph.json`, which now models the real
  system: the four producer applications, Kafka/ZooKeeper, the four pipeline
  services, S3, Neo4j, Ollama, Prometheus and Grafana. If Neo4j is
  unreachable the same seed is used in memory.
- **Generation.** Ollama runs `llama3.2:3b` locally (CPU, ~2 GB download,
  ~3 GB RAM) so the demo needs no API key or internet. `LLM_PROVIDER` can
  still switch to `anthropic` or `openai`; any failure or malformed answer
  produces the rule-based playbook instead (`generated_by:
  fallback:rule-based`), so every anomaly still gets a playbook.
- **Forecasting.** `FORECASTER=auto` now prefers the Temporal Fusion
  Transformer (pytorch-forecasting, CPU wheels) trained by
  `training/train_tft.py` on the platform's own Prometheus history
  (`make train-tft`), then the ar-ridge model, then Holt. Holt remains the
  per-prediction fallback. The compose image is built with `INSTALL_ML=true`.
  TensorFlow is dropped from `requirements-ml.txt`: nothing imported it.

## Consequences
- + Every anomaly and every predictive alert ends in a ranked, explainable
  remediation playbook grounded in the dependency graph.
- + Runs fully offline on a laptop; the LLM and the graph are swappable.
- − The local stack needs ~3.5 GB more memory (Ollama ~3 GB, Neo4j ~0.7 GB)
  and the forecasting image grows by ~1 GB (PyTorch CPU).
- − A CPU-only 3B model takes roughly 20–90 s per playbook. This is why
  diagnoses are asynchronous, batched per component and rate-limited.
- − The TFT needs about 3 hours of metric history (120-step encoder + 60-step
  horizon at 60 s) before it can be trained; until then `auto` uses Holt.
- − The seed graph's health statuses are static; live health is not yet
  written back into Neo4j.
- − SRS v1.0 lists GraphRAG under Appendix C. This ADR records the owner's
  decision to bring it into scope; the SRS should be updated to match.
