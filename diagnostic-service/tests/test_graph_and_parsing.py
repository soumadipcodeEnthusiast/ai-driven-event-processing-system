from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from app.config import resolve_path
from app.dependency_graph_client import (
    DependencyGraphClient,
    NetworkXDependencyGraph,
    UnknownNodeError,
)
from app.fallback import build_fallback
from app.playbook_generator import (
    IncidentPlaybook,
    PlaybookGenerator,
    PlaybookParseError,
    md_code_block,
    parse_llm_response,
)
from tests.conftest import ALERT_ID, CONTRACTS, VALID_LLM_JSON

SEED = resolve_path("data/dependency_graph.json")
ALL_IDS = {
    "ingestion-service",
    "forecasting-service",
    "diagnostic-service",
    "kafka",
    "zookeeper",
    "prometheus",
    "graph-db",
    "llm",
}


def _client() -> DependencyGraphClient:
    return DependencyGraphClient(SEED)


# ─── graph ──────────────────────────────────────────────────────────────────


def test_seed_contains_all_component_ids_and_matches_schema() -> None:
    seed = json.loads(SEED.read_text())
    assert {n["id"] for n in seed["nodes"]} == ALL_IDS
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = CONTRACTS / "dependency-graph-seed.schema.json"
    if schema_path.exists():
        jsonschema.validate(seed, json.loads(schema_path.read_text()))


def test_two_hop_traversal_both_directions() -> None:
    deps = {d["node_id"]: d for d in _client().get_dependencies("ingestion-service")}
    assert deps["kafka"] == {
        "node_id": "kafka",
        "node_type": "broker",
        "health_status": "healthy",
        "edge_type": "consumes_from",
        "depth": 1,
        "direction": "downstream",
        "via": "ingestion-service",
    }
    assert deps["zookeeper"]["depth"] == 2 and deps["zookeeper"]["via"] == "kafka"
    assert deps["zookeeper"]["edge_type"] == "depends_on"
    assert deps["prometheus"]["direction"] == "upstream" and deps["prometheus"]["depth"] == 1
    assert deps["forecasting-service"]["depth"] == 2
    assert "ingestion-service" not in deps
    # 3 hops away (forecasting → diagnostic → llm is 2 from forecasting, 4 from ingestion)
    assert "llm" not in deps and "graph-db" not in deps


def test_traversal_from_forecasting() -> None:
    deps = {d["node_id"]: d for d in _client().get_dependencies("forecasting-service")}
    assert {k for k, v in deps.items() if v["depth"] == 1} == {"prometheus", "diagnostic-service"}
    assert deps["llm"]["depth"] == 2 and deps["llm"]["edge_type"] == "calls"
    assert all(d["depth"] in (1, 2) for d in deps.values())


def test_unknown_component_and_metadata() -> None:
    c = _client()
    with pytest.raises(UnknownNodeError):
        c.get_dependencies("nope")
    meta = c.get_node_metadata("kafka")
    assert set(meta) == {"version", "owner_team", "sla_tier", "last_deployed_at"}


def test_neo4j_unavailable_falls_back_to_seed() -> None:
    c = DependencyGraphClient(SEED, backend="neo4j", db_url="bolt://127.0.0.1:1")
    assert c.backend == "networkx"
    assert c.has_node("kafka")


def test_context_under_one_second_for_500_nodes() -> None:
    rng = random.Random(3)
    nodes = [
        {
            "id": f"n{i}",
            "node_type": "service",
            "health_status": "healthy",
            "metadata": {
                "version": "1",
                "owner_team": "t",
                "sla_tier": "tier-2",
                "last_deployed_at": "2026-01-01T00:00:00Z",
            },
        }
        for i in range(500)
    ]
    edges = [
        {
            "source": f"n{rng.randrange(500)}",
            "target": f"n{rng.randrange(500)}",
            "edge_type": "calls",
        }
        for _ in range(2000)
    ]
    g = DependencyGraphClient("", graph=NetworkXDependencyGraph({"nodes": nodes, "edges": edges}))
    t0 = time.perf_counter()
    for i in range(20):
        deps = g.get_dependencies(f"n{i}")
        _ = [g.get_node_metadata(d["node_id"]) for d in deps]
    assert (time.perf_counter() - t0) / 20 < 1.0


