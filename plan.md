# Cryo — Plan

> **Cryo** is a consolidated, self-hosted **lakehouse maintenance & health platform** for Apache
> Iceberg. It is a "Floe, but better": policy/preset-driven maintenance with first-class
> **table health**, **datalake (fleet) health**, **ad-hoc maintenance**, and a UI to slice the
> fleet and bulk-assign maintenance presets — while preserving the load-aware, partition-aware
> filtering that keeps our Trino clusters healthy today.
>
> *Cryo* is a working codename (a cryostat keeps things in a stable, cold state). Rename freely.

---

## 1. Why this project

Today our Iceberg maintenance is **scattered** across:

| Piece | What it does | Where it lives |
|-------|--------------|----------------|
| `dag_iceberg_table_stats_batch.py` | PyIceberg manifest scan → per-table/per-partition stats, health score (A–F), S3 cost estimate. Writes `cdp.admin.iceberg_compaction_scan` (Iceberg) + syncs to Postgres. | Airflow |
| `dag_iceberg_auto_maintenance_with_trino.py` | Wave-based scheduler: discovers targets, launches per-table dbt subprocesses, governs concurrency by reading live Trino load, round-robins replicas, deadline-aware. | Airflow |
| `pw_dbt_trino/macros/iceberg_*.sql` | The "brains": partition-aware OPTIMIZE, GB-budgeted chunking, transform detection, cooldown, off-peak, adaptive budget, drift detection, audit logging, health/cost/diagnostics reports. | dbt |
| `iceberg_maintenance_report.py` | Daily/weekly HTML email of maintenance + fleet health. | Airflow |

Problems: logic is split across Airflow + dbt + Python; there is no UI; engine choice (Trino only)
is hard-wired; there is no clean separation between *where tables live* and *where maintenance
runs*; ad-hoc maintenance means hand-editing dbt args.

**Goal:** consolidate all of this into **one deployable product** with a UI, an API, a pluggable
engine layer (Trino **and** Spark/EMR), and **no Airflow**. Scheduling, concurrency governance,
and orchestration move into the platform itself.

---

## 2. What we are building (scope)

### In scope (v1 → v2)
1. **Sources** (read side): connect to one or more **Iceberg catalogs** (REST, Hive Metastore,
   Glue, Polaris/Lakekeeper/Nessie). Discover tables, read metadata via PyIceberg.
2. **Destinations / Engines** (write side): submit maintenance to **Trino** (SQL) and **Spark on
   EMR** (Spark SQL). Sources and destinations are configured and stored **independently**.
3. **Scanner**: port `iceberg_table_stats_batch.py` into a first-class, chunked, resumable service
   (K8s `CronJob`) producing table + partition stats, health grade, and cost estimates.
4. **Presets** (policies, but better): named maintenance profiles that bind **operations + params
   + target engine + schedule + selection rules**. Example: `spark-large` = "tables > 100 GB →
   Spark/EMR, deep maintenance weekly".
5. **Assignment UX**: a table explorer with rich filters (schema, size, small-file count/%, grade,
   partitioning, write frequency, drift…), **filter → Select all matches → assign preset** (across
   pagination, not just the visible page), plus rule-based auto-matching with manual override.
6. **Planner**: engine-agnostic, partition-aware plan generation (the crown-jewel logic, ported
   from the dbt macros) → rendered to **Trino SQL** or **Spark SQL** by separate renderers.
7. **Governor / wave scheduler**: global admission control. Reads live engine load (Trino
   `system.runtime.queries`; EMR job slots), enforces concurrency caps, cooldown, off-peak windows,
   adaptive budget, deadlines, failure isolation per table.
8. **Ad-hoc maintenance**: trigger any operation on any table/selection on demand, with a dry-run
   "preview the exact SQL & estimated load" step.
9. **Health dashboards**: datalake (fleet) health + per-table health & audit timeline.
10. **Reporting & alerting**: scheduled HTML/email/Slack summaries (port existing report), plus
    in-app drift/cost alerts.
11. **Kubernetes deployment**: Docker images + Helm chart for all components.

