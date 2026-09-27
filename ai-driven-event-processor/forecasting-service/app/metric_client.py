"""
metric_client.py — Prometheus HTTP API polling client stub.

Wraps calls to the Prometheus query_range endpoint to retrieve metric
time-series data for use by ForecastingEngine.

Requirements:
    REQ-D — Metric data must be polled from Prometheus for TFT input.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
DEFAULT_STEP = os.getenv("PROMETHEUS_STEP", "60s")


class MetricClient:
    """
    HTTP client for querying the Prometheus HTTP API.

    All methods are stubs — implement using the httpx async or sync client.
    """

    def __init__(self, base_url: str = PROMETHEUS_URL) -> None:
        self._base_url = base_url.rstrip("/")
        logger.info("MetricClient initialised (base_url=%s)", self._base_url)

    def query_range(
        self,
        promql: str,
        start: str,
        end: str,
        step: str = DEFAULT_STEP,
    ) -> list[dict[str, Any]]:
        """
        Execute a Prometheus range query and return raw result data.

        REQ-D: The system shall query Prometheus using the ``query_range``
        endpoint to retrieve historical metric samples over the forecasting
        window.

        Args:
            promql: PromQL expression (e.g. ``rate(http_requests_total[5m])``).
            start:  ISO-8601 start timestamp (inclusive).
            end:    ISO-8601 end timestamp (inclusive).
            step:   Resolution step (e.g. ``"60s"``).

        Returns:
            List of result dicts from the Prometheus ``data.result`` array.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-D
        #   URL: GET {base_url}/api/v1/query_range
        #   Params: query=promql, start=start, end=end, step=step
        #   Return: response.json()["data"]["result"]
        raise NotImplementedError("TODO: implement query_range — REQ-D")

    def query_instant(self, promql: str, time: str | None = None) -> list[dict[str, Any]]:
        """
        Execute a Prometheus instant query.

        REQ-D: Used to verify current metric state before triggering a
        full range query.

        Args:
            promql: PromQL expression.
            time:   Optional evaluation timestamp (ISO-8601); defaults to now.

        Returns:
            List of result dicts from the Prometheus ``data.result`` array.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-D
        raise NotImplementedError("TODO: implement query_instant — REQ-D")