# ─── LLM output parsing ─────────────────────────────────────────────────────


def test_parse_valid_json_reranks() -> None:
    out = parse_llm_response(VALID_LLM_JSON)
    assert [s["rank"] for s in out["steps"]] == [1, 2, 3]
    assert out["steps"][0]["title"] == "Check pod CPU"
    assert out["root_cause_hypothesis"].startswith("Consumer backlog")


def test_parse_code_fence_and_prose() -> None:
    text = "Sure! Here is the playbook:\n```json\n" + VALID_LLM_JSON + "\n```\nGood luck."
    assert len(parse_llm_response(text)["steps"]) == 3
    text2 = "Analysis follows. " + VALID_LLM_JSON + " Hope this helps {not json}"
    assert len(parse_llm_response(text2)["steps"]) == 3


def test_parse_aliases_list_and_missing_fields() -> None:
    text = json.dumps(
        [
            {"action": "A", "cmd": "a", "expected": "ok"},
            {"title": "B"},
            "C as plain string",
            {"rank": "x", "title": "D", "command": ["d1", "d2"]},
        ]
    )
    out = parse_llm_response(text)
    assert [s["title"] for s in out["steps"]] == ["A", "B", "C as plain string", "D"]
    assert out["steps"][3]["command"] == "d1\nd2"
    assert all(s["expected_outcome"] for s in out["steps"])
    assert out["summary"] == ""


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I cannot help with that.",
        "{ broken json",
        '{"summary": "x", "steps": [{"title": "only one"}, {"title": "two"}]}',
        '{"summary": "x", "steps": "not a list"}',
        '{"steps": [{"command": "no title"}, {}, {}, {}]}',
    ],
)
def test_parse_malformed_raises(text: str) -> None:
    with pytest.raises(PlaybookParseError):
        parse_llm_response(text)


def test_parse_caps_steps() -> None:
    steps = [{"title": f"s{i}", "rank": i} for i in range(25)]
    assert len(parse_llm_response(json.dumps({"steps": steps}))["steps"]) == 10


# ─── playbook store & render ────────────────────────────────────────────────


def test_playbook_persistence_round_trip(tmp_path: Path) -> None:
    store = tmp_path / "pb"
    gen = PlaybookGenerator(store)
    pb = gen.generate("kafka", VALID_LLM_JSON, alert_id=ALERT_ID, generated_by="ollama:llama3.1")
    assert (store / f"{pb.playbook_id}.json").is_file()
    assert not list(store.glob(".tmp-*"))

    reloaded = PlaybookGenerator(store)
    again = reloaded.get(pb.playbook_id)
    assert again is not None and again.to_dict() == pb.to_dict()
    assert reloaded.find_by_alert(ALERT_ID) is not None
    assert reloaded.list(component_id="kafka")[0].playbook_id == pb.playbook_id
    assert reloaded.list(component_id="llm") == []

    schema_path = CONTRACTS / "incident-playbook.schema.json"
    jsonschema = pytest.importorskip("jsonschema")
    if schema_path.exists():
        jsonschema.validate(
            json.loads((store / f"{pb.playbook_id}.json").read_text()),
            json.loads(schema_path.read_text()),
        )


def test_get_rejects_invalid_ids(tmp_path: Path) -> None:
    gen = PlaybookGenerator(tmp_path)
    (tmp_path / "secret.json").write_text("{}")
    for bad in ("../secret", "secret", "pb-kafka-XYZ", "pb-kafka-0123abcd/../../x", "pb--12345678"):
        assert gen.get(bad) is None