### Explicitly preserved from today (non-negotiable "keep these")
- **Partition-aware OPTIMIZE** driven by pre-computed scan data (no live `$files` scans on hot paths).
- **GB-budgeted, partition-count-capped chunking** (avoids Trino `FilterNode` complexity errors).
- **Partition transform handling** (`identity/day/month/year`, `$partitions` introspection).
- **Small-table bypass** (single full-table OPTIMIZE when table fits one chunk).
- **Load-aware admission** via `system.runtime.queries`; per-replica concurrency + round-robin.
- **Cooldown** (skip recently maintained), **off-peak** windows, **adaptive budget**, **deadlines**.
- **Failure isolation** per table; **audit logging**; **drift detection**; **A–F health grade**;
  **S3 cost estimation**; **correct operation ordering** (expire → remove_orphan → optimize →
  optimize_manifests).

### Out of scope (v1)
- Non-Iceberg formats (Hudi/Paimon/Delta). Architecture stays format-pluggable but v1 = Iceberg.
- Streaming/continuous self-optimization (Amoro-style Flink optimizers). We do batch + scheduled +
  ad-hoc. (Continuous mode is a future phase.)
- Query acceleration / catalog serving (we are maintenance + observability, not a query gateway).
- Multi-tenant RBAC beyond basic auth + roles (kept simple in v1, designed to extend).

---

## 3. How the new product is used (target workflow)

1. **Connect a Source** — add the Iceberg catalog (e.g. Hive Metastore `thrift://…:9083` or REST).
2. **Connect Destinations** — add a Trino cluster (coordinators/replicas) and a Spark/EMR engine.
3. **Scanner runs** (scheduled) — populates fleet stats + health.
4. **Create Presets** — e.g.
   - `trino-standard`: optimize + expire on Trino, off-peak nightly, for tables ≤ 100 GB.
   - `spark-large`: rewrite_data_files + expire + remove_orphan on Spark/EMR, weekly, tables > 100 GB.
   - `small-files-blitz`: optimize-only for high small-file tables, morning window, high concurrency.
5. **Assign tables** — in the Table Explorer, filter (`schema = mview AND size_gb > 100`), **Select
   all matching → Assign `spark-large`**. Or let a preset's **rules** auto-match (and override per
   table when needed).
6. **Platform schedules & governs** — the Governor builds runs from due presets, admits jobs within
   live load caps, executes partition-aware maintenance on the chosen engine, logs everything.
7. **Observe** — dashboards for table/datalake health; live run console; drift & cost alerts;
   scheduled email/Slack reports.
8. **Ad-hoc** — anytime: select tables → "Run maintenance now" → preview plan/SQL → execute.

---

## 4. Key architecture decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Backend = Python 3.12 + FastAPI** | Reuse PyIceberg + trino client + boto3; async API; team familiarity. |
| D2 | **Frontend = React + TypeScript (Vite) + Ant Design + Recharts + TanStack Query** | AntD `Table` gives production-grade **column filtering + row selection + "select all across pages"** out of the box — a direct fit for the assignment UX. Recharts for health/cost charts. |
| D3 | **Metadata store = PostgreSQL** (SQLAlchemy + Alembic) | We already operate Postgres for `iceberg_compaction_tasks`; relational fits presets/assignments/jobs; JSONB for flexible stats/plan blobs. |
| D4 | **Sources ≠ Destinations** (separate entities, configs, tables) | Matches the requirement: catalogs (read) are independent from engines (write). A table is discovered from a Source; maintained on a Destination chosen by its preset. |
| D5 | **Engine-agnostic Planner + per-engine SQL renderers** | Keep the partition-aware planning in **one** place; keep **Trino SQL** and **Spark SQL** generation in **separate** modules (`engines/trino`, `engines/spark`). |
| D6 | **Spark target = EMR Serverless (primary), pluggable** | K8s-native, on-demand, no cluster babysitting. Adapter interface also allows EMR-on-EKS / Livy / Spark Connect later. (Assumption — see §7.) |
| D7 | **Governor pattern, not naive worker concurrency** | Concurrency limits are **global & external** (cluster-wide OPTIMIZE caps, EMR slots). A single admission controller reading live load is correct; dumb per-worker parallelism is not. |
| D8 | **Redis** for the job queue, distributed locks, live run state; **Postgres** for durable truth | Wave scheduler needs fast locks + ephemeral counters; durable audit stays in PG. |
| D9 | **No Airflow.** Internal scheduler (APScheduler in the Scheduler service) + K8s `CronJob` for the Scanner | Removes the Airflow dependency entirely as required. |
| D10 | **Stats stay dual-written**: Postgres (serving/UI) + optional Iceberg history table | UI reads Postgres; long-term history/trend can also live in Iceberg like today. |
| D11 | **Plans are previewable & dry-runnable** | Every job can render its exact SQL before execution — safety + the "preview" UX. |

