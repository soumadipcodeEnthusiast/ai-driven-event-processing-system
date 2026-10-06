from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.diagnostic_engine import DiagnosticEngine
from app.llm_client import LLMClient, LLMError
from app.main import RateLimiter, create_app
from tests.conftest import ALERT_ID, CONTRACTS, LLM_URL, SERVICE_ROOT, VALID_LLM_JSON

ANOMALY = {
    "metric_name": "cpu_utilisation",
    "predicted_value": 0.83,
    "threshold": 0.8,
    "predicted_breach_time": "2026-01-01T12:45:00Z",
    "severity": "low",
}


def _llm(provider: str) -> LLMClient:
    return LLMClient(provider, LLM_URL, "m", api_key="secret", sleep=lambda _s: None)


# ─── providers ──────────────────────────────────────────────────────────────


@respx.mock
def test_ollama_request_shape() -> None:
    route = respx.post(f"{LLM_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": VALID_LLM_JSON, "done": True})
    )
    assert _llm("ollama").complete("p", "sys") == VALID_LLM_JSON
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is False and body["format"] == "json" and body["prompt"] == "p"


@respx.mock
def test_anthropic_request_shape() -> None:
    route = respx.post(f"{LLM_URL}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "{}"}],
            },
        )
    )
    assert _llm("anthropic").complete("p", "sys") == "{}"
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "secret"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == "m" and body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "p"}]
    assert "temperature" not in body


@respx.mock
def test_anthropic_refusal_is_error() -> None:
    respx.post(f"{LLM_URL}/v1/messages").mock(
        return_value=httpx.Response(200, json={"stop_reason": "refusal", "content": []})
    )
    with pytest.raises(LLMError, match="refused"):
        _llm("anthropic").complete("p")


@respx.mock
def test_openai_compatible_request_shape() -> None:
    route = respx.post(f"{LLM_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    c = LLMClient("openai", LLM_URL + "/v1", "gpt", api_key="k")
    assert c.complete("p", "sys") == "{}"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer k"
    assert json.loads(req.content)["messages"][0] == {"role": "system", "content": "sys"}


@respx.mock
def test_retry_with_backoff_on_5xx_and_timeout() -> None:
    route = respx.post(f"{LLM_URL}/api/generate").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(503),
            httpx.Response(200, json={"response": "ok"}),
        ]
    )
    sleeps: list[float] = []
    c = LLMClient("ollama", LLM_URL, "m", sleep=sleeps.append, backoff_seconds=0.5)
    assert c.complete("p") == "ok"
    assert route.call_count == 3 and sleeps == [0.5, 1.0]


@respx.mock
def test_no_retry_on_4xx_and_gives_up_after_attempts() -> None:
    route = respx.post(f"{LLM_URL}/api/generate").mock(return_value=httpx.Response(400))
    with pytest.raises(LLMError):
        _llm("ollama").complete("p")
    assert route.call_count == 1
    route.mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError, match="3 attempts"):
        _llm("ollama").complete("p")


# ─── engine ─────────────────────────────────────────────────────────────────


def test_build_prompt_contains_context(make_engine: Callable[..., DiagnosticEngine]) -> None:
    eng = make_engine()
    ctx = eng.retrieve_context("ingestion-service", [ANOMALY])
    prompt = eng.build_prompt("ingestion-service", ctx)
    assert "`ingestion-service`" in prompt
    assert "| kafka | broker | healthy | consumes_from | downstream | 1 |" in prompt
    assert '"metric_name": "cpu_utilisation"' in prompt
    assert "single JSON object" in prompt and '"root_cause_hypothesis"' in prompt
    assert ctx["node_metadata"]["kafka"]["owner_team"] == "platform-infra"


# ─── API ────────────────────────────────────────────────────────────────────


def test_ops(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").json() == {"status": "ready"}
    assert "diagnoses_total" in c.get("/metrics").text


def test_diagnose_fallback_when_llm_none(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    r = c.post("/diagnose/ingestion-service", json={"alert_id": ALERT_ID, "anomalies": [ANOMALY]})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"component_id", "playbook_id", "summary", "details"}
    assert body["playbook_id"].startswith("pb-ingestion-service-")
    assert body["details"]["outcome"] == "fallback"
    assert body["details"]["generated_by"] == "fallback:rule-based"
    assert len(body["details"]["steps"]) >= 3
    assert 'diagnoses_total{outcome="fallback"}' in c.get("/metrics").text

    pb = c.get(f"/playbook/{body['playbook_id']}").json()
    assert pb["alert_id"] == ALERT_ID and pb["generated_by"] == "fallback:rule-based"
    assert [s["rank"] for s in pb["steps"]] == list(range(1, len(pb["steps"]) + 1))
    schema_path = CONTRACTS / "incident-playbook.schema.json"
    if schema_path.exists():
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(pb, json.loads(schema_path.read_text()))


def test_diagnose_without_body(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().post("/diagnose/kafka")
    assert r.status_code == 200 and r.json()["details"]["alert_id"] is None


@respx.mock
def test_diagnose_with_llm_ok_and_idempotent(client_factory: Callable[..., TestClient]) -> None:
    route = respx.post(f"{LLM_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": VALID_LLM_JSON})
    )
    c = client_factory("ollama")
    body = {"alert_id": ALERT_ID, "anomalies": [ANOMALY]}
    first = c.post("/diagnose/ingestion-service", json=body).json()
    assert first["details"]["outcome"] == "ok"
    assert first["details"]["generated_by"] == "ollama:test-model"
    assert first["details"]["steps"][0]["title"] == "Check pod CPU"
    second = c.post("/diagnose/ingestion-service", json=body).json()
    assert second["playbook_id"] == first["playbook_id"]
    assert second["details"]["cached"] is True
    assert route.call_count == 1
    assert len(c.get("/playbooks", params={"alert_id": ALERT_ID}).json()) == 1
    metrics = c.get("/metrics").text
    assert 'llm_request_seconds_count{provider="ollama"}' in metrics
    assert "playbooks_stored_total" in metrics


