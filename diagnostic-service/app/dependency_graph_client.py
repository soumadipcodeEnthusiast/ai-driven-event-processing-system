"""
dependency_graph_client.py — dependency graph backends and client (REQ-G).

Backends implement :class:`DependencyGraph`:
    * :class:`NetworkXDependencyGraph` — in-memory, loaded from the JSON seed
      (``GRAPH_SEED_PATH``, contracts/dependency-graph-seed.schema.json). Default.
    * :class:`Neo4jDependencyGraph` — optional (``GRAPH_BACKEND=neo4j``); the
      ``neo4j`` driver is imported lazily. Seeds an empty database from the JSON.

:class:`DependencyGraphClient` is the facade used by DiagnosticEngine
(``get_dependencies`` → backend ``query_dependencies``). If Neo4j is
unreachable it falls back to the seed graph and logs a warning.

Traversal (ARCHITECTURE §10.9): up to 2 hops in **both** directions —
``downstream`` (what the component depends on) and ``upstream`` (what depends
on it) — each node reported once with its smallest depth, ``edge_type`` as
stored on the edge that reached it, and the ``via`` node.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

NODE_TYPES = {"service", "datastore", "broker", "external"}
HEALTH = {"healthy", "degraded", "down", "unknown"}
EDGE_TYPES = {"calls", "depends_on", "publishes_to", "consumes_from"}
METADATA_KEYS = ("version", "owner_team", "sla_tier", "last_deployed_at")
MAX_DEPTH = 2


class GraphError(RuntimeError):
    pass


class UnknownNodeError(KeyError):
    pass


def load_seed(path: str | Path) -> dict[str, Any]:
    """Load and sanity-check the seed JSON."""
    with open(path, encoding="utf-8") as fh:
        seed = json.load(fh)
    if not isinstance(seed, dict) or not isinstance(seed.get("nodes"), list):
        raise GraphError(f"invalid graph seed {path}: 'nodes' list required")
    ids = set()
    for n in seed["nodes"]:
        if n.get("node_type") not in NODE_TYPES or n.get("health_status") not in HEALTH:
            raise GraphError(f"invalid node in seed: {n.get('id')}")
        ids.add(n["id"])
    for e in seed.get("edges", []):
        if e.get("edge_type") not in EDGE_TYPES or e["source"] not in ids or e["target"] not in ids:
            raise GraphError(f"invalid edge in seed: {e}")
    return seed


class DependencyGraph(ABC):
    """Backend interface."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool: ...

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any]:
        """``{node_id, node_type, health_status, metadata}``; raises UnknownNodeError."""

    @abstractmethod
    def query_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        """
        Dependencies of *component_id* up to 2 hops, both directions.

        REQ-G: each item has ``node_id``, ``node_type``, ``health_status``,
        ``edge_type`` and ``depth`` (1|2), plus ``direction``
        (downstream|upstream) and ``via``. Sorted by (depth, direction, node_id).
        """

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


# ─── networkx backend ────────────────────────────────────────────────────────


