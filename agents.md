# AGENTS.md — Guide for AI Agents working on Cryo

This file orients an AI coding agent (and humans) working in this repo. Read it before making
changes. For *how* Cryo is built, read `docs/design/hld.md` and `docs/design/lld.md` first.

---

## 1. What this repo is

**Cryo** — a self-hosted **Apache Iceberg maintenance & health platform**. It consolidates Iceberg
table maintenance (compaction/optimize, snapshot expiry, orphan-file removal, manifest rewrite),
datalake health/observability, and ad-hoc maintenance behind a Python backend + React UI, deployable to
Kubernetes. It replaces a set of Airflow DAGs + dbt macros (see `docs/design/hld.md` §9 for the mapping).

**Hard requirements (do not violate):**
- **No Airflow.** Scheduling/orchestration lives in the platform (`scheduler/` + `governor/`).
- **Sources (read) and Destinations/engines (write) are separate** — separate DB tables, configs,
  and code. Never couple catalog access to engine execution.
- **Trino SQL and Spark SQL generation are separate** (`engines/trino/sql.py` vs
  `engines/spark/sql.py`). The Planner is engine-agnostic and emits **no SQL strings**.
- **Preserve the load-aware, partition-aware behavior** that protects Trino today (see §5).

---

## 2. Source-of-truth docs (read order)

1. `docs/design/hld.md` — components, data flow, deployment, separation of concerns.
2. `docs/design/lld.md` — schemas, interfaces, planner algorithm, rendered SQL, API surface.
3. `docs/CAPABILITIES.md` — what ships today (generated; edit the capability manifest, not the Markdown).
4. `docs/README.md` — index of the docs. Internal planning files (`plan.md`, `todo.md`) live in the
   internal repository only and are excluded from the public mirror
   (`scripts/oss_mirror_exclude_paths.txt`).
5. `docs/` — persistent project brain. Always keep it current with fixes, implementations,
   new features, design decisions, operational notes, and any project knowledge future agents
   or maintainers will need. If you're picking up work with no other context, start at
   `docs/research-and-roadmap.md` (Parts 1 and 4) and `docs/architecture.md`.
6. This file — conventions & guardrails.
7. [`docs/LOCAL_DEV_ONLY_INVENTORY.md`](./docs/LOCAL_DEV_ONLY_INVENTORY.md) — ad-hoc local datalake
   components that must not ship to prod; **update in the same PR** when adding new dev-only scripts
   or compose services.

When in doubt about maintenance semantics, match the planner and engine contracts in `docs/design/lld.md`.

---

## 3. Project layout (where things go)

