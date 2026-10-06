"""
alerts.py — PredictiveAlert model, severity rules and the in-memory alert store.

Field set is exhaustive (contracts/predictive-alert.schema.json,
additionalProperties: false) — do not add fields to ``to_dict``.

Decisions (docs/ARCHITECTURE.md §10):
    * ``predicted_value`` is the p90 value at ``predicted_breach_time``.
    * overshoot = (predicted_value - threshold) / threshold; if threshold <= 0
      any breach (value > threshold) is ``critical``.
    * An ``open`` *or* ``acknowledged`` alert blocks a new alert for the same
      (component_id, metric_name); only ``resolved`` frees the slot.
"""

from __future__ import annotations

import math
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.metrics import PREDICTIVE_ALERTS, PREDICTIVE_ALERTS_OPEN


class AlertSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


_SEVERITY_ORDER = {
    AlertSeverity.LOW: 0,
    AlertSeverity.MEDIUM: 1,
    AlertSeverity.HIGH: 2,
    AlertSeverity.CRITICAL: 3,
}


def severity_rank(sev: AlertSeverity) -> int:
    return _SEVERITY_ORDER[sev]


def overshoot_ratio(predicted_value: float, threshold: float) -> float:
    """Relative overshoot of *threshold*; ``inf`` if threshold <= 0 and breached."""
    if threshold <= 0:
        return float("inf") if predicted_value > threshold else 0.0
    return (predicted_value - threshold) / threshold


def severity_for(predicted_value: float, threshold: float) -> AlertSeverity:
    """Severity bands from CONTRACTS §3: <10% low, <25% medium, <50% high, else critical."""
    ratio = overshoot_ratio(predicted_value, threshold)
    if math.isfinite(ratio):
        ratio = round(ratio, 9)  # 0.88 vs 0.80 is exactly 10 %, not 9.999…%
    if ratio < 0.10:
        return AlertSeverity.LOW
    if ratio < 0.25:
        return AlertSeverity.MEDIUM
    if ratio < 0.50:
        return AlertSeverity.HIGH
    return AlertSeverity.CRITICAL


def utc_iso(dt: datetime) -> str:
    """UTC ISO-8601 with ``Z`` suffix, second precision."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_alert_id() -> str:
    return f"al-{secrets.token_hex(6)}"


@dataclass
class PredictiveAlert:
    """Forecast-driven predictive alert (CONTRACTS §3)."""

    alert_id: str
    component_id: str
    metric_name: str
    predicted_breach_time: datetime
    predicted_value: float
    threshold: float
    lead_time_minutes: float
    severity: AlertSeverity
    created_at: datetime
    model_version: str
    status: AlertStatus = AlertStatus.OPEN
    playbook_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "component_id": self.component_id,
            "metric_name": self.metric_name,
            "predicted_breach_time": utc_iso(self.predicted_breach_time),
            "predicted_value": round(float(self.predicted_value), 6),
            "threshold": float(self.threshold),
            "lead_time_minutes": round(float(self.lead_time_minutes), 2),
            "severity": self.severity.value,
            "status": self.status.value,
            "created_at": utc_iso(self.created_at),
            "model_version": self.model_version,
            "playbook_id": self.playbook_id,
        }

    def anomaly(self) -> dict[str, Any]:
        """Item for the ``/diagnose`` body ``anomalies[]`` (ARCHITECTURE §10.1)."""
        d = self.to_dict()
        return {
            k: d[k]
            for k in (
                "metric_name",
                "predicted_value",
                "threshold",
                "predicted_breach_time",
                "severity",
            )
        }


class InvalidTransitionError(ValueError):
    pass


class AlertStore:
    """Thread-safe in-memory alert store (ADR 0007: not durable across restarts)."""

    def __init__(self, max_alerts: int = 10_000) -> None:
        self._lock = threading.RLock()
        self._alerts: dict[str, PredictiveAlert] = {}
        self._max = max_alerts
        PREDICTIVE_ALERTS_OPEN.set(0)

    def _refresh_gauge(self) -> None:
        PREDICTIVE_ALERTS_OPEN.set(
            sum(1 for a in self._alerts.values() if a.status is AlertStatus.OPEN)
        )

    def active_for(self, component_id: str, metric_name: str) -> PredictiveAlert | None:
        """Return the open/acknowledged alert for (component, metric), if any."""
        with self._lock:
            for a in self._alerts.values():
                if (
                    a.component_id == component_id
                    and a.metric_name == metric_name
                    and a.status is not AlertStatus.RESOLVED
                ):
                    return a
        return None

    def add_if_absent(self, alert: PredictiveAlert) -> bool:
        """Atomically insert *alert* unless an active one exists (dedup). True if added."""
        with self._lock:
            if self.active_for(alert.component_id, alert.metric_name) is not None:
                return False
            self._alerts[alert.alert_id] = alert
            self._evict()
            self._refresh_gauge()
        PREDICTIVE_ALERTS.labels(component=alert.component_id, severity=alert.severity.value).inc()
        return True

    def _evict(self) -> None:
        if len(self._alerts) <= self._max:
            return
        resolved = sorted(
            (a for a in self._alerts.values() if a.status is AlertStatus.RESOLVED),
            key=lambda a: a.created_at,
        )
        for a in resolved[: len(self._alerts) - self._max]:
            del self._alerts[a.alert_id]

    def get(self, alert_id: str) -> PredictiveAlert | None:
        with self._lock:
            return self._alerts.get(alert_id)

    def list(
        self,
        status: AlertStatus | None = None,
        component_id: str | None = None,
        limit: int = 100,
    ) -> list[PredictiveAlert]:
        with self._lock:
            items = [
                a
                for a in self._alerts.values()
                if (status is None or a.status is status)
                and (not component_id or a.component_id == component_id)
            ]
        items.sort(key=lambda a: a.created_at, reverse=True)
        return items[: max(0, limit)]

    def set_playbook(self, alert_id: str, playbook_id: str) -> None:
        with self._lock:
            if alert_id in self._alerts:
                self._alerts[alert_id].playbook_id = playbook_id

    def transition(self, alert_id: str, target: AlertStatus) -> PredictiveAlert | None:
        """open→acknowledged, open|acknowledged→resolved. None if alert missing."""
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert is None:
                return None
            allowed = {
                AlertStatus.ACKNOWLEDGED: {AlertStatus.OPEN},
                AlertStatus.RESOLVED: {AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED},
            }.get(target, set())
            if alert.status is target:
                return alert  # idempotent
            if alert.status not in allowed:
                raise InvalidTransitionError(
                    f"cannot move alert from {alert.status.value} to {target.value}"
                )
            alert.status = target
            self._refresh_gauge()
            return alert

    def clear(self) -> None:
        with self._lock:
            self._alerts.clear()
            self._refresh_gauge()
