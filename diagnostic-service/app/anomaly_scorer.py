"""
anomaly_scorer.py — event anomaly scoring (SRS v1.0 §4.4, REQ-016, REQ-018).

REQ-016: compute an anomaly score for each processed event.
REQ-018: support model retraining/version swaps without downtime — the
scorer takes its model version as a constructor argument so a new
``AnomalyScorer`` instance can be swapped in (``with_version``) and
atomically repointed to, without stopping in-flight scoring.

Model (``ewma-zscore``): an online, per-stream baseline. A stream is the
event's ``(component_id or source, type)``. For every numeric field of the
event payload the scorer keeps an exponentially weighted mean and variance;
an event's deviation is the largest absolute z-score across its fields. A
second signal, *novelty*, covers events with no numeric payload: an event
``type`` that is rare (or never seen) for an otherwise well-established
source scores high.

The combined deviation ``z`` is squashed into ``[0, 1]`` with a logistic
centred on ``Z_MIDPOINT`` (z = 3 → 0.5, z ≈ 4.4 → 0.8, z = 6 → 0.95), so
higher = more anomalous and the default 0.8 threshold means "~4.4 sigma".
Each event is scored *before* it updates the baseline, so an outlier can't
mask itself. Until a stream has ``min_samples`` observations it scores 0
(warm-up): there is no baseline to deviate from yet.
"""

from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass, field
from typing import Any

Z_MIDPOINT = 3.0
MAX_Z = 50.0  # clamp so a near-zero variance can't overflow the logistic
EPSILON = 1e-9


@dataclass(frozen=True)
class AnomalyScore:
    event_id: str
    score: float
    model_version: str
    # The field (or "type" for novelty) that drove the score; None during warm-up.
    reason: str | None = None


@dataclass
class _FieldStats:
    """Exponentially weighted mean/variance (West, 1979) for one payload field."""

    count: int = 0
    mean: float = 0.0
    var: float = 0.0

    def zscore(self, x: float) -> float:
        return abs(x - self.mean) / math.sqrt(self.var + EPSILON)

    def update(self, x: float, alpha: float) -> None:
        self.count += 1
        if self.count == 1:
            self.mean = x
            return
        # Use the plain running mean/variance until the EWMA's effective
        # window (~1/alpha) is reached, so early baselines aren't over-weighted
        # towards the first sample.
        a = max(alpha, 1.0 / self.count)
        diff = x - self.mean
        incr = a * diff
        self.mean += incr
        self.var = (1.0 - a) * (self.var + diff * incr)


@dataclass
class _StreamStats:
    count: int = 0
    fields: dict[str, _FieldStats] = field(default_factory=dict)


def numeric_fields(payload: Any, prefix: str = "", depth: int = 0) -> dict[str, float]:
    """Flatten finite numeric values of ``payload`` (nested dicts, max depth 3)."""
    out: dict[str, float] = {}
    if not isinstance(payload, dict) or depth > 3:
        return out
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            if math.isfinite(value):
                out[name] = float(value)
        elif isinstance(value, dict):
            out.update(numeric_fields(value, f"{name}.", depth + 1))
    return out


def _squash(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-(min(z, MAX_Z) - Z_MIDPOINT)))


class AnomalyScorer:
    """Scores normalized events for anomalousness.

    ``model_version`` identifies the scoring model/weights in use (REQ-018);
    callers swap models by constructing a new ``AnomalyScorer`` (or calling
    ``with_version``) and atomically repointing to it, rather than mutating
    one in place. Thread-safe: request handlers and the Kafka consumer score
    concurrently.
    """

    def __init__(
        self,
        model_version: str,
        *,
        alpha: float = 0.05,
        min_samples: int = 10,
        max_streams: int = 10_000,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.model_version = model_version
        self.alpha = alpha
        self.min_samples = max(2, min_samples)
        self.max_streams = max_streams
        self._lock = threading.Lock()
        self._streams: dict[str, _StreamStats] = {}
        # source → {event type → count}, for the novelty signal
        self._type_counts: dict[str, dict[str, int]] = {}

    # ── REQ-018: version swaps ─────────────────────────────────────────────

    def with_version(self, model_version: str, *, warm_start: bool = True) -> AnomalyScorer:
        """Return a scorer for ``model_version``, optionally seeded with this one's baselines."""
        new = AnomalyScorer(
            model_version,
            alpha=self.alpha,
            min_samples=self.min_samples,
            max_streams=self.max_streams,
        )
        if warm_start:
            with self._lock:
                new._streams = copy.deepcopy(self._streams)
                new._type_counts = copy.deepcopy(self._type_counts)
        return new

    @property
    def stream_count(self) -> int:
        with self._lock:
            return len(self._streams)

    # ── REQ-016: scoring ───────────────────────────────────────────────────

    def score_event(self, event: dict[str, Any]) -> AnomalyScore:
        """Return an anomaly score in ``[0, 1]`` for ``event`` (REQ-016), then learn from it."""
        event_id = str(event.get("event_id") or "")
        source = str(event.get("component_id") or event.get("source") or "unknown")
        event_type = str(event.get("type") or "generic")
        values = numeric_fields(event.get("payload"))
        stream_key = f"{source}\x1f{event_type}"

        with self._lock:
            z, reason = self._novelty(source, event_type)
            stream = self._streams.get(stream_key)
            if stream is not None and stream.count >= self.min_samples:
                for name, x in values.items():
                    stats = stream.fields.get(name)
                    if stats is None or stats.count < self.min_samples:
                        continue
                    fz = stats.zscore(x)
                    if fz > z:
                        z, reason = fz, name
            self._learn(stream_key, source, event_type, values)

        score = _squash(z) if reason is not None else 0.0
        return AnomalyScore(event_id, round(score, 6), self.model_version, reason)

    def _novelty(self, source: str, event_type: str) -> tuple[float, str | None]:
        """Deviation for a rare ``type`` within a well-established ``source``."""
        counts = self._type_counts.get(source)
        if not counts:
            return 0.0, None
        total = sum(counts.values())
        if total < self.min_samples * 5:
            return 0.0, None
        # Laplace-smoothed frequency; p = 1/total-ish for a never-seen type.
        p = (counts.get(event_type, 0) + 1) / (total + len(counts) + 1)
        z = -2.0 * math.log10(p)  # p=1e-2 → 4, p=1e-3 → 6
        return (z, "type") if p < 0.05 else (0.0, None)

    def _learn(
        self, stream_key: str, source: str, event_type: str, values: dict[str, float]
    ) -> None:
        stream = self._streams.get(stream_key)
        if stream is None:
            if len(self._streams) >= self.max_streams:
                # Bound memory: evict the least-observed stream.
                victim = min(self._streams, key=lambda k: self._streams[k].count)
                del self._streams[victim]
            stream = self._streams[stream_key] = _StreamStats()
        stream.count += 1
        for name, x in values.items():
            stream.fields.setdefault(name, _FieldStats()).update(x, self.alpha)
        types = self._type_counts.setdefault(source, {})
        types[event_type] = types.get(event_type, 0) + 1