class NetworkXDependencyGraph(DependencyGraph):
    def __init__(self, seed: dict[str, Any]) -> None:
        g: nx.MultiDiGraph = nx.MultiDiGraph()
        for n in seed["nodes"]:
            meta = n.get("metadata", {})
            g.add_node(
                n["id"],
                node_type=n["node_type"],
                health_status=n["health_status"],
                metadata={k: meta.get(k, "") for k in METADATA_KEYS},
            )
        for e in seed.get("edges", []):
            g.add_edge(e["source"], e["target"], edge_type=e["edge_type"])
        self._g = g

    @classmethod
    def from_file(cls, path: str | Path) -> NetworkXDependencyGraph:
        return cls(load_seed(path))

    @property
    def graph(self) -> nx.MultiDiGraph:
        return self._g

    def has_node(self, node_id: str) -> bool:
        return bool(self._g.has_node(node_id))

    def get_node(self, node_id: str) -> dict[str, Any]:
        if not self._g.has_node(node_id):
            raise UnknownNodeError(node_id)
        attrs = self._g.nodes[node_id]
        return {
            "node_id": node_id,
            "node_type": attrs["node_type"],
            "health_status": attrs["health_status"],
            "metadata": dict(attrs["metadata"]),
        }

    def _edge_type(self, src: str, dst: str) -> str:
        data = self._g.get_edge_data(src, dst) or {}
        # first edge in seed order (MultiDiGraph keys are insertion-ordered ints)
        return str(next(iter(data.values()))["edge_type"]) if data else "depends_on"

    def query_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        if not self._g.has_node(component_id):
            raise UnknownNodeError(component_id)
        found: dict[str, dict[str, Any]] = {}
        for direction in ("downstream", "upstream"):
            seen = {component_id}
            queue: deque[tuple[str, int]] = deque([(component_id, 0)])
            while queue:
                current, depth = queue.popleft()
                if depth == MAX_DEPTH:
                    continue
                neighbours = (
                    self._g.successors(current)
                    if direction == "downstream"
                    else self._g.predecessors(current)
                )
                for nb in sorted(set(neighbours)):
                    if nb in seen:
                        continue
                    seen.add(nb)
                    queue.append((nb, depth + 1))
                    edge_type = (
                        self._edge_type(current, nb)
                        if direction == "downstream"
                        else self._edge_type(nb, current)
                    )
                    prev = found.get(nb)
                    if prev is None or depth + 1 < prev["depth"]:
                        attrs = self._g.nodes[nb]
                        found[nb] = {
                            "node_id": nb,
                            "node_type": attrs["node_type"],
                            "health_status": attrs["health_status"],
                            "edge_type": edge_type,
                            "depth": depth + 1,
                            "direction": direction,
                            "via": current,
                        }
        return sorted(
            found.values(), key=lambda d: (d["depth"], d["direction"] != "downstream", d["node_id"])
        )


# ─── Neo4j backend (optional) ────────────────────────────────────────────────

_DOWNSTREAM_CYPHER = """
MATCH p = (n:Component {id: $id})-[*1..2]->(m:Component)
WHERE m.id <> $id
RETURN m.id AS node_id, m.node_type AS node_type, m.health_status AS health_status,
       toLower(type(last(relationships(p)))) AS edge_type, length(p) AS depth,
       nodes(p)[-2].id AS via
"""
_UPSTREAM_CYPHER = """
MATCH p = (m:Component)-[*1..2]->(n:Component {id: $id})
WHERE m.id <> $id
RETURN m.id AS node_id, m.node_type AS node_type, m.health_status AS health_status,
       toLower(type(head(relationships(p)))) AS edge_type, length(p) AS depth,
       nodes(p)[1].id AS via
"""


