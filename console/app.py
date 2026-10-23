"""
console/app.py — demo console: one page that shows the pipeline live and drives the demo.

Runs inside the compose network (port 8099, host 9099). The browser only talks
to this origin; the console reads Kafka directly (topics, offsets, consumer
lag, recent messages), scrapes each service's /metrics, and proxies the demo
actions (send events, score an event, run a forecast) to the real services.
It holds no state of its own and changes nothing it doesn't put back
(forecast thresholds are restored after a simulated breach).
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import statistics
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any

import boto3
import httpx
from botocore.config import Config as BotoConfig
from confluent_kafka import Consumer, ConsumerGroupTopicPartitions, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


BOOTSTRAP = _env("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
INGESTION = _env("INGESTION_URL", "http://ingestion-service:8080")
DIAGNOSTIC = _env("DIAGNOSTIC_URL", "http://diagnostic-service:8082")
FORECASTING = _env("FORECASTING_URL", "http://forecasting-service:8081")
ARCHIVER = _env("ARCHIVER_URL", "http://event-archiver:8083")
PROMETHEUS = _env("PROMETHEUS_URL", "http://prometheus:9090")
GRAFANA = _env("GRAFANA_URL", "http://grafana:3000")
ZOOKEEPER = _env("ZOOKEEPER_ADDR", "zookeeper:2181")
NEO4J_HTTP = _env("NEO4J_HTTP_URL", "http://neo4j:7474")
OLLAMA = _env("OLLAMA_URL", "http://ollama:11434")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "http://minio:9000")
S3_BUCKET = _env("S3_ARCHIVE_BUCKET", "event-archive")
S3_REGION = _env("S3_ARCHIVE_REGION", "us-east-1")

# Links the browser opens: host ports, not in-network ones.
PUBLIC_HOST = _env("PUBLIC_HOST", "localhost")
LINKS = {
    "grafana": f"http://{PUBLIC_HOST}:{_env('GRAFANA_HOST_PORT', '9030')}",
    "prometheus": f"http://{PUBLIC_HOST}:9090",
    "minio": f"http://{PUBLIC_HOST}:9001",
    "ingestion": f"http://{PUBLIC_HOST}:{_env('INGESTION_HOST_PORT', '9080')}/actuator/health",
    "diagnostic": f"http://{PUBLIC_HOST}:{_env('DIAGNOSTIC_HOST_PORT', '9082')}/docs",
    "forecasting": f"http://{PUBLIC_HOST}:{_env('FORECASTING_HOST_PORT', '9081')}/docs",
    "archiver": f"http://{PUBLIC_HOST}:{_env('ARCHIVER_HOST_PORT', '9083')}/metrics",
    "neo4j": f"http://{PUBLIC_HOST}:{_env('NEO4J_HTTP_HOST_PORT', '9074')}",
}

TOPICS = ["raw-events", "normalized-events", "raw-events.DLT", "anomaly-audit", "incident-playbooks"]
GROUPS = {
    "ingestion-consumer-group": "ingestion-service",
    "diagnostic-anomaly-scorer": "diagnostic-service",
    "event-archiver": "event-archiver",
}

STATIC_DIR = Path(__file__).parent / "static"

_admin = AdminClient({"bootstrap.servers": BOOTSTRAP, "socket.timeout.ms": 3000})
_meta = Consumer(
    {"bootstrap.servers": BOOTSTRAP, "group.id": "demo-console-meta", "enable.auto.commit": False}
)
_producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all", "linger.ms": 5})
_s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT or None,
    region_name=S3_REGION,
    config=BotoConfig(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
)
_http = httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0))
_pool = ThreadPoolExecutor(max_workers=12)
_kafka_lock = threading.Lock()


# ── service health ───────────────────────────────────────────────────────────


def _probe_http(url: str, expect: str | None = None) -> tuple[bool, str]:
    r = _http.get(url, timeout=3.0)
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}"
    if expect and expect not in r.text:
        return False, r.text[:80]
    return True, f"HTTP {r.status_code}"


def _probe_kafka() -> tuple[bool, str]:
    md = _admin.list_topics(timeout=3)
    user_topics = [t for t in md.topics if not t.startswith("__")]
    return True, f"{len(md.brokers)} broker · {len(user_topics)} topics"


def _probe_ollama() -> tuple[bool, str]:
    r = _http.get(f"{OLLAMA}/api/tags", timeout=3.0)
    r.raise_for_status()
    models = [m.get("name", "?") for m in r.json().get("models", [])]
    return True, ", ".join(models) if models else "no model pulled yet"


def _probe_zookeeper() -> tuple[bool, str]:
    host, port = ZOOKEEPER.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=2):
        return True, "tcp 2181 open"


SERVICES: list[dict[str, Any]] = [
    {"id": "kafka", "name": "kafka", "role": "event backbone", "port": 9092,
     "probe": _probe_kafka},
    {"id": "zookeeper", "name": "zookeeper", "role": "kafka metadata", "port": None,
     "probe": _probe_zookeeper},
    {"id": "ingestion", "name": "ingestion-service", "role": "validate · normalise · DLT",
     "port": 9080, "probe": lambda: _probe_http(f"{INGESTION}/actuator/health", '"UP"')},
    {"id": "diagnostic", "name": "diagnostic-service", "role": "anomaly scoring",
     "port": 9082, "probe": lambda: _probe_http(f"{DIAGNOSTIC}/health")},
    {"id": "forecasting", "name": "forecasting-service", "role": "predictive alerts",
     "port": 9081, "probe": lambda: _probe_http(f"{FORECASTING}/health")},
    {"id": "archiver", "name": "event-archiver", "role": "kafka → s3",
     "port": 9083, "probe": lambda: _probe_http(f"{ARCHIVER}/metrics")},
    {"id": "neo4j", "name": "neo4j", "role": "graphrag · dependency graph",
     "port": 9074, "probe": lambda: _probe_http(NEO4J_HTTP)},
    {"id": "ollama", "name": "ollama", "role": "graphrag · local llm",
     "port": 9434, "probe": _probe_ollama},
    {"id": "minio", "name": "minio (s3)", "role": "object storage",
     "port": 9001, "probe": lambda: _probe_http(f"{S3_ENDPOINT or 'http://minio:9000'}/minio/health/live")
     if S3_ENDPOINT else (True, "aws s3")},
    {"id": "prometheus", "name": "prometheus", "role": "metrics",
     "port": 9090, "probe": lambda: _probe_http(f"{PROMETHEUS}/-/healthy")},
    {"id": "grafana", "name": "grafana", "role": "dashboards",
     "port": 9030, "probe": lambda: _probe_http(f"{GRAFANA}/api/health")},
]


def _check(svc: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        ok, detail = svc["probe"]()
    except Exception as exc:  # noqa: BLE001 — any failure is "down"
        ok, detail = False, type(exc).__name__
    return {
        "id": svc["id"],
        "name": svc["name"],
        "role": svc["role"],
        "port": svc["port"],
        "ok": ok,
        "ms": round((time.perf_counter() - start) * 1000, 1),
        "detail": detail,
    }


# ── kafka ────────────────────────────────────────────────────────────────────


def _kafka_snapshot() -> dict[str, Any]:
    with _kafka_lock:
        md = _admin.list_topics(timeout=3)
        ends: dict[tuple[str, int], int] = {}
        topics = []
        for name in TOPICS:
            t = md.topics.get(name)
            if t is None or t.error is not None:
                topics.append({"name": name, "exists": False})
                continue
            total = retained = 0
            for p in sorted(t.partitions):
                lo, hi = _meta.get_watermark_offsets(TopicPartition(name, p), timeout=3)
                ends[(name, p)] = hi
                total += hi
                retained += hi - lo
            topics.append({"name": name, "exists": True, "partitions": len(t.partitions),
                           "messages": total, "retained": retained})

        groups = []
        for group, owner in GROUPS.items():
            try:
                fut = _admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group)])[group]
                tps = fut.result(timeout=3).topic_partitions or []
            except Exception:  # noqa: BLE001 — group not created yet
                tps = []
            by_topic: dict[str, int] = {}
            for tp in tps:
                if tp.offset < 0:
                    continue
                lag = max(ends.get((tp.topic, tp.partition), tp.offset) - tp.offset, 0)
                by_topic[tp.topic] = by_topic.get(tp.topic, 0) + lag
            for topic, lag in sorted(by_topic.items()):
                groups.append({"group": group, "owner": owner, "topic": topic, "lag": lag})
            if not by_topic:
                groups.append({"group": group, "owner": owner, "topic": None, "lag": None})
    return {"topics": topics, "groups": groups}


def _decode(raw: bytes | None) -> Any:
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _tail(topic: str, n: int) -> list[dict[str, Any]]:
    md = _admin.list_topics(topic=topic, timeout=3)
    t = md.topics.get(topic)
    if t is None or t.error is not None:
        raise HTTPException(404, f"topic {topic} not found")
    c = Consumer({
        "bootstrap.servers": BOOTSTRAP,
        "group.id": f"demo-console-tail-{uuid.uuid4().hex[:8]}",
        "enable.auto.commit": False,
        "enable.partition.eof": True,
    })
    try:
        assign, pending = [], set()
        for p in t.partitions:
            lo, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=3)
            if hi > lo:
                assign.append(TopicPartition(topic, p, max(lo, hi - n)))
                pending.add(p)
        if not assign:
            return []
        c.assign(assign)
        out: list[dict[str, Any]] = []
        deadline = time.monotonic() + 4
        while pending and time.monotonic() < deadline:
            msg = c.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    pending.discard(msg.partition())
                continue
            _, ts = msg.timestamp()
            out.append({
                "partition": msg.partition(),
                "offset": msg.offset(),
                "timestamp": ts,
                "key": _decode(msg.key()),
                "value": _decode(msg.value()),
                "headers": {k: (v or b"").decode("utf-8", errors="replace")
                            for k, v in (msg.headers() or [])},
            })
        out.sort(key=lambda m: m["timestamp"], reverse=True)
        return out[:n]
    finally:
        c.close()


# ── metrics scraping ─────────────────────────────────────────────────────────

_SAMPLE = re.compile(r"^([A-Za-z_:][\w:]*)(?:\{([^}]*)\})?\s+(\S+)")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _scrape(url: str) -> list[tuple[str, dict[str, str], float]]:
    try:
        r = _http.get(url, timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    out = []
    for line in r.text.splitlines():
        m = _SAMPLE.match(line)
        if m:
            try:
                out.append((m[1], dict(_LABEL.findall(m[2] or "")), float(m[3])))
            except ValueError:
                pass
    return out


def _sum(samples: list[tuple[str, dict[str, str], float]], name: str, **labels: str) -> float | None:
    hits = [v for n, lb, v in samples if n == name and all(lb.get(k) == w for k, w in labels.items())]
    return sum(hits) if hits else None


def _counters() -> dict[str, Any]:
    ing, diag, arc = _pool.map(
        _scrape,
        [f"{INGESTION}/actuator/prometheus", f"{DIAGNOSTIC}/metrics", f"{ARCHIVER}/metrics"],
    )
    return {
        "ingest_valid": _sum(ing, "ingestion_events_total", outcome="valid"),
        "ingest_invalid": _sum(ing, "ingestion_events_total", outcome="invalid"),
        "ingest_error": _sum(ing, "ingestion_events_total", outcome="error"),
        "scored": _sum(diag, "anomaly_events_scored_total"),
        "anomalies": _sum(diag, "anomalies_detected_total"),
        "audited": _sum(diag, "anomaly_audit_published_total"),
        "threshold": _sum(diag, "anomaly_threshold"),
        "archived_records": _sum(arc, "archive_records_total"),
        "archived_objects": _sum(arc, "archive_objects_total"),
        "archive_last_success": _sum(arc, "archive_last_success_timestamp_seconds"),
        "diagnoses_ok": _sum(diag, "diagnoses_total", outcome="ok"),
        "diagnoses_fallback": _sum(diag, "diagnoses_total", outcome="fallback"),
        "playbooks": _sum(diag, "playbooks_stored_total"),
        "graph_context_count": _sum(diag, "graph_context_seconds_count"),
        "graph_context_sum": _sum(diag, "graph_context_seconds_sum"),
        "llm_seconds_count": _sum(diag, "llm_request_seconds_count"),
        "llm_seconds_sum": _sum(diag, "llm_request_seconds_sum"),
    }


def _forecaster_info() -> dict[str, Any] | None:
    for name, labels, _v in _scrape(f"{FORECASTING}/metrics"):
        if name == "forecaster_info":
            return labels
    return None


def _alert_summary() -> dict[str, Any] | None:
    try:
        r = _http.get(f"{FORECASTING}/alerts/summary", timeout=3.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError:
        return None


# ── event generation (same shapes as scripts/produce-events.sh) ─────────────

SOURCES = ["checkout-api", "payments-api", "auth-service", "inventory-api"]
TYPES = ["http_request", "db_query", "cache_miss", "error", "heartbeat"]
COMPONENTS = ["ingestion-service", "forecasting-service", "diagnostic-service",
              "kafka", "zookeeper", "prometheus"]


def _events(count: int, invalid_ratio: float, run_id: str) -> tuple[list[str], int]:
    rng = random.Random()
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    lines, invalid = [], 0
    for n in range(1, count + 1):
        eid = f"evt-{run_id}-{n}"
        src = rng.choice(SOURCES)
        ts = stamp + ("Z" if n % 2 else "+00:00")
        payload = {"status": 500 if rng.random() < 0.05 else 200,
                   "latency_ms": rng.randint(5, 904)}
        if invalid_ratio > 0 and rng.random() < invalid_ratio:
            bad = [
                {"timestamp": ts, "source": src, "payload": payload},           # no event_id
                {"event_id": eid, "timestamp": ts, "payload": payload},         # no source
                {"event_id": eid, "timestamp": stamp, "source": src},           # no offset, no payload
                {"event_id": eid, "timestamp": ts, "source": src, "payload": None},
                f"this is not json {eid}",
                [eid],
            ][invalid % 6]
            lines.append(bad if isinstance(bad, str) else json.dumps(bad))
            invalid += 1
        else:
            lines.append(json.dumps({
                "event_id": eid, "timestamp": ts, "source": src, "type": rng.choice(TYPES),
                "component_id": rng.choice(COMPONENTS), "payload": payload,
            }))
    return lines, invalid


# ── anomaly demo ─────────────────────────────────────────────────────────────

# A fixed "normal" checkout latency history (~100 ms ± 12). Every scoring
# request gets a fresh stream seeded with it, so the same latency always gets
# the same score no matter what was scored before.
_BASE_RNG = random.Random(7)
BASELINE = [round(_BASE_RNG.gauss(100, 12)) for _ in range(30)]


def _upstream(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    return r.json()


# ── history (sparklines, throughput chart) ───────────────────────────────────

SAMPLE_SECONDS = 5
_history: deque[dict[str, Any]] = deque(maxlen=180)  # 15 min at 5 s
_stop = threading.Event()


def _sample() -> dict[str, Any]:
    snap = _kafka_snapshot()
    c = _counters()
    return {
        "t": time.time(),
        "topics": {t["name"]: t["messages"] for t in snap["topics"] if t.get("exists")},
        "lag": sum(g["lag"] or 0 for g in snap["groups"]),
        "anomalies": c["anomalies"],
        "archived_objects": c["archived_objects"],
        "ingest_valid": c["ingest_valid"],
        "ingest_invalid": c["ingest_invalid"],
        "playbooks": c["playbooks"],
    }


def _sampler() -> None:
    while not _stop.is_set():
        try:
            _history.append(_sample())
        except Exception:  # noqa: BLE001 — a gap in the history is fine
            pass
        _stop.wait(SAMPLE_SECONDS)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    thread = threading.Thread(target=_sampler, name="history-sampler", daemon=True)
    thread.start()
    yield
    _stop.set()


# ── API ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="demo console", docs_url=None, redoc_url=None, lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"links": LINKS, "bucket": S3_BUCKET, "s3": "minio" if S3_ENDPOINT else "aws"}


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    checks = [_pool.submit(_check, s) for s in SERVICES]
    counters = _pool.submit(_counters)
    alerts = _pool.submit(_alert_summary)
    forecaster = _pool.submit(_forecaster_info)
    try:
        kafka: dict[str, Any] = {"ok": True, **_kafka_snapshot()}
    except Exception as exc:  # noqa: BLE001
        kafka = {"ok": False, "error": str(exc), "topics": [], "groups": []}
    return {
        "time": time.time(),
        "services": [f.result() for f in checks],
        "kafka": kafka,
        "counters": counters.result(),
        "alerts": alerts.result(),
        "forecaster": forecaster.result(),
    }


@app.get("/api/history")
def history() -> dict[str, Any]:
    return {"step": SAMPLE_SECONDS, "samples": list(_history)}


@app.get("/api/throughput")
def throughput(minutes: int = 30) -> dict[str, Any]:
    end = time.time()
    try:
        r = _http.get(f"{PROMETHEUS}/api/v1/query_range", params={
            "query": "sum by (outcome) (rate(ingestion_events_total[1m]))",
            "start": end - minutes * 60, "end": end, "step": 15,
        })
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"prometheus: {exc}") from exc
    series = {
        s["metric"].get("outcome", "?"): [[float(t), float(v)] for t, v in s["values"]]
        for s in r.json()["data"]["result"]
    }
    return {"start": end - minutes * 60, "end": end, "series": series}


@app.get("/api/topics/{topic}/tail")
def tail(topic: str, n: int = 8) -> list[dict[str, Any]]:
    if topic not in TOPICS:
        raise HTTPException(404, f"unknown topic {topic}")
    return _tail(topic, max(1, min(n, 25)))


class ProduceRequest(BaseModel):
    count: int = Field(300, ge=1, le=20000)
    invalid_ratio: float = Field(0.1, ge=0.0, le=1.0)


@app.post("/api/produce")
def produce(body: ProduceRequest) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%H%M%S") + "-" + uuid.uuid4().hex[:4]
    lines, invalid = _events(body.count, body.invalid_ratio, run_id)
    delivered = failed = 0

    def on_delivery(err: Any, _msg: Any) -> None:
        nonlocal delivered, failed
        if err is None:
            delivered += 1
        else:
            failed += 1

    start = time.perf_counter()
    for line in lines:
        while True:
            try:
                _producer.produce("raw-events", value=line.encode(), on_delivery=on_delivery)
                break
            except BufferError:
                _producer.poll(0.1)
        _producer.poll(0)
    _producer.flush(15)
    return {
        "run_id": run_id, "sent": len(lines), "invalid": invalid,
        "delivered": delivered, "failed": failed,
        "seconds": round(time.perf_counter() - start, 3),
        "sample": json.loads(next((ln for ln in lines if ln.startswith("{\"event_id\"")), "null")
                             or "null"),
    }


class ScoreRequest(BaseModel):
    latency_ms: float = Field(..., ge=0, le=100000)


@app.post("/api/anomaly/score")
def score(body: ScoreRequest) -> dict[str, Any]:
    stream = f"checkout-demo-{uuid.uuid4().hex[:6]}"

    def event(eid: str, ms: float) -> dict[str, Any]:
        return {"event": {"event_id": eid, "source": stream, "type": "http_request",
                          "payload": {"latency_ms": ms}}}

    for i, ms in enumerate(BASELINE):
        _upstream(_http.post(f"{DIAGNOSTIC}/score/{stream}-b{i}", json=event(f"{stream}-b{i}", ms)))
    eid = f"{stream}-probe"
    result = _upstream(_http.post(f"{DIAGNOSTIC}/score/{eid}", json=event(eid, body.latency_ms)))
    return {
        **result,
        "latency_ms": body.latency_ms,
        "baseline": {"n": len(BASELINE), "mean": round(statistics.mean(BASELINE), 1),
                     "stdev": round(statistics.stdev(BASELINE), 1)},
    }


class ThresholdRequest(BaseModel):
    threshold: float = Field(..., ge=0.0, le=1.0)


@app.put("/api/anomaly/threshold")
def set_threshold(body: ThresholdRequest) -> Any:
    return _upstream(_http.put(f"{DIAGNOSTIC}/thresholds", json={"threshold": body.threshold}))


@app.get("/api/anomaly/model")
def get_model() -> Any:
    return _upstream(_http.get(f"{DIAGNOSTIC}/model"))


class ForecastRequest(BaseModel):
    component_id: str = "ingestion-service"


@app.post("/api/forecast")
def forecast(body: ForecastRequest) -> Any:
    return _upstream(_http.post(f"{FORECASTING}/forecast", json={"component_id": body.component_id},
                                timeout=30.0))


@app.post("/api/forecast/simulate")
def simulate(body: ForecastRequest) -> dict[str, Any]:
    """Lower one threshold far below the current value, forecast, then put it back."""
    comp = body.component_id
    first = forecast(body)
    metrics = first.get("details", {}).get("metrics", {})
    usable = [m for m, d in metrics.items()
              if d.get("outcome") != "insufficient_data" and d.get("last_value") is not None]
    if not usable:
        raise HTTPException(409, "no metric has enough history yet — wait ~10 min after start-up")
    metric = "cpu_utilisation" if "cpu_utilisation" in usable else usable[0]
    original = _upstream(_http.get(f"{FORECASTING}/thresholds"))[comp][metric]
    lowered = round(max(float(metrics[metric]["last_value"]) * 0.5, 1e-4), 6)
    _upstream(_http.put(f"{FORECASTING}/thresholds/{comp}", json={metric: lowered}))
    try:
        result = forecast(body)
    finally:
        _upstream(_http.put(f"{FORECASTING}/thresholds/{comp}", json={metric: original}))
    return {"metric": metric, "lowered_to": lowered, "restored_to": original, "result": result}


# ── GraphRAG ─────────────────────────────────────────────────────────────────


@app.get("/api/graph/{component_id}")
def graph(component_id: str) -> Any:
    return _upstream(_http.get(f"{DIAGNOSTIC}/graph/{component_id}"))


class DiagnoseRequest(BaseModel):
    component_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    anomalies: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


@app.post("/api/diagnose")
def diagnose(body: DiagnoseRequest) -> Any:
    """GraphRAG on demand. A local LLM on CPU can take a minute or two."""
    return _upstream(_http.post(
        f"{DIAGNOSTIC}/diagnose/{body.component_id}",
        json={"anomalies": body.anomalies},
        timeout=httpx.Timeout(300.0, connect=3.0),
    ))


@app.get("/api/playbooks")
def playbooks(limit: int = 10) -> Any:
    return _upstream(_http.get(f"{DIAGNOSTIC}/playbooks", params={"limit": max(1, min(limit, 50))}))


@app.get("/api/incidents")
def incidents() -> Any:
    return _upstream(_http.get(f"{DIAGNOSTIC}/incidents"))


@app.get("/api/alerts")
def alerts() -> list[Any]:
    out: list[Any] = []
    for status in ("open", "acknowledged"):
        out += _upstream(_http.get(f"{FORECASTING}/alerts", params={"status": status}))
    return sorted(out, key=lambda a: a["created_at"], reverse=True)


@app.post("/api/alerts/{alert_id}/{action}")
def alert_action(alert_id: str, action: str) -> Any:
    if action not in ("ack", "resolve"):
        raise HTTPException(404, "unknown action")
    return _upstream(_http.post(f"{FORECASTING}/alerts/{alert_id}/{action}"))


@app.get("/api/archive")
def archive() -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    try:
        for page in _s3.get_paginator("list_objects_v2").paginate(
            Bucket=S3_BUCKET, Prefix="events/", PaginationConfig={"MaxItems": 20000}
        ):
            objects += page.get("Contents", [])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"s3: {exc}") from exc
    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    return {
        "bucket": S3_BUCKET,
        "count": len(objects),
        "bytes": sum(o["Size"] for o in objects),
        "latest": [{"key": o["Key"], "size": o["Size"],
                    "modified": o["LastModified"].isoformat()} for o in objects[:10]],
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
