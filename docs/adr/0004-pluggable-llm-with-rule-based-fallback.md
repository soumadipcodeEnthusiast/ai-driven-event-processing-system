# ADR-0004: Pluggable LLM provider with a rule-based fallback

## Status
Accepted (2026-10-04)

## Context
REQ-H needs a playbook with ≥ 3 ranked steps. Which LLM to use (GPT-4o vs. self-hosted Llama 3) was an open question. LLMs are slow, can be unavailable, cost money, and sometimes return malformed JSON. Tests must run without an LLM (CONTRACTS.md §6). The system should *always* produce a playbook.

## Decision
- One `LLMProvider` interface with adapters for `ollama` (default local, compose profile `llm`), `anthropic` and `openai`, selected by `LLM_PROVIDER`. `none` disables the LLM. The SDKs are optional imports; plain HTTP via httpx is fine.
- Every call is bounded by `LLM_TIMEOUT_SECONDS`. The response must parse into the `IncidentPlaybook` step schema with ≥ 3 steps.
- On a timeout, transport error, parse failure, fewer than 3 steps, or `LLM_PROVIDER=none`, a **deterministic rule-based generator** builds the playbook from the graph context: the component, unhealthy or degraded neighbours, metric anomaly type, and owner team. It is marked `generated_by: "fallback:rule-based"`, counted in `diagnoses_total{outcome="fallback"}`, and failures are counted in `llm_failures_total{provider}`.
- `generated_by` records `<provider>:<model>` for provenance.

## Consequences
- + REQ-H holds even when the LLM is down. Demos and CI are deterministic.
- + Switching providers is a configuration change, which settles the "which LLM" question without a code change.
- − Fallback playbooks are generic. The dashboard should show `generated_by` so operators know which kind they are reading.
- − Graph context (metadata, owner teams) is sent to the LLM. Hosted providers receive internal topology, so prefer `ollama` where that matters (see ARCHITECTURE.md §9).
- − LLM output is untrusted. Commands are advisory text and are **never executed**.
