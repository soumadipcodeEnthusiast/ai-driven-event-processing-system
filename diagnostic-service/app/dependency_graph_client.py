"""
dependency_graph_client.py — Dependency graph query client stub.

Wraps calls to the graph database (Neo4j / Neptune / networkx in-memory)
to retrieve dependency subgraphs for failing components.

Requirements:
    REQ-G — The system shall query a dependency graph for the failing
             component and its transitive dependencies.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

GRAPH_DB_URL = os.getenv("GRAPH_DB_URL", "bolt://neo4j:7687")
GRAPH_DB_USER = os.getenv("GRAPH_DB_USER", "neo4j")
GRAPH_DB_PASSWORD = os.getenv("GRAPH_DB_PASSWORD", "changeme")


class DependencyGraph:
    """
    In-memory or persistent representation of the service dependency graph.

    Class-diagram operations:
        query_dependencies(component_id) → list[dict]
    """

    def query_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        """
        Query the graph for all dependencies of *component_id* (up to 2 hops).

        REQ-G: The system shall return a list of dependency node descriptors,
        each including ``node_id``, ``node_type``, ``health_status``, and
        ``edge_type`` (e.g. "calls", "depends_on", "publishes_to").

        Args:
            component_id: Identifier of the component whose dependencies
                          are to be retrieved.

        Returns:
            List of dependency node dicts.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-G
        #   Cypher: MATCH (n {id: $component_id})-[r*1..2]->(m) RETURN n, r, m
        #   Or: networkx BFS / DFS up to depth 2
        raise NotImplementedError("TODO: implement query_dependencies — REQ-G")


class DependencyGraphClient:
    """
    Client adapter that connects to the graph database and exposes a
    clean Python API for use by DiagnosticEngine.
    """

    def __init__(self, db_url: str = GRAPH_DB_URL) -> None:
        self._db_url = db_url
        self._graph = DependencyGraph()
        logger.info("DependencyGraphClient initialised (db_url=%s)", self._db_url)

    def get_dependencies(self, component_id: str) -> list[dict[str, Any]]:
        """
        Retrieve dependency information for *component_id* from the graph DB.

        REQ-G: Delegates to :class:`DependencyGraph`.

        Args:
            component_id: Component identifier.

        Returns:
            List of dependency node dicts (see :meth:`DependencyGraph.query_dependencies`).

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-G
        #   1. Open / reuse driver connection to self._db_url
        #   2. Delegate to self._graph.query_dependencies(component_id)
        #   3. Close / return connection to pool
        raise NotImplementedError("TODO: implement get_dependencies — REQ-G")

    def get_node_metadata(self, node_id: str) -> dict[str, Any]:
        """
        Retrieve metadata for a single graph node.

        REQ-G: Node metadata includes service version, team owner, SLA tier,
        and last deployment timestamp.

        Args:
            node_id: Graph node identifier.

        Returns:
            Metadata dict.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-G
        raise NotImplementedError("TODO: implement get_node_metadata — REQ-G")
