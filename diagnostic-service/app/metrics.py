"""
metrics.py — Prometheus metrics for diagnostic-service (anomaly detection).

Names and labels are fixed by docs/CONTRACTS.md §7 — dashboards and alert
rules depend on them. Do not rename.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ANOMALY_SCORES = Histogram(
    "anomaly_score",
    "Distribution of computed anomaly scores (REQ-016).",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
ANOMALIES_DETECTED = Counter(
    "anomalies_detected_total",
    "Events whose anomaly score exceeded the configured threshold (REQ-017).",
)
ANOMALY_AUDIT_PUBLISH_FAILURES = Counter(
    "anomaly_audit_publish_failures_total",
    "Failed attempts to publish an anomaly record to the audit topic (REQ-019).",
)
ANOMALY_AUDIT_PUBLISHED = Counter(
    "anomaly_audit_published_total",
    "Anomaly records the broker acknowledged on the audit topic (REQ-019).",
)
EVENTS_SCORED = Counter(
    "anomaly_events_scored_total",
    "Events scored, by entry point (http = POST /score, kafka = normalized-events consumer).",
    ["source"],
)
CONSUMER_ERRORS = Counter(
    "anomaly_consumer_errors_total",
    "normalized-events consumer errors, by reason (kafka|malformed|unexpected).",
    ["reason"],
)
ANOMALY_THRESHOLD = Gauge(
    "anomaly_threshold",
    "Current anomaly-score threshold (REQ-017/REQ-020).",
)
MODEL_INFO = Gauge(
    "anomaly_model_info",
    "Active anomaly scoring model version (value is always 1).",
    ["model_version"],
)
SCORING_LATENCY = Histogram(
    "anomaly_scoring_latency_seconds",
    "Latency of a single AnomalyScorer.score_event call.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)



# ── GraphRAG root-cause analysis (dependency graph → LLM → incident playbook) ──

DIAGNOSES = Counter(
    "diagnoses_total",
    "Diagnoses executed: ok (LLM playbook), fallback (rule-based), cached, error (no playbook).",
    ["outcome"],
)
DIAGNOSIS_LATENCY = Histogram(
    "diagnosis_latency_seconds",
    "End-to-end latency of one diagnosis (graph context + LLM + persistence).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 240.0),
)
GRAPH_CONTEXT = Histogram(
    "graph_context_seconds",
    "Latency of dependency-graph context retrieval (target < 1 s).",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
LLM_REQUEST = Histogram(
    "llm_request_seconds",
    "Latency of one LLM invocation including retries.",
    ["provider"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 240.0),
)
LLM_FAILURES = Counter(
    "llm_failures_total",
    "LLM invocations that produced no usable playbook (transport error or invalid output).",
    ["provider"],
)
PLAYBOOKS_STORED = Counter(
    "playbooks_stored_total",
    "Playbooks persisted to PLAYBOOK_STORE_DIR.",
)
PLAYBOOKS_PRUNED = Counter(
    "playbooks_pruned_total",
    "Playbooks deleted by retention (PLAYBOOK_MAX_COUNT / PLAYBOOK_RETENTION_DAYS).",
)
DIAGNOSE_RATE_LIMITED = Counter(
    "diagnose_rate_limited_total",
    "POST /diagnose requests rejected with 429 by the rate limiter.",
)
INCIDENTS = Counter(
    "incident_triggers_total",
    "Automatic diagnoses requested from detected anomalies, by outcome "
    "(queued|cooldown|dropped|unknown_component|done|failed).",
    ["outcome"],
)
PLAYBOOKS_PUBLISHED = Counter(
    "incident_playbooks_published_total",
    "Incident playbooks the broker acknowledged on the incident-playbooks topic.",
    ["outcome"],
)


def set_model_info(model_version: str) -> None:
    MODEL_INFO.clear()
    MODEL_INFO.labels(model_version=model_version).set(1)


HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method and route template.",
    ["method", "path"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Records ``http_requests_total`` / ``http_request_duration_seconds``.

    The ``path`` label uses the matched route template (e.g. ``/alerts/{alert_id}``)
    to keep label cardinality bounded.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            HTTP_REQUEST_DURATION.labels(request.method, path).observe(time.perf_counter() - start)