class Neo4jDependencyGraph(DependencyGraph):
    """Neo4j backend. Nodes are ``(:Component {id, node_type, health_status, <metadata>})``,
    relationships are upper-cased edge types (``CALLS``, ``DEPENDS_ON`` …)."""

    def __init__(self, url: str, user: str, password: str, seed: dict[str, Any] | None = None):
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise GraphError("neo4j driver not installed") from exc
        self._driver = GraphDatabase.driver(url, auth=(user, password))
        self._driver.verify_connectivity()
        if seed is not None:
            self._seed_if_empty(seed)

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [dict(r) for r in session.run(query, **params)]

    def _seed_if_empty(self, seed: dict[str, Any]) -> None:
        if self._run("MATCH (n:Component) RETURN count(n) AS c")[0]["c"] > 0:
            return
        for n in seed["nodes"]:
            props = {"node_type": n["node_type"], "health_status": n["health_status"]}
            props.update({k: n["metadata"].get(k, "") for k in METADATA_KEYS})
            self._run("MERGE (c:Component {id: $id}) SET c += $props", id=n["id"], props=props)
        for e in seed.get("edges", []):
            rel = e["edge_type"].upper()
            if e["edge_type"] not in EDGE_TYPES:  # whitelist: rel type is interpolated
                continue
            self._run(
                f"MATCH (a:Component {{id: $s}}), (b:Component {{id: $t}}) MERGE (a)-[:{rel}]->(b)",
                s=e["source"],
                t=e["target"],
            )
        logger.info("seeded Neo4j with %d nodes", len(seed["nodes"]))

    def has_node(self, node_id: str) -> bool:
        return bool(self._run("MATCH (n:Component {id: $id}) RETURN n.id AS id", id=node_id))

    def get_node(self, node_id: str) -> dict[str, Any]:
        rows = self._run("MATCH (n:Component {id: $id}) RETURN properties(n) AS p", id=node_id)
        if not rows:
            raise UnknownNodeError(node_id)
        p = rows[0]["p"]
        return {
            "node_id": node_id,
            "node_type": p.get("node_type", "service"),
            "health_status": p.get("health_status", "unknown"),
            "metadata": {k: str(p.get(k, "")) for k in METADATA_KEYS},
        }

    def query_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        if not self.has_node(component_id):
            raise UnknownNodeError(component_id)
        found: dict[str, dict[str, Any]] = {}
        for direction, query in (
            ("downstream", _DOWNSTREAM_CYPHER),
            ("upstream", _UPSTREAM_CYPHER),
        ):
            for row in sorted(self._run(query, id=component_id), key=lambda r: r["depth"]):
                if row["node_id"] in found and found[row["node_id"]]["depth"] <= row["depth"]:
                    continue
                found[row["node_id"]] = {**row, "direction": direction}
        return sorted(
            found.values(), key=lambda d: (d["depth"], d["direction"] != "downstream", d["node_id"])
        )

    def close(self) -> None:
        self._driver.close()


# ─── client facade ───────────────────────────────────────────────────────────


class DependencyGraphClient:
    """Facade used by DiagnosticEngine; selects the backend per ``GRAPH_BACKEND``."""

    def __init__(
        self,
        seed_path: str | Path,
        backend: str = "networkx",
        db_url: str = "",
        db_user: str = "",
        db_password: str = "",
        graph: DependencyGraph | None = None,
    ) -> None:
        self._fallback: DependencyGraph | None = None
        if graph is not None:
            self._graph = graph
        else:
            seed = load_seed(seed_path)
            self._fallback = NetworkXDependencyGraph(seed)
            self._graph = self._fallback
            if backend == "neo4j":
                try:
                    self._graph = Neo4jDependencyGraph(db_url, db_user, db_password, seed)
                except Exception as exc:
                    logger.warning(
                        "Neo4j at %s unavailable (%s); falling back to seed graph %s",
                        db_url,
                        exc,
                        seed_path,
                    )
        self.backend = "neo4j" if isinstance(self._graph, Neo4jDependencyGraph) else "networkx"
        logger.info("DependencyGraphClient initialised (backend=%s)", self.backend)

    def _call(self, method: str, *args: Any) -> Any:
        try:
            return getattr(self._graph, method)(*args)
        except UnknownNodeError:
            raise
        except Exception as exc:
            if self._fallback is None or self._graph is self._fallback:
                raise
            logger.warning("graph backend error (%s); using seed graph for this query", exc)
            return getattr(self._fallback, method)(*args)

    def has_node(self, node_id: str) -> bool:
        return bool(self._call("has_node", node_id))

    def get_node(self, node_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("get_node", node_id)
        return result

    def get_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        """REQ-G: dependency nodes of *component_id* (see DependencyGraph.query_dependencies)."""
        result: list[dict[str, Any]] = self._call("query_dependencies", component_id)
        return result

    def get_node_metadata(self, node_id: str) -> dict[str, Any]:
        """REQ-G: ``{version, owner_team, sla_tier, last_deployed_at}`` of *node_id*."""
        return dict(self.get_node(node_id)["metadata"])

    def close(self) -> None:
        self._graph.close()
