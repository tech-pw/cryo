# AGENTS.md — Guide for AI Agents working on Cryo

This file orients an AI coding agent (and humans) working in this repo. Read it before making
changes. For *what* we're building and *why*, read `plan.md`, `hld.md`, and `lld.md` first.

---

## 1. What this repo is

**Cryo** — a self-hosted **Apache Iceberg maintenance & health platform**. It consolidates Iceberg
table maintenance (compaction/optimize, snapshot expiry, orphan-file removal, manifest rewrite),
datalake health/observability, and ad-hoc maintenance behind a Python backend + React UI, deployable to
Kubernetes. It replaces a set of Airflow DAGs + dbt macros (see `hld.md` §9 for the mapping).

**Hard requirements (do not violate):**
- **No Airflow.** Scheduling/orchestration lives in the platform (`scheduler/` + `governor/`).
- **Sources (read) and Destinations/engines (write) are separate** — separate DB tables, configs,
  and code. Never couple catalog access to engine execution.
- **Trino SQL and Spark SQL generation are separate** (`engines/trino/sql.py` vs
  `engines/spark/sql.py`). The Planner is engine-agnostic and emits **no SQL strings**.
- **Preserve the load-aware, partition-aware behavior** that protects Trino today (see §5).

---

## 2. Source-of-truth docs (read order)

1. `plan.md` — vision, scope, decisions, phases, assumptions.
2. `hld.md` — components, data flow, deployment, separation of concerns.
3. `lld.md` — schemas, interfaces, planner algorithm, rendered SQL, API surface.
4. `todo.md` — the phased task checklist; keep it updated as you complete work.
5. `docs/` — persistent project brain. Always keep it current with fixes, implementations,
   new features, design decisions, operational notes, and any project knowledge future agents
   or maintainers will need. If you're picking up work with no other context, start at
   [`docs/EXECUTION_PLAN.md`](./docs/EXECUTION_PLAN.md) — it sequences everything in
   `docs/RECOMMENDATIONS.md`, `docs/PERFORMANCE_AND_OPENNESS.md`, and
   `docs/ARCHITECTURE_AND_AI_STRATEGY.md` into an ordered, actionable plan.
6. This file — conventions & guardrails.
7. [`docs/LOCAL_DEV_ONLY_INVENTORY.md`](./docs/LOCAL_DEV_ONLY_INVENTORY.md) — ad-hoc local datalake
   components that must not ship to prod; **update in the same PR** when adding new dev-only scripts
   or compose services.

The reference implementation being ported lives **outside this repo** (read-only, for parity).
The maintainer team tracks these upstream files locally for behavior comparison; the exact
paths are intentionally not committed here because they reference a private developer
checkout. Files of interest when checking semantic parity:

- `dags/data_team/trino_admin/dag_iceberg_auto_maintenance_with_trino.py`
- `dags/data_team/iceberg_compaction_utils/iceberg_table_stats_batch.py`
- `pw_dbt_trino/macros/iceberg_*.sql`
- `dags/data_team/trino_admin/iceberg_maintenance_report.py`

When in doubt about maintenance semantics, **match these files' behavior** unless `lld.md` says otherwise.

---

## 3. Project layout (where things go)

See `lld.md` §1 for the full tree. Quick map:

| Area | Path | Notes |
|------|------|-------|
| Config | `cryo/config.py` | pydantic-settings; infra only. Connection secrets live in DB. |
| DB models / migrations | `cryo/db/`, `migrations/` | SQLAlchemy + Alembic. Schema `cryo`. |
| Domain DTOs/enums | `cryo/domain/` | Pure pydantic/dataclasses. No I/O. |
| Read side | `cryo/sources/` | PyIceberg catalog adapters. |
| Write side | `cryo/engines/trino/`, `cryo/engines/spark/` | **Keep dialects separate.** |
| Planner | `cryo/planner/` | Pure, engine-agnostic, unit-tested. **No SQL strings.** |
| Scanner | `cryo/scanner/` | Port of stats batch; CronJob entrypoint `run.py`. |
| Orchestration | `cryo/scheduler/`, `cryo/governor/`, `cryo/worker/` | Leader-elected scheduler/governor. |
| Reporting | `cryo/reporting/` | HTML/email/Slack + alerts. |
| API | `cryo/api/` | FastAPI app + routers. |
| Queue/locks | `cryo/queue/` | Redis queue + leader/per-table locks. |
| Frontend | `web/` | React + TS + Vite + Ant Design + Recharts + TanStack Query. |
| Deploy | `deploy/helm/cryo/` | Helm chart per service. |

---

## 4. Conventions

### Python (backend)
- Python **3.12**, fully type-hinted; `from __future__ import annotations` where useful.
- Tooling: **ruff** (lint+format), **mypy** (strict-ish), **pytest**. Run before finishing (see §6).
- FastAPI + Pydantic v2; SQLAlchemy 2.0 style; async where it pays (API, governor loops); the
  scanner and engine execution can be sync (they're I/O bound on external systems).
- Layering: `api → repositories → db`, `worker → planner + engines`, `governor → engines (probe) +
  queue`. **Domain/planner never import db/engine clients.**
- Naming mirrors `lld.md` (`MaintenancePlan`, `Operation`, `PartitionPredicate`, `OperationType`,
  `EngineType`, `Mode`). Don't invent parallel vocabularies.
- Secrets: never log them; never return them from the API; encrypt at rest.
- Don't add narrating comments; comment only non-obvious intent/trade-offs (e.g. why a chunk cap
  avoids Trino `FilterNode` errors).

