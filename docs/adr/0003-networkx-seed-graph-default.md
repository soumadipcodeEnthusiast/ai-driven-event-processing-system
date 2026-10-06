# ADR-0003: networkx seed graph by default, Neo4j optional

## Status
Accepted (2026-10-04)

## Context
REQ-G requires 2-hop dependency context in < 1 s for graphs of up to 500 nodes. The scaffold pointed at `bolt://neo4j:7687`, but compose had no Neo4j service. The platform has 8 components (CONTRACTS.md §4). The open question was Neo4j vs. AWS Neptune.

## Decision
Put a `GraphBackend` port behind `DependencyGraphClient`:

- **`networkx`** (default, `GRAPH_BACKEND=networkx`). An in-process `DiGraph` loaded at start-up from `GRAPH_SEED_PATH` (default `diagnostic-service/data/dependency_graph.json`, schema `contracts/dependency-graph-seed.schema.json`). 2-hop retrieval is a bounded BFS: microseconds at 500 nodes.
- **`neo4j`** (optional, compose profile `neo4j`, `GRAPH_DB_URL/USER/PASSWORD`). Cypher `MATCH (n {id:$id})-[r*1..2]-(m)`. It can be seeded from the same JSON.

Neptune is deferred. It can be added later as another adapter (openCypher) without touching `DiagnosticEngine`.

## Consequences
- + No extra infrastructure by default. Tests need no network, and REQ-G latency is easy to meet.
- + The seed file is versioned with the code and reviewed like code, which is a reasonable stand-in for a CMDB.
- − `health_status` in the seed is static. Live health is not reflected unless an adapter merges Prometheus `up{}` or the alert state. That merge is a future enhancement and is kept out of the contract.
- − Graph changes need a redeploy (or a restart that reloads the seed) on the networkx backend.
- − Neo4j adds an outage mode. See ARCHITECTURE.md §7 (recommended: fall back to the seed graph and flag `details.graph_backend`).
