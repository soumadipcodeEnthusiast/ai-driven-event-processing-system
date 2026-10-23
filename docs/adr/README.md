# Architecture Decision Records

Nygard format (Status / Context / Decision / Consequences). Numbered sequentially and never renumbered. To change a decision, supersede it with a new ADR. Any change to `docs/CONTRACTS.md` must reference an ADR here.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-http-handoff-forecasting-to-diagnostic.md) | Synchronous HTTP hand-off forecasting → diagnostic (not a Kafka alerts topic) | Reinstated by 0010 |
| [0002](0002-statistical-fallback-forecaster.md) | Statistical forecaster by default, TFT optional (`FORECASTER=auto`) | Amended by 0010 (TFT is the default when trained) |
| [0003](0003-networkx-seed-graph-default.md) | networkx seed graph by default, Neo4j optional | Reinstated by 0010, with Neo4j as the default backend |
| [0004](0004-pluggable-llm-with-rule-based-fallback.md) | Pluggable LLM provider with rule-based fallback | Reinstated by 0010 (Ollama default) |
| [0005](0005-file-based-json-playbook-store.md) | File-based JSON playbook store | Reinstated by 0010 |
| [0006](0006-grafana-infinity-dashboard.md) | Grafana + Infinity data source as the REQ-034 dashboard | Partially superseded by 0008 (Infinity/aiops-overview dropped; Grafana itself stands) |
| [0007](0007-in-memory-alert-store.md) | In-memory alert store (interim, single replica) | Accepted (interim) |
| [0008](0008-rescope-diagnostic-service-to-anomaly-detection.md) | Rescope diagnostic-service to anomaly detection (REQ-016–020), drop GraphRAG/Neo4j/LLM/playbooks | GraphRAG part superseded by 0010; anomaly detection stands |
| [0009](0009-standalone-kafka-to-s3-archiver.md) | Standalone Kafka → S3 archiver (`event-archiver`) for REQ-028 to REQ-030 | Accepted |
| [0010](0010-reinstate-graphrag-and-tft.md) | Reinstate GraphRAG root-cause analysis (Neo4j + Ollama LLM) and the TFT forecaster in the live pipeline | Accepted |