### SQL generation
- Trino dialect ONLY in `engines/trino/sql.py`; Spark dialect ONLY in `engines/spark/sql.py`.
- Every renderer change needs a **golden test** (input plan → expected SQL) for that engine.
- Partition predicate math (epoch-day/month/year) must match the dbt macro exactly — there are
  parity tests; keep them green.

### Frontend
- TypeScript strict; functional components + hooks; TanStack Query for data; types generated from
  the backend OpenAPI schema (don't hand-write API types).
- Use Ant Design components (esp. `<Table>` selection/filtering) rather than rolling custom ones.
- Bulk selection sends a **filter spec**, never a list of page IDs (see `lld.md` §12/§13).

### Database
- All tables in schema `cryo`. Every schema change = an Alembic migration (additive-first).
- Prefer `jsonb` for forward-compatible blobs (`raw` stats, `plan`, `params`, `predicate`).

### Git / MRs (see workspace rule `gitlab-workflow`)
- Conventional commits: `feat|fix|chore|docs|refactor|test`. Reference issues `#<id>`.
- Small, focused MRs. Update docs (`docs/`, `plan/hld/lld/todo/README`) when behavior changes.
- **Only commit when explicitly asked.**

---

## 5. Behavior you MUST preserve (parity checklist)

When touching scanner/planner/governor/engines, keep all of these (ported from today):

- Partition-aware OPTIMIZE from **pre-computed scan data** (no live `$files` scans on hot paths).
- **GB-budgeted chunking** + **max partitions/chunk** cap (avoid Trino `FilterNode` complexity).
- **Oversize partition runs alone**; **small-table bypass** (single full-table OPTIMIZE).
- Partition **transform handling**: identity/day/month/year + `$partitions` introspection.
- `min_small_files` gating; partitions ranked by small-file count DESC.
- **Load-aware admission** via `system.runtime.queries`; **per-replica caps** + **round-robin**.
- **Cooldown** (skip recently maintained), **off-peak** windows, **adaptive budget**, **deadlines**.
- **Failure isolation** per table; **per-table lock** (no double maintenance).
- Correct op order: `expire_snapshots → remove_orphan_files → optimize → optimize_manifests`.
- **A–F health grade** + score deductions (WARN −8 / CRIT −20) and **S3 cost** heuristic, unchanged.
- **Audit every action** (table, action, status, detail, engine, duration).
- Trino writer target configured at the Iceberg catalog level (`iceberg.target-max-file-size`);
  session tuning before Trino OPTIMIZE only uses supported session properties
  (`task_min_writer_count`, `writer_scaling_min_data_processed`).

If you must change any of these, call it out explicitly and update `lld.md` + parity tests.

---

## 6. Common commands

> The repo is being built out; create these via the `Makefile`/`docker-compose.yml` as you implement
> Phase 0. Until then, prefer the Makefile targets below as the canonical interface.

```bash
# Local full stack (datalake + Cryo deps + observability)
make up                 # all three compose files
make up-cryo            # Cryo platform only (prod-shaped)
make up-datalake        # MinIO + catalogs + Trino (LOCAL/CI only)
make down               # stop full local stack
make seed               # create demo Iceberg tables (scripts/)

# Backend
make install            # uv/pip install -e .[dev]
make migrate            # alembic upgrade head
make scan               # run scanner once against the local source
make api                # run FastAPI (uvicorn) locally
make worker             # run a worker
make governor           # run the governor
make scheduler          # run the scheduler

# Quality (run before finishing any change)
make lint               # ruff check + ruff format --check
make typecheck          # mypy
make test               # pytest (unit + parity); add -k for a subset

# Frontend
cd web && npm install && npm run dev      # Vite dev server
cd web && npm run build && npm run test
```

Use the specialized editor tools for files; reserve the shell for git/build/test/docker.

---

## 7. Definition of done (per change)
1. Code matches `lld.md` contracts and the parity checklist (§5).
2. `make lint && make typecheck && make test` pass; new logic has tests (planner/renderers = golden
   + parity tests).
3. New SQL paths covered for **both** engines if applicable.
4. Migrations included for schema changes; API changes reflected in OpenAPI (frontend types regen).
5. Docs updated (`docs/` as the persistent project brain, `todo.md` checkboxes, and
   `lld.md`/`hld.md` if behavior/contracts changed).
6. No secrets in logs/responses; destructive ops guarded by guardrails + `force` (audited).

---

## 8. Gotchas / footguns
- **Don't** issue full-table OPTIMIZE on large tables without partition filters — it hammers Trino.
  The Planner exists precisely to prevent this; route everything through it.
- **Don't** rely on worker replica count for concurrency — the **Governor** bounds real concurrency
  against live engine load. Scaling workers ≠ more parallel OPTIMIZEs.
- **Don't** read live `$files`/manifests on request paths — use the Scanner's stored stats.
- **Trino vs Spark op names differ**: Trino `EXECUTE optimize` ≈ Spark `rewrite_data_files`; Trino
  `optimize_manifests` ≈ Spark `rewrite_manifests`. Map via `OperationType`, render per engine.
- Partition values from the scan are **raw** (epoch-day ints for `day`, etc.). Convert in the
  renderer exactly as the dbt macro does.
- Leader-elected services (scheduler/governor): assume multiple replicas may start; guard with the
  Redis lease so only one is active.
- Time zones: off-peak windows are **IST**; compute carefully (engine probes/Postgres are UTC).
