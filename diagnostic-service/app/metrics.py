"""
metrics.py — Prometheus metrics for diagnostic-service.

Names, labels and buckets are fixed by docs/CONTRACTS.md §7 and the SRE
dashboards/alert rules. ``llm_*`` series are only ever created with a real
provider label, so with LLM_PROVIDER=none no llm_* samples are exported.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

DIAGNOSES = Counter(
    "diagnoses_total",
    "Diagnoses executed: ok (LLM playbook), fallback (rule-based), error (no playbook).",
    ["outcome"],
)
DIAGNOSIS_LATENCY = Histogram(
    "diagnosis_latency_seconds",
    "End-to-end latency of POST /diagnose (context + LLM + persistence).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0),
)
GRAPH_CONTEXT = Histogram(
    "graph_context_seconds",
    "Latency of dependency-graph context retrieval (REQ-G target < 1 s).",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
LLM_REQUEST = Histogram(
    "llm_request_seconds",
    "Latency of one LLM invocation including retries.",
    ["provider"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0),
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