@respx.mock
def test_diagnose_malformed_llm_output_falls_back(
    client_factory: Callable[..., TestClient],
) -> None:
    respx.post(f"{LLM_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "Sorry, I am not sure. {oops"})
    )
    c = client_factory("ollama")
    body = c.post("/diagnose/graph-db", json={"anomalies": [ANOMALY]}).json()
    assert body["details"]["outcome"] == "fallback"
    assert "llm_error" in body["details"]
    assert 'llm_failures_total{provider="ollama"}' in c.get("/metrics").text


@respx.mock
def test_diagnose_llm_down_falls_back(client_factory: Callable[..., TestClient]) -> None:
    respx.post(f"{LLM_URL}/api/generate").mock(side_effect=httpx.ConnectError("refused"))
    body = client_factory("ollama").post("/diagnose/kafka").json()
    assert body["details"]["outcome"] == "fallback"


def test_diagnose_errors(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    assert c.post("/diagnose/unknown-thing").status_code == 404
    assert c.post("/diagnose/kafka", json={"alert_id": "bad"}).status_code == 422


def test_playbook_endpoints(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    a = c.post("/diagnose/kafka").json()["playbook_id"]
    b = c.post("/diagnose/llm", json={"alert_id": ALERT_ID}).json()["playbook_id"]

    md = c.get(f"/playbook/{a}", params={"format": "markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert md.text.startswith(f"# Incident playbook `{a}`")
    assert c.get(f"/playbook/{a}", params={"format": "xml"}).status_code == 422
    assert c.get("/playbook/pb-kafka-00000000").status_code == 404
    assert c.get("/playbook/..%2F..%2Fetc%2Fpasswd").status_code == 404

    items = c.get("/playbooks").json()
    assert isinstance(items, list) and {i["playbook_id"] for i in items} == {a, b}
    assert items[0]["generated_at"] >= items[1]["generated_at"]
    assert all("steps" in i for i in items)
    assert [
        i["playbook_id"] for i in c.get("/playbooks", params={"component_id": "llm"}).json()
    ] == [b]
    assert [
        i["playbook_id"] for i in c.get("/playbooks", params={"alert_id": ALERT_ID}).json()
    ] == [b]


def test_no_llm_series_when_provider_none(tmp_path: object) -> None:
    """With LLM_PROVIDER=none no llm_* samples may be exported (SRE fallback-ratio alert)."""
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import create_app\n"
        "with TestClient(create_app()) as c:\n"
        "    c.post('/diagnose/kafka')\n"
        "    print(c.get('/metrics').text)\n"
    )
    env = {**os.environ, "LLM_PROVIDER": "none", "PLAYBOOK_STORE_DIR": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=SERVICE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    samples = [ln for ln in out.splitlines() if ln.startswith("llm_")]
    assert samples == []
    assert 'diagnoses_total{outcome="fallback"} 1.0' in out


def test_diagnose_rate_limited_with_retry_after(
    settings: Settings, make_engine: Callable[..., DiagnosticEngine]
) -> None:
    settings.diagnose_rate_limit_per_minute = 2
    with TestClient(create_app(settings, make_engine())) as c:
        assert c.post("/diagnose/kafka").status_code == 200
        assert c.post("/diagnose/kafka").status_code == 200
        r = c.post("/diagnose/kafka")
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1
        metrics = c.get("/metrics").text
        limited = [
            ln for ln in metrics.splitlines() if ln.startswith("diagnose_rate_limited_total ")
        ]
        assert limited and float(limited[0].split()[1]) >= 1  # counters are process-global


def test_rate_limiter_refills_over_time() -> None:
    t = [0.0]
    limiter = RateLimiter(60, clock=lambda: t[0])  # 1 token per second, burst 60
    for _ in range(60):
        assert limiter.acquire() == 0
    assert limiter.acquire() == pytest.approx(1.0)
    t[0] += 1.0
    assert limiter.acquire() == 0
    assert RateLimiter(0).acquire() == 0  # disabled


def test_alert_locks_are_released(make_engine: Callable[..., DiagnosticEngine]) -> None:
    engine = make_engine()
    engine.run("kafka", ALERT_ID)
    engine.run("kafka", ALERT_ID)  # cached, still idempotent
    assert engine._alert_locks == {}
    assert len(engine.playbooks.list(alert_id=ALERT_ID)) == 1
