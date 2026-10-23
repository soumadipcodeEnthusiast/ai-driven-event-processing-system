# Jira backlog import

[`jira-import.csv`](jira-import.csv) holds the project backlog: 7 epics (one per feature area), a story per SRS v1.0 requirement with acceptance criteria and how each is verified, the bugs found during end-to-end testing, and the open Phase 5 work. Status reflects the code today: implemented items are **Done**, attaching the production S3 bucket is **In Progress**, and Phase 5 hardening is **To Do**.

## Import (Jira Cloud, company-managed project)

1. **Settings (⚙) → System → External System Import → CSV**, or from the project: **⋯ → Import issues**.
2. Upload `jira-import.csv`. Keep the defaults: comma delimiter, UTF-8.
3. Pick the target project.
4. Map the fields:

| CSV column | Jira field |
|---|---|
| Issue Id | **Issue Id** |
| Parent Id | **Parent Id** (links stories/tasks/bugs to their epic) |
| Issue Type | Issue Type |
| Summary | Summary |
| Description | Description (uses Jira wiki markup: `*bold*`, `* bullets`) |
| Priority | Priority |
| Labels | Labels |
| Component | Component/s (tick "create new components" if offered) |
| Status | Status (the importer moves issues through the workflow) |
| Story Points | Story Points (Story point estimate in team-managed projects) |

5. **Begin import.** You should get 69 issues: 10 Epics, 30 Stories, 21 Tasks and 8 Bugs (51 Done, 7 In Progress, 11 To Do). E8 is GraphRAG, E9 is the TFT, and E10 "Testing & quality gates" holds one issue per test suite (T1–T9) with its latest result: filter by label `testing`.

In a team-managed project, map `Parent Id` → **Parent**. If your workflow uses different status names (for example "Closed" instead of "Done"), map them on the value-mapping screen.

## Regenerating

The CSV was generated from the requirements traceability table in the top-level README and docs/PLAN.md. When a requirement's status changes, update it there first and keep this file in sync.
