from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.diagnostic_engine import DiagnosticEngine
from app.llm_client import LLMClient
from app.main import create_app

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = SERVICE_ROOT.parent / "contracts"
LLM_URL = "http://llm.test"
ALERT_ID = "al-0123456789ab"

VALID_LLM_JSON = """{
  "summary": "CPU saturation predicted on ingestion-service",
  "root_cause_hypothesis": "Consumer backlog on kafka drives CPU",
  "steps": [
    {"rank": 2, "title": "Check consumer lag", "command": "kafka-consumer-groups --describe",
     "expected_outcome": "Lag identified"},
    {"rank": 1, "title": "Check pod CPU", "command": "kubectl top pods",
     "expected_outcome": "Hot pods found"},
    {"rank": 3, "title": "Scale out", "command": "kubectl scale deploy/ingestion-service",
     "expected_outcome": "CPU below threshold"}
  ]
}"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        playbook_store_dir=str(tmp_path / "playbooks"),
        llm_provider="none",
        llm_backoff_seconds=0,
    )


@pytest.fixture
def make_engine(settings: Settings) -> Callable[..., DiagnosticEngine]:
    def factory(provider: str = "none") -> DiagnosticEngine:
        llm = None
        if provider != "none":
            llm = LLMClient(provider, LLM_URL, "test-model", api_key="k", sleep=lambda _s: None)
        return DiagnosticEngine(settings, llm_client=llm)

    return factory


@pytest.fixture
def client_factory(
    settings: Settings, make_engine: Callable[..., DiagnosticEngine]
) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(provider: str = "none") -> TestClient:
        c = TestClient(create_app(settings, make_engine(provider)))
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)
