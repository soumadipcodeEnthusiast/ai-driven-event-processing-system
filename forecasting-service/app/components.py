"""
components.py — monitored-component configuration with hot reload (REQ-F).

The YAML file at ``COMPONENTS_CONFIG`` defines, per component, the PromQL
query and breach threshold of each metric. The file is re-read whenever its
mtime changes, so thresholds can be changed without a restart. Runtime
overrides from ``PUT /thresholds/{component_id}`` are layered on top of the
file values and survive file reloads.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_MINUTES = 180
MIN_HISTORY_MINUTES = 30


class ConfigError(ValueError):
    """Raised when the components file is malformed."""


class UnknownComponentError(KeyError):
    pass


class UnknownMetricError(KeyError):
    pass


@dataclass(frozen=True)
class MetricSpec:
    name: str
    query: str
    threshold: float
    description: str = ""


@dataclass(frozen=True)
class ComponentConfig:
    component_id: str
    metrics: dict[str, MetricSpec] = field(default_factory=dict)
    enabled: bool = True
    history_minutes: int = DEFAULT_HISTORY_MINUTES


def parse_components(raw: Any) -> dict[str, ComponentConfig]:
    """Validate and convert the parsed YAML document into ComponentConfig objects."""
    if not isinstance(raw, dict) or not isinstance(raw.get("components"), dict):
        raise ConfigError("components file must contain a top-level 'components' mapping")
    defaults = raw.get("defaults") or {}
    default_history = int(defaults.get("history_minutes", DEFAULT_HISTORY_MINUTES))
    out: dict[str, ComponentConfig] = {}
    for comp_id, comp in raw["components"].items():
        if not isinstance(comp, dict):
            raise ConfigError(f"component {comp_id!r} must be a mapping")
        enabled = bool(comp.get("enabled", True))
        raw_metrics = comp.get("metrics") or {}
        if not isinstance(raw_metrics, dict) or (enabled and not raw_metrics):
            raise ConfigError(f"enabled component {comp_id!r} needs a non-empty 'metrics' mapping")
        metrics: dict[str, MetricSpec] = {}
        for metric_name, spec in raw_metrics.items():
            if not isinstance(spec, dict) or "query" not in spec or "threshold" not in spec:
                raise ConfigError(f"{comp_id}.{metric_name}: 'query' and 'threshold' are required")
            metrics[str(metric_name)] = MetricSpec(
                name=str(metric_name),
                query=str(spec["query"]).strip(),
                threshold=float(spec["threshold"]),
                description=str(spec.get("description", "")),
            )
        out[str(comp_id)] = ComponentConfig(
            component_id=str(comp_id),
            metrics=metrics,
            enabled=enabled,
            history_minutes=max(
                MIN_HISTORY_MINUTES, int(comp.get("history_minutes", default_history))
            ),
        )
    return out


class ComponentConfigStore:
    """Thread-safe, mtime-hot-reloaded view of ``components.yaml`` plus overrides."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._mtime: float | None = None
        self._components: dict[str, ComponentConfig] = {}
        self._overrides: dict[str, dict[str, float]] = {}
        self.reload(force=True)

    @property
    def path(self) -> Path:
        return self._path

    def reload(self, force: bool = False) -> bool:
        """Re-read the file if its mtime changed. Returns True if reloaded.

        A malformed file is logged and ignored (the previous config is kept).
        """
        with self._lock:
            try:
                mtime = os.stat(self._path).st_mtime
            except OSError as exc:
                if force:
                    logger.error("components config %s not readable: %s", self._path, exc)
                return False
            if not force and mtime == self._mtime:
                return False
            try:
                with open(self._path, encoding="utf-8") as fh:
                    components = parse_components(yaml.safe_load(fh))
            except (OSError, yaml.YAMLError, ConfigError, ValueError, TypeError) as exc:
                logger.error("invalid components config %s, keeping previous: %s", self._path, exc)
                self._mtime = mtime
                return False
            self._components = components
            self._mtime = mtime
            if self._overrides and not force:
                logger.info(
                    "components config reloaded; runtime threshold overrides still apply: %s",
                    self._overrides,
                )
            logger.info("loaded components config %s (%d components)", self._path, len(components))
            return True

    # ── read API (each call checks for file changes) ───────────────────────

    def components(self, include_disabled: bool = False) -> dict[str, ComponentConfig]:
        self.reload()
        with self._lock:
            return {
                k: self._apply_overrides(v)
                for k, v in self._components.items()
                if include_disabled or v.enabled
            }

    def get(self, component_id: str) -> ComponentConfig:
        comps = self.components(include_disabled=True)
        if component_id not in comps:
            raise UnknownComponentError(component_id)
        return comps[component_id]

    def thresholds(self) -> dict[str, dict[str, float]]:
        return {
            cid: {m: spec.threshold for m, spec in comp.metrics.items()}
            for cid, comp in self.components(include_disabled=True).items()
        }

    # ── write API (REQ-F) ──────────────────────────────────────────────────

    def set_thresholds(self, component_id: str, values: dict[str, float]) -> dict[str, float]:
        """Apply runtime threshold overrides; returns the effective thresholds."""
        comp = self.get(component_id)
        unknown = [m for m in values if m not in comp.metrics]
        if unknown:
            raise UnknownMetricError(", ".join(sorted(unknown)))
        with self._lock:
            self._overrides.setdefault(component_id, {}).update(
                {k: float(v) for k, v in values.items()}
            )
        logger.info(
            "threshold override for %s: %s (wins over %s until restart)",
            component_id,
            values,
            self._path,
        )
        return {m: s.threshold for m, s in self.get(component_id).metrics.items()}

    def _apply_overrides(self, comp: ComponentConfig) -> ComponentConfig:
        ov = self._overrides.get(comp.component_id)
        if not ov:
            return comp
        metrics = {
            name: MetricSpec(spec.name, spec.query, ov.get(name, spec.threshold), spec.description)
            for name, spec in comp.metrics.items()
        }
        return ComponentConfig(comp.component_id, metrics, comp.enabled, comp.history_minutes)
