# AI-Driven Event Processing Architecture

A dual-path event-processing system combining a **synchronous Kafka + Java Spring Boot ingestion pipeline** with an **asynchronous AIOps layer** for predictive failure forecasting (Temporal Fusion Transformer) and automated root-cause playbook generation (GraphRAG over a dependency graph).

---

## System Overview

```
External Events
      │
      ▼
┌─────────────────────────────┐
│  ingestion-service (Java)   │  ← Synchronous Path
│  Kafka Consumer             │    REQ-A, REQ-B, REQ-C
│  Normalization              │
└─────────────┬───────────────┘
              │ NormalizedEvent
              ▼
┌─────────────────────────────┐     ┌──────────────────────────────┐
│  forecasting-service (Py)   │────▶│  diagnostic-service (Py)     │
│  TFT-based metric forecast  │     │  GraphRAG root-cause engine   │
│  REQ-D, REQ-E, REQ-F        │     │  REQ-G, REQ-H, REQ-I         │
└─────────────────────────────┘     └──────────────────────────────┘
              │                                    │
              └──────────────┬─────────────────────┘
                             ▼
              ┌──────────────────────────┐
              │  dashboard (REQ-K)       │
              │  Alerts + Playbooks      │
              └──────────────────────────┘
```

## Services

| Service | Language | Port | Description |
|---|---|---|---|
| `ingestion-service` | Java 17 / Spring Boot 3 | 8080 | Kafka consumer, validation, normalisation |
| `forecasting-service` | Python 3.11 / FastAPI | 8081 | TFT metric forecasting + threshold evaluation |
| `diagnostic-service` | Python 3.11 / FastAPI | 8082 | GraphRAG diagnostics + playbook generation |

## Quick Start

```bash
# Clone and enter repository
git clone <repo-url>
cd ai-driven-event-processor

# Start all infrastructure + services
./scripts/setup-local.sh

# Or directly with Docker Compose
docker-compose -f infra/docker-compose.yml up --build
```

> **Note:** Services will start but stub methods will raise errors at runtime — this is expected during the scaffolding phase.

## Requirements Traceability

| REQ ID | Owner Service | File |
|---|---|---|
| REQ-A | ingestion-service | `EventConsumer.java` |
| REQ-B | ingestion-service | `EventConsumer.java`, `NormalizationService.java` |
| REQ-C | ingestion-service | `NormalizationService.java` |
| REQ-D | forecasting-service | `forecasting_engine.py` |
| REQ-E | forecasting-service | `forecasting_engine.py` |
| REQ-F | forecasting-service | `forecasting_engine.py` |
| REQ-G | diagnostic-service | `diagnostic_engine.py`, `dependency_graph_client.py` |
| REQ-H | diagnostic-service | `diagnostic_engine.py`, `playbook_generator.py` |
| REQ-I | diagnostic-service | `playbook_generator.py` |
| REQ-K | dashboard | `dashboard/README.md` |

## Documentation

- [`docs/STRUCTURE.md`](docs/STRUCTURE.md) — Repository layout and conventions
- [`docs/PLAN.md`](docs/PLAN.md) — Implementation road-map
- [`docs/sdlc-justification.md`](docs/sdlc-justification.md) — SDLC methodology rationale

## License

MIT — see [`LICENSE`](LICENSE).