---

## 5. Component map (one-liner each)

- **api** — FastAPI app: CRUD (sources/engines/presets/assignments), table explorer + bulk assign,
  ad-hoc triggers, dashboards, job monitoring (REST + WebSocket/SSE).
- **scanner** — PyIceberg fleet scan → stats/health/cost (K8s CronJob).
- **scheduler** — turns due presets into maintenance **runs**; enqueues per-table **jobs**.
- **governor** — global admission control + wave scheduling against live engine load.
- **worker** — executes a single table job on its engine (Trino or Spark), partition-aware, audited.
- **engines/** — `trino/` and `spark/` adapters (connection + SQL rendering), behind one interface.
- **planner** — engine-agnostic partition-aware plan builder (ported macros logic).
- **web** — React UI.
- **db** — Postgres schema + Alembic migrations; Redis for queue/locks/live-state.

See `hld.md` for the diagram and data flow; `lld.md` for schemas, interfaces, and SQL.

---

## 6. Phased roadmap

> Detailed checklist lives in `todo.md`. Phases are sequential but overlap where safe.

- **Phase 0 — Foundations**: repo layout, config, Postgres schema + migrations, settings,
  Source/Destination connection abstractions, local docker-compose (MinIO + REST + Trino + Spark +
  Postgres + Redis), CI.
- **Phase 1 — Scanner**: port `iceberg_table_stats_batch.py` → service; persist stats/health/cost;
  chunked + resumable; CronJob. Fleet visible via API.
- **Phase 2 — Planner + Trino engine**: port partition-aware OPTIMIZE + lifecycle ops to the
  engine-agnostic planner + Trino renderer; single-table execution with full safety filters.
- **Phase 3 — Governor + Scheduler**: wave scheduler, load-aware admission, cooldown/off-peak/
  budget/deadline, per-replica round-robin; preset-driven scheduled runs; audit log.
- **Phase 4 — Presets & assignment API**: preset CRUD, rule engine, explicit assignments, effective-
  preset resolution, ad-hoc trigger + dry-run/preview.
- **Phase 5 — Web UI**: table explorer (filter → select-all → assign), preset editor, run console,
  table & datalake health dashboards, ad-hoc + preview.
- **Phase 6 — Spark/EMR engine**: Spark SQL renderer + EMR Serverless adapter; size-based routing
  (the `spark-large` preset); EMR slot governance.
- **Phase 7 — Reporting/alerting + hardening**: email/Slack reports (port existing), drift/cost
  alerts, metrics (Prometheus), Helm chart polish, docs, e2e tests.

---

## 7. Assumptions & open questions

Reasonable defaults chosen; flag to change:

1. **Spark execution target = EMR Serverless** (primary). If you prefer EMR-on-EKS, EMR-on-EC2 +
   Livy, or Spark Connect, the engine interface supports it — tell me which to implement first.
2. **Catalog for Spark maintenance**: Spark needs Iceberg catalog access (Glue/REST/HMS) matching
   the Source. Assumed reachable from EMR with the same warehouse/S3.
3. **Auth**: v1 = simple username/password + role (admin/operator/viewer) with JWT; can swap to
   OIDC/SSO. Confirm your SSO if needed.
4. **Single logical fleet** initially (one primary catalog `cdp`), but the model supports N sources.
5. **Postgres is provided** (managed/RDS) in prod; local docker-compose ships one for dev.
6. **Frontend** = React + AntD as in D2. If you'd rather have a Python-only UI (e.g. Streamlit/
   Reflex) to avoid a JS toolchain, say so — but React gives the best fit for the bulk-select UX.
7. **Project name** `Cryo` is a placeholder.

---

## 8. Success criteria

- One deployable product replaces the two DAGs + dbt macros + report script (Airflow removed).
- Trino load stays bounded: no regression vs today's partition-aware, load-governed behavior.
- A user can, in the UI: filter the fleet, select-all matches, assign a preset, and see scheduled +
  ad-hoc maintenance execute on the right engine with full audit + live status.
- Tables > 100 GB route to Spark/EMR via preset rules; everything else stays on Trino.
- Table health, datalake health, drift, and cost are visible and trustworthy.
