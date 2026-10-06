# ADR-0005: File-based JSON playbook store

## Status
Accepted (2026-10-04)

## Context
REQ-I requires playbooks stored as structured JSON, retrievable by ID and linked to `alert_id`. The scaffold used an in-memory dict, which loses everything on restart. Volume is low: one playbook per alert, at most dozens a day.

## Decision
Store each playbook as `{PLAYBOOK_STORE_DIR}/{playbook_id}.json` (default `/data/playbooks`, a mounted volume). Write to a temp file in the same directory, then `os.replace` (atomic rename). `GET /playbooks?alert_id=&component_id=` scans the directory, optionally behind an in-memory index rebuilt at start-up. `playbook_id` is validated against `^pb-[a-z0-9][a-z0-9-]*-[0-9a-f]{8}$` before any path is built.

## Consequences
- + Survives restarts. Zero infrastructure, human-inspectable, trivially backed up, and the format *is* the contract schema.
- + `tmp_path` makes it easy to test.
- − Single writer only. Several diagnostic replicas need a shared RWX volume (and still race on the index) or a real store. **Diagnostic runs as 1 replica** until this changes, even though the scaffolded HPA allows up to 4.
- − Listing is O(n) in file count. Fine for up to about 10⁴ playbooks. Add retention/TTL or move on before that.
- Future path: SQLite on the same volume (single node), then Postgres or an object store with an index, behind the same `PlaybookStore` port.