See `docs/design/lld.md` §1 for the full tree. Quick map:

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
| Frontend | `web/` | React + TS + Vite + Radix UI + Tailwind + Recharts + TanStack. |
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
- Naming mirrors `docs/design/lld.md` (`MaintenancePlan`, `Operation`, `PartitionPredicate`, `OperationType`,
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
- Reuse `web/src/components/ui/` Radix primitives and the shared TanStack `DataTable`; do not add
  Ant Design or one-off table implementations.
- Bulk selection sends a **filter spec**, never a list of page IDs (see `docs/design/lld.md` §12/§13).

### Database
- All tables in schema `cryo`. Every schema change = an Alembic migration (additive-first).
- Prefer `jsonb` for forward-compatible blobs (`raw` stats, `plan`, `params`, `predicate`).

### Git / MRs
- Conventional commits: `feat|fix|chore|docs|refactor|test`. Reference issues `#<id>`.
- Small, focused MRs. Update docs (`docs/`, `docs/design/hld.md`, `docs/design/lld.md`, `README.md`) when behavior changes.
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
- Correct op order: `convert_equality_deletes → convert_deletes → rewrite_position_deletes → optimize → compute_statistics →
 optimize_manifests → expire_data → expire_snapshots → remove_orphan_files`. Compact first, clean up after, so a
 single run reclaims the space its own compaction freed. `cryo/planner/guardrails.py::CORRECT_ORDER`
 is the only definition; `web/src/lib/operationOrder.ts` mirrors it.
- **A–F health grade** + score deductions (WARN −8 / CRIT −20) and **S3 cost** heuristic, unchanged.
- **Audit every action** (table, action, status, detail, engine, duration).
- Trino writer target configured at the Iceberg catalog level (`iceberg.target-max-file-size`);
  session tuning before Trino OPTIMIZE only uses supported session properties
  (`task_min_writer_count`, `writer_scaling_min_data_processed`).

If you must change any of these, call it out explicitly and update `docs/design/lld.md` + parity tests.

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
make scan               # run one-shot batch scanner against local sources
make scanner            # run ad-hoc scan queue consumer
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
1. Code matches `docs/design/lld.md` contracts and the parity checklist (§5).
2. `make lint && make typecheck && make test` pass; new logic has tests (planner/renderers = golden
   + parity tests).
3. New SQL paths covered for **both** engines if applicable.
4. Migrations included for schema changes; API changes reflected in OpenAPI (frontend types regen).
5. Docs updated (`docs/` as the persistent project brain, and
   `docs/design/lld.md`/`docs/design/hld.md` if behavior/contracts changed).
6. No secrets in logs/responses; destructive ops guarded by guardrails + `force` (audited).

---

## 8. Operator help

Day-to-day install and debug help for people using Cryo lives in [`docs/operator/`](./docs/operator/README.md),
with a read-only debug agent at [`.agents/agents/cryo-debug.md`](./.agents/agents/cryo-debug.md).
Those files are procedures. This file stays the contributor guardrails. Do not paste a runbook
here. `.claude/` and `.agents/skills/` are gitignored local state. Do not put a shared procedure there.

---

## 9. Gotchas / footguns
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
- Time zones: off-peak windows use the deployment timezone. Engine probes and Postgres are UTC.

---

## Learned User Preferences

- When given broad “implement all phases / don’t ask / fix everything” mandates, keep going to
  completion without pausing for confirmation between phases.
- AI providers (Anthropic/OpenAI/Azure) and Slack belong on the **Connections** page — not buried
  in settings or env-only wiring; S3 connections should offer IAM role / IRSA-style auth, not only
  static keys.
- For research and Slack/report format work, ground recommendations in official docs and real
  channel/data evidence — not impressions; land durable format guidance under `docs/`.
- When a scoping or implementation doc marks items as **Scoped out**, leave those alone unless
  explicitly asked to expand scope.
- Workload-aware research build order (when investing in that track): finish **AutoComp**
  (highest near-term ROI) → **PTO** (biggest net-new) → WAIR / Okolnychyi secondaries; master spec
  is `docs/implementation/workload-aware-optimization.md`. That track is backlog relative to
  making core maintenance actually run.
- Cryo is judged by whether it actually maintains Iceberg, not by new features. Prefer simplifying
  the execution path (queue / scanner / governor) over adding intelligence.
- Frontend should feel like premium operator SaaS: runs/scans like Airflow 3.x DAG/run debug
  (status, logs, a merged operations list); table detail like OpenMetadata.
- When given a file path, read the file first — the filename is not the topic.

## Learned Workspace Facts

- Public GitHub remote is `https://github.com/tech-pw/cryo`.
- `EngineType` includes `trino`, `spark_emr`, `native`, and `flink`; **native** is the Go
  compaction path — UI/run presets should expose it when configured, not Trino-only.
- Slack daily-report structure guidance lives in `docs/SLACK_REPORT_FORMAT_GUIDE.md`; native-engine
  scope/gaps are tracked in `docs/native-engine-scoping-research.md`.
- Job-store list methods (`list_queued_jobs` / `list_running_jobs` / `list_admitted_jobs`) must
  stay **bounded, oldest-first**. Unbounded scans of those lists, called on a timer from every
  worker, can OOM the fleet. Governor reconcile has the same trap — never add a new periodic
  full-table job-list.
- Governor engine-slot admission is not worker drain capacity. A worker runs one job at a time,
  so the fleet drains at roughly (replicas / job duration); admitting against engine capacity
  alone buries the claim queue under work no worker can reach. Scheduled runs need a table-count
  cap (`budget_hint` / `max_jobs_per_scheduled_run`); the remainder is reached by backlog
  batches (`scheduler_backlog_drain_*`) that fire only once the preset's in-flight jobs fall
  to the low-water mark, never by raising the cap. A waiting-but-healthy backlog must
  never be treated as a failed dispatch. The backpressure signal is the admitted stream's
  undelivered depth, and worker presence comes from the Redis Streams consumer group.
- Trino "hot table" ranking should read `QueryLog` first, but a Source with
  `query_log.mode == "iceberg_table"` writes `table_query_activity` and **no** `query_logs` rows.
  Ranking must fall back to `table_query_activity` or the top-N preset silently unpins.
