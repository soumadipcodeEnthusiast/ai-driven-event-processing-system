"""
metrics.py — Prometheus metrics for forecasting-service.

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

# ─── Contract metrics (§7) ───────────────────────────────────────────────────

FORECAST_RUNS = Counter(
    "forecast_runs_total",
    "Forecast cycles executed, by component and outcome (ok|error).",
    ["component", "outcome"],
)
FORECAST_LATENCY = Histogram(
    "forecast_latency_seconds",
    "End-to-end latency of one forecast cycle (poll + inference + evaluation).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)
PREDICTIVE_ALERTS = Counter(
    "predictive_alerts_total",
    "Predictive alerts created, by component and severity.",
    ["component", "severity"],
)
PREDICTIVE_ALERTS_OPEN = Gauge(
    "predictive_alerts_open",
    "Number of predictive alerts currently in status 'open'.",
)
FORECASTER_INFO = Gauge(
    "forecaster_info",
    "Active forecaster implementation (value is always 1).",
    ["forecaster", "model_version"],
)

# ─── Generic HTTP request metrics (used by components.yaml error_rate PromQL) ─

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


def set_forecaster_info(forecaster: str, model_version: str) -> None:
    FORECASTER_INFO.clear()
    FORECASTER_INFO.labels(forecaster=forecaster, model_version=model_version).set(1)


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
