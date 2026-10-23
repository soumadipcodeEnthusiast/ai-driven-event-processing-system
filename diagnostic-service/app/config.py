"""
config.py — runtime configuration for diagnostic-service (docs/CONTRACTS.md §5).

All values come from environment variables. Two features share the service:

* anomaly detection (SRS v1.0 §4.4, REQ-016 to REQ-020), and
* GraphRAG root-cause analysis (docs/adr/0010): a 2-hop dependency subgraph
  from Neo4j is retrieved for the anomalous component, rendered into a prompt
  and sent to an LLM (Ollama locally), which writes an incident playbook. A
  rule-based playbook is produced whenever the LLM is off, fails or answers
  with something unusable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent

LLM_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (default LLM_API_URL, default LLM_MODEL)
    "ollama": ("http://ollama:11434", "llama3.2:3b"),
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-5-5"),
    "openai": ("https://api.openai.com", "gpt-4o-mini"),
}


def resolve_path(path: str) -> Path:
    """Absolute as-is; relative against CWD if it exists there, else the service root."""
    p = Path(path)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    return SERVICE_ROOT / p


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=False, protected_namespaces=()
    )

    # REQ-018: current scoring model version; rotated at runtime via PUT /model
    # (or by redeploying with a new MODEL_VERSION).
    model_version: str = "ewma-zscore-1.0"

    # REQ-016: scorer tuning — EWMA smoothing factor and per-stream warm-up.
    scorer_alpha: float = Field(default=0.05, gt=0.0, le=1.0)
    scorer_min_samples: int = Field(default=10, ge=2)

    # REQ-017 / REQ-020: initial anomaly-score threshold; adjustable at runtime
    # through ThresholdManager (PUT /thresholds), not just at startup.
    anomaly_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    # REQ-019: Kafka connection for the anomaly audit topic.
    kafka_bootstrap_servers: str = "kafka:9092"
    anomaly_audit_topic: str = "anomaly-audit"

    # REQ-016: score every event on normalized-events, not only POST /score.
    event_consumer_enabled: bool = False
    event_input_topic: str = "normalized-events"
    event_consumer_group: str = "diagnostic-anomaly-scorer"

    # ── GraphRAG root-cause analysis (docs/adr/0010) ───────────────────────
    # Dependency graph: Neo4j in compose/k8s; the JSON seed loads an empty
    # database and is the in-memory fallback when Neo4j is unreachable.
    graph_backend: Literal["networkx", "neo4j"] = "networkx"
    graph_seed_path: str = "data/dependency_graph.json"
    graph_db_url: str = "bolt://neo4j:7687"
    graph_db_user: str = "neo4j"
    graph_db_password: str = ""

    # LLM that writes the playbook from the graph context; none = rule-based only.
    llm_provider: Literal["ollama", "anthropic", "openai", "none"] = "none"
    llm_api_url: str = ""  # empty → provider default (LLM_DEFAULTS)
    llm_model: str = ""  # empty → provider default (LLM_DEFAULTS)
    llm_api_key: str = ""
    llm_timeout_seconds: float = 300.0
    llm_max_attempts: int = 1
    llm_backoff_seconds: float = 0.5

    playbook_store_dir: str = "/data/playbooks"
    # Retention: oldest playbooks beyond either limit are deleted (0 disables a limit).
    playbook_max_count: int = 1000
    playbook_retention_days: float = 30.0
    # /diagnose token bucket: sustained requests per minute, burst = same value (0 disables).
    diagnose_rate_limit_per_minute: int = 60

    # Anomalies trigger a diagnosis automatically: one per component per
    # cooldown window, after a short wait so a burst lands in one playbook.
    incident_auto_diagnose: bool = True
    incident_cooldown_seconds: float = Field(default=300.0, ge=0.0)
    incident_batch_seconds: float = Field(default=5.0, ge=0.0)
    incident_playbook_topic: str = "incident-playbooks"

    # REQ-046: OAuth 2.0 bearer-token validation (see app/auth.py).
    oauth2_enabled: bool = False
    oauth2_jwks_url: str = ""
    oauth2_issuer: str = ""
    oauth2_audience: str = ""

    log_level: str = "INFO"

    def llm_url(self) -> str:
        return self.llm_api_url or LLM_DEFAULTS.get(self.llm_provider, ("", ""))[0]

    def llm_model_name(self) -> str:
        return self.llm_model or LLM_DEFAULTS.get(self.llm_provider, ("", ""))[1]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
