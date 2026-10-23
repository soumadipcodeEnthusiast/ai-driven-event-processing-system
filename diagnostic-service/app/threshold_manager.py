"""
threshold_manager.py — anomaly threshold evaluation (SRS v1.0 §4.4, REQ-017, REQ-020).

REQ-017: trigger an alert when the anomaly score exceeds a configurable threshold.
REQ-020: allow administrators to adjust detection thresholds via configuration
(at runtime, without a redeploy — see also REQ-052, Administration and
Configuration Management).

Boundary: a score must strictly *exceed* the threshold (``score > threshold``),
matching the SRS wording. No hysteresis: each event is evaluated on its own,
so there is no alert state to flap.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from app.metrics import ANOMALY_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThresholdChange:
    """One entry of the configuration-change audit trail (REQ-053)."""

    old: float
    new: float
    changed_by: str | None
    changed_at: str


def _validate(threshold: float) -> float:
    value = float(threshold)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"threshold must be a finite number in [0, 1], got {threshold!r}")
    return value


class ThresholdManager:
    """Holds the current anomaly-score threshold and evaluates scores against it.

    The threshold is mutable at runtime (REQ-020); reads/writes come from
    concurrent request-handling and consumer threads, hence the lock.
    """

    def __init__(self, threshold: float) -> None:
        self._threshold = _validate(threshold)
        self._lock = threading.Lock()
        self._history: list[ThresholdChange] = []
        ANOMALY_THRESHOLD.set(self._threshold)

    @property
    def threshold(self) -> float:
        with self._lock:
            return self._threshold

    @property
    def history(self) -> list[ThresholdChange]:
        with self._lock:
            return list(self._history)

    def set_threshold(self, threshold: float, changed_by: str | None = None) -> None:
        """Update the detection threshold at runtime (REQ-020).

        Raises ``ValueError`` for values outside ``[0, 1]``. Every change is
        logged and kept in ``history`` (REQ-053 configuration-change audit).
        """
        value = _validate(threshold)
        with self._lock:
            old = self._threshold
            self._threshold = value
            change = ThresholdChange(
                old=old,
                new=value,
                changed_by=changed_by,
                changed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            self._history.append(change)
            del self._history[:-100]  # keep the most recent 100 changes
            ANOMALY_THRESHOLD.set(value)
        logger.info(
            "event=threshold_changed old=%s new=%s changed_by=%s", old, value, changed_by or "-"
        )

    def evaluate(self, score: float) -> bool:
        """Return whether ``score`` strictly exceeds the current threshold (REQ-017)."""
        return score > self.threshold
