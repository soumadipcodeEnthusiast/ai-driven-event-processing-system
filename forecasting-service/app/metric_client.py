"""
metric_client.py — Prometheus HTTP API client.

Wraps ``/api/v1/query_range`` and ``/api/v1/query`` to retrieve metric
time-series for ForecastingEngine.

Requirements:
    REQ-D — Metric data must be polled from Prometheus for forecaster input.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)?$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}


class MetricClientError(RuntimeError):
    """Raised when Prometheus is unreachable or returns an error payload."""


def parse_duration_seconds(value: str | float | int) -> float:
    """Parse a Prometheus duration (``"60s"``, ``"1m"``, ``"15"``) into seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2) or "s"]


def _ts(value: str | float | datetime) -> str:
    """Prometheus accepts RFC3339 or unix seconds; normalise to unix seconds."""
    if isinstance(value, datetime):
        return f"{value.timestamp():.3f}"
    return str(value)


class MetricClient:
    """Synchronous HTTP client for the Prometheus HTTP API."""

    def __init__(
        self,
        base_url: str = "http://prometheus:9090",
        default_step: str = "60s",
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.default_step = default_step
        self._client = client or httpx.Client(timeout=timeout)
        logger.info("MetricClient initialised (base_url=%s)", self._base_url)

    def close(self) -> None:
        self._client.close()

    # ── internals ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{self._base_url}{path}"
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise MetricClientError(f"prometheus request failed: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise MetricClientError(
                f"prometheus returned non-JSON (HTTP {resp.status_code})"
            ) from exc
        if resp.status_code >= 400 or body.get("status") != "success":
            raise MetricClientError(
                f"prometheus error (HTTP {resp.status_code}): "
                f"{body.get('errorType', '')} {body.get('error', '')}".strip()
            )
        result = body.get("data", {}).get("result", [])
        if not isinstance(result, list):
            # scalar / string result types
            return [{"metric": {}, "value": result}]
        return result

    # ── public API ─────────────────────────────────────────────────────────

    def query_range(
        self,
        promql: str,
        start: str | float | datetime,
        end: str | float | datetime,
        step: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a Prometheus range query and return ``data.result``.

        REQ-D: retrieves historical metric samples over the forecasting window.

        Args:
            promql: PromQL expression.
            start:  RFC3339 string, unix seconds or datetime (inclusive).
            end:    RFC3339 string, unix seconds or datetime (inclusive).
            step:   Resolution step (e.g. ``"60s"``); defaults to PROMETHEUS_STEP.

        Returns:
            List of ``{"metric": {...}, "values": [[ts, "v"], ...]}`` dicts.

        Raises:
            MetricClientError: on transport or API errors.
        """
        return self._get(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": _ts(start),
                "end": _ts(end),
                "step": step or self.default_step,
            },
        )

    def query_instant(
        self, promql: str, time: str | float | datetime | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute a Prometheus instant query and return ``data.result``.

        Returns:
            List of ``{"metric": {...}, "value": [ts, "v"]}`` dicts.

        Raises:
            MetricClientError: on transport or API errors.
        """
        params = {"query": promql}
        if time is not None:
            params["time"] = _ts(time)
        return self._get("/api/v1/query", params)

    def ping(self) -> bool:
        """Return True if Prometheus answers its readiness endpoint."""
        try:
            return self._client.get(f"{self._base_url}/-/ready").status_code == 200
        except httpx.HTTPError:
            return False
