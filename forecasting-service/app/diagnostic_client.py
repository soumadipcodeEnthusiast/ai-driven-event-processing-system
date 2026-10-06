"""
diagnostic_client.py — hand-off of new alerts to diagnostic-service (ADR 0001).

``POST {DIAGNOSTIC_URL}/diagnose/{component_id}`` with
``{"alert_id": ..., "anomalies": [...]}``. 3 attempts in total with
exponential back-off; retries only on 5xx and network errors, never on 4xx
(a 404 means the component is not in the dependency graph — final).
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DiagnosticClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        attempts: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._attempts = max(1, attempts)
        self._backoff = backoff_seconds
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def diagnose(
        self, component_id: str, alert_id: str, anomalies: list[dict[str, Any]]
    ) -> str | None:
        """Request a playbook; return its ``playbook_id`` or None on failure."""
        url = f"{self._base_url}/diagnose/{component_id}"
        body = {"alert_id": alert_id, "anomalies": anomalies}
        for attempt in range(1, self._attempts + 1):
            delay = self._backoff * (2 ** (attempt - 1))
            try:
                resp = self._client.post(url, json=body)
            except httpx.HTTPError as exc:
                logger.warning(
                    "diagnose %s attempt %d/%d failed: %s", alert_id, attempt, self._attempts, exc
                )
            else:
                if resp.status_code < 300:
                    try:
                        pb = resp.json().get("playbook_id")
                    except ValueError:
                        pb = None
                    if isinstance(pb, str) and pb:
                        return pb
                    logger.error("diagnose %s: response without playbook_id", alert_id)
                    return None
                if resp.status_code == 429:
                    # diagnostic-service rate limiter: honour Retry-After (capped).
                    with contextlib.suppress(ValueError):
                        delay = max(delay, min(float(resp.headers.get("Retry-After", 0)), 30.0))
                elif resp.status_code < 500:
                    logger.error(
                        "diagnose %s rejected (HTTP %d), not retrying",
                        alert_id,
                        resp.status_code,
                    )
                    return None
                logger.warning(
                    "diagnose %s attempt %d/%d: HTTP %d",
                    alert_id,
                    attempt,
                    self._attempts,
                    resp.status_code,
                )
            if attempt < self._attempts:
                self._sleep(delay)
        logger.error("diagnose %s: giving up after %d attempts", alert_id, self._attempts)
        return None
