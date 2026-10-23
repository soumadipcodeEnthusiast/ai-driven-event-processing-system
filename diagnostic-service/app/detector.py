"""
detector.py — the score → evaluate → audit pipeline (REQ-016, REQ-017, REQ-019).

Shared by ``POST /score/{event_id}`` and the ``normalized-events`` consumer
(app/event_consumer.py) so both paths behave identically. Also owns the
active ``AnomalyScorer`` reference for REQ-018 version swaps: ``swap_model``
builds the replacement off to the side and repoints in one assignment, so
in-flight ``process`` calls finish on the old scorer and new calls pick up
the new one — no request is ever refused.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.anomaly_scorer import AnomalyScorer
from app.audit_publisher import AuditPublisher
from app.metrics import ANOMALIES_DETECTED, ANOMALY_SCORES, EVENTS_SCORED, SCORING_LATENCY
from app.threshold_manager import ThresholdManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    event_id: str
    score: float
    model_version: str
    threshold: float
    anomalous: bool
    audited: bool
    reason: str | None


# Called with (event, score, threshold, reason) for every anomalous event; used
# to start a GraphRAG diagnosis (app/incident_trigger.py). Must not block.
AnomalyHook = Callable[[dict[str, Any], float, float, "str | None"], object]


class AnomalyDetector:
    def __init__(
        self,
        scorer: AnomalyScorer,
        thresholds: ThresholdManager,
        audit: AuditPublisher,
        on_anomaly: AnomalyHook | None = None,
    ) -> None:
        self._scorer = scorer
        self.thresholds = thresholds
        self.audit = audit
        self.on_anomaly = on_anomaly

    @property
    def scorer(self) -> AnomalyScorer:
        return self._scorer

    def swap_model(self, model_version: str, *, warm_start: bool = True) -> AnomalyScorer:
        """REQ-018: rotate to ``model_version`` without downtime."""
        old = self._scorer
        self._scorer = old.with_version(model_version, warm_start=warm_start)
        logger.info(
            "event=model_swapped old=%s new=%s warm_start=%s",
            old.model_version,
            model_version,
            warm_start,
        )
        return self._scorer

    def process(self, event: dict[str, Any], source: str = "http") -> Detection:
        scorer = self._scorer  # one read: the whole call uses a single model version
        start = time.perf_counter()
        result = scorer.score_event(event)
        SCORING_LATENCY.observe(time.perf_counter() - start)
        ANOMALY_SCORES.observe(result.score)
        EVENTS_SCORED.labels(source=source).inc()

        threshold = self.thresholds.threshold
        anomalous = result.score > threshold
        audited = False
        if anomalous:
            ANOMALIES_DETECTED.inc()
            logger.warning(
                "event=anomaly_detected event_id=%s score=%.4f threshold=%s reason=%s",
                result.event_id,
                result.score,
                threshold,
                result.reason,
            )
            audited = self.audit.publish_anomaly(
                event,
                result.score,
                threshold=threshold,
                model_version=result.model_version,
                reason=result.reason,
            )
            if self.on_anomaly is not None:
                try:
                    self.on_anomaly(event, result.score, threshold, result.reason)
                except Exception:  # - never break scoring on the RCA hand-off
                    logger.exception("anomaly hook failed for %s", result.event_id)
        return Detection(
            event_id=result.event_id,
            score=result.score,
            model_version=result.model_version,
            threshold=threshold,
            anomalous=anomalous,
            audited=audited,
            reason=result.reason,
        )
