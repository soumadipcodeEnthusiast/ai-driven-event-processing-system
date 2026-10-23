# SDLC Methodology Justification

## Executive Summary

This project adopts an **Iterative + Incremental** SDLC model (specifically
aligned with the **Agile / Scrum** framework), rather than a sequential
Waterfall model. This document justifies that choice in the context of the
AI-Driven Event Processing Architecture.

---

## Why Iterative / Incremental?

### 1. Evolving ML Model Requirements (REQ-021 to REQ-025)

The forecasting model (TensorFlow/scikit-learn, per SRS v1.0 §2.5/§3.3)
requires empirical tuning: forecast-window lengths, threshold sensitivity,
and retraining cadence are only determinable after observing real traffic
patterns. A Waterfall model would force these decisions up-front based on
assumptions; an iterative model lets them emerge from measured results.

### 2. Anomaly Detection Model Tuning (REQ-016 to REQ-020)

Anomaly-score thresholds and model choice depend on observed event
distributions, which only exist once ingestion is running — another reason
to defer that tuning to later iterations rather than fix it upfront. (This
project's earlier design iterated on GraphRAG prompt engineering instead;
that work is not part of SRS v1.0, which specifies plain anomaly detection
here — see `docs/adr/0008`. The same "feedback loops over upfront
specification" argument for Agile still holds for the rescoped feature.)

### 3. Independent Service Scalability

The three services (ingestion, forecasting, diagnostic) evolve at different
rates:
- `ingestion-service` is largely stable once the Kafka schema is agreed.
- `forecasting-service` iterates as model accuracy improves.
- `diagnostic-service` iterates as the anomaly-detection model and
  threshold tuning mature.

An incremental approach lets each service be released independently without
holding up the others.

### 4. Risk Management

Highest-risk components (the forecasting ML pipeline, anomaly-detection
model selection) are scheduled early in the backlog so integration failures
are discovered before downstream services are completed.

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
| 1 | Repository scaffold, Jenkins CI pipeline, Terraform cluster, Kafka integration smoke test |
| 2 | `NormalizationService` + schema validation (REQ-005, REQ-008) |
| 3 | `ForecastingEngine` statistical forecaster → ML (TensorFlow/scikit-learn) model (REQ-021) |
| 4 | Threshold evaluation + `PredictiveAlert` emission (REQ-025, REQ-052) |
| 5 | `AnomalyScorer` model integration (REQ-016, REQ-018) |
| 6 | `ThresholdManager` + `AuditPublisher` (REQ-017, REQ-019, REQ-020) |
| 7 | Dashboard integration (REQ-034), OAuth2/TLS (REQ-046, REQ-047), S3 archival (REQ-028), end-to-end testing |
| 8 | Performance tuning, HPA validation, production hardening |

---

## References

- Beck, K. et al. (2001). *Manifesto for Agile Software Development*.

(The earlier design's references to LSTM and Temporal Fusion Transformer
papers are removed along with the PyTorch/pytorch-forecasting stack they
supported — SRS v1.0 specifies TensorFlow and Scikit-learn instead, see
`forecasting-service/requirements-ml.txt`.)
