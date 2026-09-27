# SDLC Methodology Justification

## Executive Summary

This project adopts an **Iterative + Incremental** SDLC model (specifically
aligned with the **Agile / Scrum** framework), rather than a sequential
Waterfall model. This document justifies that choice in the context of the
AI-Driven Event Processing Architecture.

---

## Why Iterative / Incremental?

### 1. Evolving ML Model Requirements (REQ-D, REQ-E, REQ-F)

The Temporal Fusion Transformer (TFT) component requires empirical tuning:
forecast-window lengths, threshold sensitivity, and retraining cadence are
only determinable after observing real traffic patterns. A Waterfall model
would force these decisions up-front based on assumptions; an iterative model
lets them emerge from measured results.

### 2. GraphRAG Prompt Engineering (REQ-G, REQ-H, REQ-I)

Root-cause playbook quality depends on prompt structure, graph schema, and
LLM selection — all of which benefit from short feedback loops. Each sprint
delivers a working (but improving) playbook, letting domain experts validate
outputs and feed corrections back into the next iteration.

### 3. Independent Service Scalability

The three services (ingestion, forecasting, diagnostic) evolve at different
rates:
- `ingestion-service` is largely stable once the Kafka schema is agreed.
- `forecasting-service` iterates as model accuracy improves.
- `diagnostic-service` iterates as the dependency graph grows.

An incremental approach lets each service be released independently without
holding up the others.

### 4. Risk Management

Highest-risk components (TFT training pipeline, GraphRAG context window
management) are scheduled early in the backlog so integration failures are
discovered before downstream services are completed.

---

## Rejected Alternatives

| Model | Reason for Rejection |
|---|---|
| Waterfall | Requirements for ML components are inherently uncertain upfront |
| Prototype-only | No path to production quality; prototypes become permanent |
| Spiral | Overhead too high for a 3-service bounded system |
| Kanban-only | Lacks sprint-level commitment needed for cross-team coordination |

---

## Sprint Structure (Recommended)

| Sprint | Focus |
|---|---|
| 1 | Repository scaffold, CI pipeline, Kafka integration smoke test |
| 2 | `NormalizationService` + schema validation (REQ-B, REQ-C) |
| 3 | `ForecastingEngine` TFT stub → real training loop (REQ-D, REQ-E) |
| 4 | Threshold evaluation + `PredictiveAlert` emission (REQ-F) |
| 5 | `DependencyGraphClient` + `DiagnosticEngine` context retrieval (REQ-G) |
| 6 | Playbook generation + `IncidentPlaybook.render()` (REQ-H, REQ-I) |
| 7 | Dashboard integration (REQ-K), end-to-end testing |
| 8 | Performance tuning, HPA validation, production hardening |

---

## References

- Beck, K. et al. (2001). *Manifesto for Agile Software Development*.
- Hochreiter, S. & Schmidhuber, J. (1997). *Long Short-Term Memory*. Neural Computation.
- Lim, B. et al. (2021). *Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting*. International Journal of Forecasting.