def test_store_unwritable_falls_back_to_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    gen = PlaybookGenerator(blocker / "sub")
    assert not gen.persistent
    pb = gen.generate("kafka", VALID_LLM_JSON, generated_by="openai:x")
    assert gen.get(pb.playbook_id) is pb


def test_render_markdown_escapes_untrusted_text() -> None:
    pb = IncidentPlaybook(
        playbook_id="pb-kafka-0123abcd",
        component_id="kafka",
        summary="<script>alert(1)</script> **bold**",
        root_cause_hypothesis="[click](http://evil)",
        steps=[
            {
                "rank": 1,
                "title": "# not a heading",
                "command": "echo ```; rm -rf /",
                "expected_outcome": "ok",
            },
            {"rank": 2, "title": "two", "command": "", "expected_outcome": "fine"},
            {"rank": 3, "title": "three", "command": "ls", "expected_outcome": "listed"},
        ],
        alert_id=ALERT_ID,
    )
    md = pb.render()
    assert "# Incident playbook `pb-kafka-0123abcd`" in md
    assert "<script>" not in md and "&lt;script&gt;" in md
    assert "\\*\\*bold\\*\\*" in md and "\\[click\\]" in md
    assert "### 1. \\# not a heading" in md
    assert "````text\necho ```; rm -rf /\n````" in md
    assert "**Expected outcome:** listed" in md
    assert md.index("### 1.") < md.index("### 2.") < md.index("### 3.")


def test_md_code_block_fence() -> None:
    assert md_code_block("ls").startswith("```text")


# ─── rule-based fallback ────────────────────────────────────────────────────


def test_fallback_prioritises_unhealthy_dependency() -> None:
    c = _client()
    deps = c.get_dependencies("ingestion-service")
    for d in deps:
        if d["node_id"] == "zookeeper":
            d["health_status"] = "down"
    ctx = {
        "component": c.get_node("ingestion-service"),
        "dependencies": deps,
        "anomalies": [
            {
                "metric_name": "cpu_utilisation",
                "severity": "high",
                "predicted_breach_time": "2026-01-01T12:30:00Z",
            }
        ],
    }
    out = build_fallback("ingestion-service", ctx)
    assert len(out["steps"]) >= 3
    assert [s["rank"] for s in out["steps"]] == list(range(1, len(out["steps"]) + 1))
    assert "zookeeper" in out["steps"][0]["title"]
    assert "zookeeper" in out["root_cause_hypothesis"]
    assert out["summary"].startswith("High cpu_utilisation breach")


def test_fallback_without_anomalies_still_three_steps() -> None:
    c = _client()
    ctx = {
        "component": c.get_node("llm"),
        "dependencies": c.get_dependencies("llm"),
        "anomalies": [],
    }
    out = build_fallback("llm", ctx)
    assert len(out["steps"]) >= 3


def test_retention_prunes_oldest_beyond_max_count(tmp_path: Path) -> None:
    gen = PlaybookGenerator(tmp_path, max_count=2)
    ids = [gen.generate("kafka", VALID_LLM_JSON, generated_by="x:y").playbook_id for _ in range(3)]
    kept = {p.playbook_id for p in gen.list()}
    assert len(kept) == 2 and ids[-1] in kept
    assert len(list(tmp_path.glob("pb-*.json"))) == 2
    # Survives a restart with the same limit.
    assert len(PlaybookGenerator(tmp_path, max_count=2).list()) == 2


def test_retention_prunes_by_age_on_startup(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    old = PlaybookGenerator(tmp_path).generate("kafka", VALID_LLM_JSON, generated_by="x:y")
    path = tmp_path / f"{old.playbook_id}.json"
    data = json.loads(path.read_text())
    data["generated_at"] = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    path.write_text(json.dumps(data))
    fresh = PlaybookGenerator(tmp_path, retention_days=30)
    assert fresh.get(old.playbook_id) is None
    assert not path.exists()
