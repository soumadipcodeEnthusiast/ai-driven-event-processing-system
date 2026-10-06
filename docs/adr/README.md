# Architecture Decision Records

Nygard format (Status / Context / Decision / Consequences). Numbered sequentially and never renumbered. To change a decision, supersede it with a new ADR. Any change to `docs/CONTRACTS.md` must reference an ADR here.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-http-handoff-forecasting-to-diagnostic.md) | Synchronous HTTP hand-off forecasting → diagnostic (not a Kafka alerts topic) | Accepted |
| [0002](0002-statistical-fallback-forecaster.md) | Statistical forecaster by default, TFT optional (`FORECASTER=auto`) | Accepted |
| [0003](0003-networkx-seed-graph-default.md) | networkx seed graph by default, Neo4j optional | Accepted |
| [0004](0004-pluggable-llm-with-rule-based-fallback.md) | Pluggable LLM provider with rule-based fallback | Accepted |
| [0005](0005-file-based-json-playbook-store.md) | File-based JSON playbook store | Accepted |
| [0006](0006-grafana-infinity-dashboard.md) | Grafana + Infinity data source as the REQ-K dashboard | Accepted |
| [0007](0007-in-memory-alert-store.md) | In-memory alert store (interim, single replica) | Accepted (interim) |
