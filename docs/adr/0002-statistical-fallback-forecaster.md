# ADR-0002: Statistical forecaster by default, TFT optional

## Status
Accepted (2026-10-04)

## Context
REQ-D names a Temporal Fusion Transformer. A TFT brings in torch, lightning and pytorch-forecasting (about 1-2 GB of images), needs a trained artefact, and needs history this project does not have yet. Tests must pass without ML dependencies (CONTRACTS.md §6). REQ-D also caps latency at < 2 s for a 60-minute window, and REQ-E needs a point forecast plus a p90 band.

## Decision
Define one forecaster interface that returns `point` and `p90` series per metric, with two implementations:

- **Statistical** (always available; numpy/pandas only). Trend plus seasonality-free smoothing (e.g. Holt / linear trend on the recent window), with a p90 band from residual spread (`point + 1.2816·σ`, widening with horizon).
- **TFT** (optional). Selected only if `requirements-ml.txt` is installed (`INSTALL_ML=true`) and `TFT_MODEL_PATH` exists.

`FORECASTER=auto|tft|statistical`, default `auto`. `auto` picks TFT if both the model and the deps are present, otherwise statistical. The active choice is exported as `forecaster_info{forecaster,model_version}` and written to every alert's `model_version`.

## Consequences
- + Default images are small, CI is fast, and the end-to-end demo works on a laptop with no GPU or training step.
- + Easily meets the REQ-D latency target. TFT latency is measured separately when it is enabled.
- + Gives an honest baseline: TFT has to beat it on backtests to be worth turning on.
- − The README claims TFT while the default deployment runs a statistical model. `forecaster_info` and `model_version` make this visible, and the docs must say so.
- − Two code paths. Both must satisfy the same contract tests (shape, p90 ≥ point, horizon length).
- − TFT training cadence (online vs. nightly batch) remains open. See PLAN.md.
