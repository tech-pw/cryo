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
   `docs/research-and-roadmap.md Part 1`, `docs/research-and-roadmap.md Part 4`, and
   `docs/architecture.md` into an ordered, actionable plan.
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
- Reuse `web/src/components/ui/` Radix primitives and the shared TanStack `DataTable`; do not add
  Ant Design or one-off table implementations.
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
- Correct op order: `convert_deletes → rewrite_position_deletes → optimize → compute_statistics →
 optimize_manifests → expire_snapshots → remove_orphan_files`. Compact first, clean up after, so a
 single run reclaims the space its own compaction freed. `cryo/planner/guardrails.py::CORRECT_ORDER`
 is the only definition; `web/src/lib/operationOrder.ts` mirrors it.
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

---

## Learned User Preferences

- When given broad “implement all phases / don’t ask / fix everything” mandates, keep going to
  completion without pausing for confirmation between phases.
- AI providers (Anthropic/OpenAI/Azure) and Slack belong on the **Connections** page — not buried
  in settings or env-only wiring; S3 connections should offer IAM role / IRSA-style auth, not only
  static keys.
- For research and Slack/report format work, ground recommendations in official docs and real
  channel/data evidence — not impressions; land durable format guidance under `docs/`. Trust live
  cluster/API/Grafana evidence over written docs when they disagree.
- When a scoping or implementation doc marks items as **Scoped out**, leave those alone unless
  explicitly asked to expand scope.
- Workload-aware research build order (when investing in that track): finish **AutoComp**
  (highest near-term ROI) → **PTO** (biggest net-new) → WAIR / Okolnychyi secondaries; master spec
  is `docs/implementation/workload-aware-optimization.md`. That track is backlog relative to
  making core maintenance actually run.
- **Standing directive, added 2026-08-06: check Cryo's own infrastructure cost every day and keep
  fixing until fully optimized — this is ongoing, not a one-time pass.** Don't wait to be asked;
  proactively pull the numbers each session and investigate anything that looks off. See the
  "Cost self-check" entry below for the concrete how-to and what's already been fixed.
- Cryo is judged by whether it actually maintains Iceberg and moves Trino/S3 cost, not by new
  features. Near-term: run `optimize`, `expire_snapshots`, `remove_orphan_files`,
  `optimize_manifests` daily on the hottest Trino tables (top ~500). Prefer simplifying the
  execution path (queue / scanner / governor) over adding intelligence or competitor-feature
  work; the 2026-09-22 pass on those three is written up in
  `docs/QUEUE_SCANNER_GOVERNOR_OVERHAUL_2026-09-22.md`.
- Frontend should feel like premium operator SaaS: runs/scans like Airflow 3.x DAG/run debug
  (status, logs, a merged operations list); table detail like OpenMetadata.
- When given a file path, read the file first — the filename is not the topic.
- When editing Confluence pages, never strip existing images or charts.
- On PDAE/Jira Cryo tickets: post evidence-backed progress comments as you go; do not close
  tickets yourself (leave Dev Complete); do not silently expand scope onto a ticket another
  session owns.

## Learned Workspace Facts

- Public GitHub remote is `https://github.com/shelf-project/Cryo` (`origin`); GitLab remote
  `gitlab` points at `example-org/data/data-engineering/cryo` (often worked on branch
  `gitlab-main`).
- Production Cryo: kubectl context **data-platform-cluster**, namespace **cryo**. Grafana
  namespace-cost dashboards filter `var-cluster_name=data-platform-eks` (cost-allocation tag,
  not the kube context) — do not guess a generic default cluster. Live API: `kubectl
  port-forward -n cryo svc/cryo-api <local>:8000`, then `POST /api/v1/auth/token` with
  `{"role":"admin"}` (`CRYO_ALLOW_DEV_TOKEN_IN_PROD=true`). Production deploys go through
  `deploy/deploy.sh` (test gate → multi-arch push → pin **one** digest into all three
  `values-prod.yaml` image fields → helm upgrade); do not hand-edit a single digest.
- `EngineType` includes `trino`, `spark_emr`, `native`, and `flink`; **native** is the Go
  compaction path — UI/run presets should expose it when configured, not Trino-only. In
  current prod `values-prod.yaml`, `CRYO_NATIVE_ENGINE_ENABLED=false` (native K8s dispatch
  off; Spark/Trino go through workers) and `CRYO_SCAN_ENGINE=go`.
- Slack daily-report structure guidance lives in `docs/SLACK_REPORT_FORMAT_GUIDE.md`; native-engine
  scope/gaps are tracked in `docs/native-engine-scoping-research.md`.
