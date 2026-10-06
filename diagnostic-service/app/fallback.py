"""
fallback.py — rule-based playbook built from the graph context (ADR 0004).

Used when LLM_PROVIDER=none, the LLM fails, or its output is unusable, so a
playbook is always produced (``generated_by: "fallback:rule-based"``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

NAMESPACE = "event-processor"
RECENT_DEPLOY = timedelta(hours=24)
_HEALTH_ORDER = {"down": 0, "degraded": 1, "unknown": 2, "healthy": 3}
_SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _metric_step(component: str, node_type: str, anomaly: dict[str, Any]) -> dict[str, str]:
    metric = str(anomaly.get("metric_name", "metric"))
    sev = anomaly.get("severity", "?")
    when = anomaly.get("predicted_breach_time", "soon")
    sel = f"-n {NAMESPACE} -l app.kubernetes.io/name={component}"
    m = metric.lower()
    if "cpu" in m:
        return {
            "title": f"Confirm CPU pressure on {component} ({sev}, breach predicted {when})",
            "command": f"kubectl top pods {sel} --containers",
            "expected_outcome": "Pods consuming most CPU are identified and compared with limits.",
        }
    if "mem" in m:
        return {
            "title": f"Check memory growth and OOM kills on {component} ({sev})",
            "command": f"kubectl top pods {sel} && kubectl get pods {sel} "
            "-o jsonpath='{range .items[*]}{.metadata.name} "
            '{.status.containerStatuses[*].lastState.terminated.reason}{"\\n"}{end}\'',
            "expected_outcome": "Memory trend and any OOMKilled restarts are known; leak vs. "
            "load-driven growth can be distinguished.",
        }
    if "error" in m:
        return {
            "title": f"Inspect recent errors in {component} logs ({sev})",
            "command": f"kubectl logs -n {NAMESPACE} deploy/{component} --since=15m "
            "| grep -iE 'error|exception|timeout' | tail -n 50",
            "expected_outcome": "The dominant error type and failing downstream call are known.",
        }
    if "lag" in m:
        return {
            "title": f"Inspect consumer-group lag on {component} ({sev})",
            "command": "kafka-consumer-groups --bootstrap-server kafka:29092 "
            "--describe --all-groups",
            "expected_outcome": "Partitions with growing lag and their consumers are identified.",
        }
    return {
        "title": f"Inspect metric {metric} of {component} ({sev})",
        "command": f"curl -s 'http://prometheus:9090/api/v1/query?query=up{{service=\"{component}\"}}'",
        "expected_outcome": "Current value and trend of the metric are confirmed in Prometheus.",
    }


def _dependency_step(dep: dict[str, Any]) -> dict[str, str]:
    node, ntype, health = dep["node_id"], dep["node_type"], dep["health_status"]
    if ntype == "broker":
        cmd = (
            "kafka-topics --bootstrap-server kafka:29092 --describe "
            "--under-replicated-partitions"
        )
    elif node == "zookeeper":
        cmd = "echo ruok | nc zookeeper 2181"
    elif ntype == "datastore":
        cmd = f"kubectl get pods -n {NAMESPACE} -l app.kubernetes.io/name={node} -o wide"
    elif ntype == "external":
        cmd = f"kubectl logs -n {NAMESPACE} deploy/diagnostic-service --since=15m | grep -i {node}"
    else:
        cmd = f"kubectl rollout status deployment/{node} -n {NAMESPACE} --timeout=30s"
    return {
        "title": f"Investigate {dep['direction']} dependency {node} ({ntype}, {health})",
        "command": cmd,
        "expected_outcome": f"{node} is confirmed healthy, or its failure is identified as the "
        "root cause and escalated to its owner.",
    }


def _recent_deploy(meta: dict[str, Any], now: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(str(meta.get("last_deployed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - ts <= RECENT_DEPLOY


def build_fallback(
    component_id: str, context: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Return ``{summary, root_cause_hypothesis, steps}`` with >= 3 ranked steps."""
    now = now or datetime.now(timezone.utc)
    comp = context["component"]
    meta = comp.get("metadata", {})
    deps: list[dict[str, Any]] = context.get("dependencies", [])
    anomalies: list[dict[str, Any]] = [
        a for a in context.get("anomalies", []) if isinstance(a, dict)
    ]
    node_type = comp.get("node_type", "service")

    steps: list[dict[str, str]] = []

    # 1. unhealthy downstream dependencies first (most likely root cause)
    unhealthy = sorted(
        (d for d in deps if d["health_status"] in ("down", "degraded")),
        key=lambda d: (
            d["direction"] != "downstream",
            _HEALTH_ORDER[d["health_status"]],
            d["depth"],
        ),
    )
    steps += [_dependency_step(d) for d in unhealthy[:3]]

    # 2. the predicted anomalies on the component itself
    seen: set[str] = set()
    for a in anomalies:
        key = str(a.get("metric_name"))
        if key not in seen:
            seen.add(key)
            steps.append(_metric_step(component_id, node_type, a))
    if not anomalies:
        steps.append(
            {
                "title": f"Check current health of {component_id}",
                "command": f"kubectl get pods -n {NAMESPACE} "
                f"-l app.kubernetes.io/name={component_id} -o wide",
                "expected_outcome": "All pods are Running/Ready with no recent restarts.",
            }
        )

    # 3. connectivity to healthy direct downstream dependencies
    direct = [d["node_id"] for d in deps if d["direction"] == "downstream" and d["depth"] == 1]
    if direct:
        steps.append(
            {
                "title": f"Verify connectivity from {component_id} to {', '.join(direct)}",
                "command": f"kubectl exec -n {NAMESPACE} deploy/{component_id} -- getent hosts "
                + " ".join(direct),
                "expected_outcome": "All direct dependencies resolve and accept connections.",
            }
        )

    # 4. recent change
    if _recent_deploy(meta, now):
        steps.append(
            {
                "title": f"Roll back the deployment of {component_id} from "
                f"{meta.get('last_deployed_at')} if the issue started after it (disruptive)",
                "command": f"kubectl rollout undo deployment/{component_id} -n {NAMESPACE}",
                "expected_outcome": "The previous version is running and the metric trend reverts.",
            }
        )
    else:
        steps.append(
            {
                "title": f"Review recent changes to {component_id}",
                "command": f"kubectl rollout history deployment/{component_id} -n {NAMESPACE}",
                "expected_outcome": "Recent configuration or image changes are ruled in or out.",
            }
        )

    # 5. capacity mitigation for resource pressure
    if node_type == "service" and any(
        k in str(a.get("metric_name", "")).lower() for a in anomalies for k in ("cpu", "mem", "lag")
    ):
        steps.append(
            {
                "title": f"Scale out {component_id} to absorb load (mitigation)",
                "command": f"kubectl scale deployment/{component_id} -n {NAMESPACE} "
                "--replicas=<current+1>",
                "expected_outcome": "Per-pod utilisation drops below the threshold.",
            }
        )

    # 6. blast radius / escalation
    upstream = [d["node_id"] for d in deps if d["direction"] == "upstream"]
    steps.append(
        {
            "title": f"Notify {meta.get('owner_team', 'the owning team')} "
            f"({meta.get('sla_tier', 'n/a')})"
            + (
                f" and owners of affected upstream components: {', '.join(upstream)}"
                if upstream
                else ""
            ),
            "command": "",
            "expected_outcome": "Owners are aware of the predicted incident and its blast radius.",
        }
    )

    first = max(anomalies, key=lambda a: _SEVERITY.get(str(a.get("severity")), -1), default=None)
    if unhealthy:
        d = unhealthy[0]
        hypothesis = (
            f"{d['direction'].capitalize()} dependency {d['node_id']} ({d['node_type']}) is "
            f"{d['health_status']}; {component_id} reaches it via {d['via']} "
            f"({d['edge_type']}), making it the most likely root cause."
        )
    elif first is not None:
        hypothesis = (
            f"No unhealthy dependency in the 2-hop graph; the predicted {first.get('metric_name')} "
            f"breach points to load or resource pressure on {component_id} itself"
            + (" possibly related to its recent deployment." if _recent_deploy(meta, now) else ".")
        )
    else:
        hypothesis = (
            f"No anomaly data and no unhealthy dependency recorded for {component_id}; "
            "root cause undetermined from graph context."
        )
    if first is not None:
        summary = (
            f"{str(first.get('severity', 'predicted')).capitalize()} {first.get('metric_name')} "
            f"breach predicted on {component_id} at {first.get('predicted_breach_time', 'n/a')}."
        )
    else:
        summary = f"On-demand diagnosis of {component_id}."

    ranked = [{"rank": i + 1, **s} for i, s in enumerate(steps[:10])]
    return {"summary": summary, "root_cause_hypothesis": hypothesis, "steps": ranked}
