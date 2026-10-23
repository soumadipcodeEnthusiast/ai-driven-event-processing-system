"""
incident_trigger.py — anomalies → GraphRAG diagnosis → incident playbook on Kafka.

The detector calls :meth:`IncidentTrigger.submit` for every anomalous event.
Per component, the first anomaly queues a diagnosis; anomalies arriving in
the next ``batch_seconds`` join it, so a burst becomes one playbook. After a
diagnosis starts the component is in cooldown for ``cooldown_seconds``. A
single worker thread runs the diagnoses (each may take a minute on a local
LLM) and publishes every playbook to the ``incident-playbooks`` topic.

``submit`` never blocks and never raises into the scoring path.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.audit_publisher import Producer, _kafka_producer
from app.dependency_graph_client import UnknownNodeError
from app.diagnostic_engine import DiagnosisResult, DiagnosticEngine
from app.metrics import INCIDENTS, PLAYBOOKS_PUBLISHED
from app.playbook_generator import utc_iso

logger = logging.getLogger(__name__)

MAX_ANOMALIES_PER_INCIDENT = 20


def _severity(score: float) -> str:
    if score >= 0.99:
        return "critical"
    if score >= 0.95:
        return "high"
    return "medium"


def anomaly_context(
    event: dict[str, Any], score: float, threshold: float, reason: str | None
) -> dict[str, Any]:
    """The prompt/fallback view of one anomalous event (``kind: event_anomaly``)."""
    raw = event.get("payload")
    payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
    observed = payload.get(reason) if reason and reason != "type" else None
    return {
        "kind": "event_anomaly",
        "metric_name": reason or "score",
        "severity": _severity(score),
        "score": round(score, 4),
        "threshold": threshold,
        "observed_value": observed,
        "event_id": event.get("event_id"),
        "source": event.get("source"),
        "type": event.get("type"),
        "event_timestamp": event.get("timestamp"),
        "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class PlaybookPublisher:
    """Publishes finished playbooks to Kafka (keyed by component)."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        producer_factory: Callable[[str], Producer] = _kafka_producer,
    ) -> None:
        self.topic = topic
        self._bootstrap = bootstrap_servers
        self._factory = producer_factory
        self._producer: Producer | None = None

    def publish(
        self, result: DiagnosisResult, trigger: str, anomalies: list[dict[str, Any]] | None = None
    ) -> None:
        pb = result.playbook
        record = {
            **pb.to_dict(),
            "trigger": trigger,
            "anomalies": anomalies or [],
            "outcome": result.outcome,
            "graph_backend": result.details.get("graph_backend"),
            "unhealthy_dependencies": result.details.get("unhealthy_dependencies", []),
            "latency_seconds": result.details.get("latency_seconds"),
        }

        def on_delivery(err: Any, _msg: Any) -> None:
            PLAYBOOKS_PUBLISHED.labels(outcome="failed" if err else "ok").inc()
            if err:
                logger.error("playbook %s not delivered: %s", pb.playbook_id, err)

        try:
            if self._producer is None:
                self._producer = self._factory(self._bootstrap)
            self._producer.produce(
                self.topic,
                value=json.dumps(record, separators=(",", ":")).encode(),
                key=pb.component_id.encode(),
                on_delivery=on_delivery,
            )
            self._producer.poll(0)
        except Exception as exc:  # - the playbook is already stored; Kafka is best effort
            PLAYBOOKS_PUBLISHED.labels(outcome="failed").inc()
            logger.error("playbook %s not published: %s", pb.playbook_id, exc)

    def poll(self) -> None:
        if self._producer is not None:
            self._producer.poll(0)

    def close(self, timeout: float = 5.0) -> None:
        if self._producer is not None:
            self._producer.flush(timeout)
            self._producer = None


class IncidentTrigger:
    def __init__(
        self,
        engine: DiagnosticEngine,
        publisher: PlaybookPublisher | None,
        *,
        cooldown_seconds: float = 300.0,
        batch_seconds: float = 5.0,
        max_queue: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.publisher = publisher
        self.cooldown = cooldown_seconds
        self.batch = batch_seconds
        self._clock = clock
        self._queue: queue.Queue[tuple[str, float]] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._last: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)

    # ── called from the scoring path ───────────────────────────────────────

    def submit(self, event: dict[str, Any], anomaly: dict[str, Any]) -> str:
        """Queue (or batch, or skip) a diagnosis for the event's component."""
        component = str(event.get("component_id") or event.get("source") or "").strip()
        if not component:
            return "ignored"
        now = self._clock()
        with self._lock:
            if component in self._pending:
                batch = self._pending[component]
                if len(batch) < MAX_ANOMALIES_PER_INCIDENT:
                    batch.append(anomaly)
                return "batched"
            last = self._last.get(component)
            if last is not None and now - last < self.cooldown:
                outcome = "cooldown"
            else:
                try:
                    self._queue.put_nowait((component, now))
                except queue.Full:
                    outcome = "dropped"
                else:
                    self._pending[component] = [anomaly]
                    outcome = "queued"
        INCIDENTS.labels(outcome=outcome).inc()
        return outcome

    # ── worker ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="incident-trigger", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self.publisher is not None:
            self.publisher.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                component, queued_at = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self.publisher is not None:
                    self.publisher.poll()
                continue
            # let the rest of the burst arrive
            self._stop.wait(max(0.0, queued_at + self.batch - self._clock()))
            if self._stop.is_set():
                return
            self.diagnose(component)

    def diagnose(self, component: str) -> DiagnosisResult | None:
        with self._lock:
            anomalies = self._pending.pop(component, [])
            self._last[component] = self._clock()
        try:
            result = self.engine.run(component, None, anomalies)
        except UnknownNodeError:
            INCIDENTS.labels(outcome="unknown_component").inc()
            logger.warning("anomaly on %s, which is not in the dependency graph", component)
            return None
        except Exception:
            INCIDENTS.labels(outcome="failed").inc()
            logger.exception("automatic diagnosis of %s failed", component)
            return None
        INCIDENTS.labels(outcome="done").inc()
        pb = result.playbook
        self.recent.appendleft(
            {
                "component_id": component,
                "playbook_id": pb.playbook_id,
                "generated_by": pb.generated_by,
                "anomalies": len(anomalies),
                "generated_at": utc_iso(pb.generated_at),
            }
        )
        logger.info(
            "event=incident_playbook component=%s playbook=%s by=%s anomalies=%d",
            component,
            pb.playbook_id,
            pb.generated_by,
            len(anomalies),
        )
        if self.publisher is not None:
            self.publisher.publish(result, trigger="anomaly", anomalies=anomalies)
        return result