- Job-store list methods (`list_queued_jobs` / `list_running_jobs` / `list_admitted_jobs`) must
  stay **bounded, oldest-first**. Unbounded scans of those lists, called on a timer from every
  worker, OOMed the fleet (~1GB/5s heap). Governor reconcile has the same trap — never add a
  new periodic full-table job-list.
- Governor engine-slot admission is not worker drain capacity. A worker runs one job at a time,
  so the fleet drains at roughly (replicas / job duration); admitting against engine capacity
  alone buries the claim queue under work no worker can reach. Scheduled runs need a table-count
  cap (`budget_hint` / `max_jobs_per_scheduled_run`), and a waiting-but-healthy backlog must
  never be treated as a failed dispatch — inferring that from age alone is what recorded 5,952
  of one run's 6,117 jobs as failures on 2026-09-21. Since 2026-09-22 the backpressure signal is
  the admitted stream's undelivered depth and worker presence comes from the Redis Streams
  consumer group, so `stuck_admitted_no_dispatch` no longer exists as an error.
- Trino "hot table" ranking should read `QueryLog` first, but a Source with
  `query_log.mode == "iceberg_table"` writes `table_query_activity` and **no** `query_logs` rows.
  Ranking must fall back to `table_query_activity` or the top-N preset silently unpins.
- **Cost self-check, added 2026-08-06** (see the standing directive above): Cryo's own AWS EKS
  spend for its `cryo` namespace on the `data-platform-eks` cluster is visible two ways —
  - **Grafana dashboard**: `https://grafana.example.com`
    (`var-cluster_name=data-platform-eks&var-namespace=cryo`). Shows `used_cost` vs `unused_cost`
    (requested-but-idle capacity) split by workload.
  - **Direct query**: the same numbers come from AWS's EKS Split Cost Allocation Data via the
    `grafana-athena-datasource` Grafana datasource (uid `fe0bimj2kv9xca`), querying the `cur` table's
    `split_line_item_split_cost` (used) / `split_line_item_unused_cost` (wasted) columns, filtered by
    `resource_tags_aws_eks_namespace = 'cryo'`. Call it via Grafana's `/api/ds/query` proxy (see the
    2026-08 session transcript for the exact `rawSQL` payload shape) so you don't need direct AWS
    creds. **Gotcha**: ephemeral Kubernetes Jobs/Pods (Cryo's native + Spark dispatch pods) don't
    carry the `eks:workload-name`/`eks:deployment` tags a Deployment gets, so their cost is invisible
    in the normal per-service breakdown — it all lands in a `resource_tags_aws_eks_workload_name = ''`
    bucket. Check that bucket specifically; don't conclude "no waste" just because the named-workload
    rows look clean.
  - **Ground-truth actual usage**: `kubectl top pods -n cryo` on live dispatch pods, compared against
    their configured CPU/mem *requests* (not limits) — the gap between the two, multiplied across
    however many pods run per day, is the real waste, and it's what the `unused_cost` column above
    is billing for.
  - **Already fixed this way (2026-08-06)**: native engine's rewrite "packing floor" was sizing every
    table's pod to ~5.3GiB regardless of actual table size (`cryo/engines/native/resources.py`,
    `packing_floor_bytes`/`mem_mib_for_group_packing` — now capped by `table_total_bytes`); Spark
    fallback driver/executor static sizing was ~10x observed usage (`values-prod.yaml`); 3,276
    orphaned Spark driver pods had no cleanup path (`cryo.engines.spark.k8s.cleanup_stale_driver_pods`,
    wired into `GovernorRunner._tick`) — needed an RBAC fix too (`governor-rbac.yaml`, pods needed
    `delete` not just `get`/`list`).
  - **Not yet fixed, known gap**: Spark fallback sizing is still one static value for every table,
    unlike the native engine's already-dynamic per-table sizing — doesn't generalize once Cryo runs
    against a fleet shaped differently from this one. Extend `estimate_table_resources`-style sizing
    (driven by `manifest_count`/`total_size_gb`/`total_snapshots`) to the Spark dispatch path next.
  - **Success metric + daily tracking, added 2026-08-06**: the goal is this month's cost lower than
    last month's, **with statistical significance** — not just a number that moved. Full methodology,
    the daily log, and a zero-dependency month-over-month comparison script (Welch's t-test, refuses
    to call anything significant with <7 days of current-month data) live in
    `docs/cost-tracking/README.md` / `daily-cost-log.csv` / `compare_mom.py`. **Whenever you check
    cost — asked or self-initiated — append the day's numbers to `daily-cost-log.csv` before
    reporting them**; that's what makes "check daily" actually accumulate a real trend instead of
    depending on a conversation's context.  Two namespaces tracked: `trino-db` (fleet query/compute
    efficiency) and `cryo` (Cryo's own footprint — judge by *unused_cost ratio* trending down, not
    raw total, since real fleet work legitimately costs more to run than doing nothing).
