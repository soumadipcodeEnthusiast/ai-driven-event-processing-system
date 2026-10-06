"""
config.py — runtime configuration for diagnostic-service (docs/CONTRACTS.md §5).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent

LLM_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (default LLM_API_URL, default LLM_MODEL)
    "ollama": ("http://ollama:11434", "llama3.1"),
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
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    graph_backend: Literal["networkx", "neo4j"] = "networkx"
    graph_seed_path: str = "data/dependency_graph.json"
    graph_db_url: str = "bolt://neo4j:7687"
    graph_db_user: str = "neo4j"
    graph_db_password: str = ""

    llm_provider: Literal["ollama", "anthropic", "openai", "none"] = "none"
    llm_api_url: str = ""  # empty → provider default (LLM_DEFAULTS)
    llm_model: str = ""  # empty → provider default (LLM_DEFAULTS)
    llm_api_key: str = ""
    llm_timeout_seconds: float = 60.0
    llm_max_attempts: int = 3
    llm_backoff_seconds: float = 0.5

    playbook_store_dir: str = "/data/playbooks"
    # Retention: oldest playbooks beyond either limit are deleted (0 disables a limit).
    playbook_max_count: int = 1000
    playbook_retention_days: float = 30.0
    # /diagnose token bucket: sustained requests per minute, burst = same value (0 disables).
    diagnose_rate_limit_per_minute: int = 60
    log_level: str = "INFO"

    def llm_url(self) -> str:
        return self.llm_api_url or LLM_DEFAULTS.get(self.llm_provider, ("", ""))[0]

    def llm_model_name(self) -> str:
        return self.llm_model or LLM_DEFAULTS.get(self.llm_provider, ("", ""))[1]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
