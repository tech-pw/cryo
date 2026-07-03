# Cryo — TODO

Phased, actionable checklist. Mirrors the roadmap in `plan.md` §6. Check items off as you go and keep
this file honest. `P0`…`P7` = phase. Each phase ends with a usable increment.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `(parity)` must match the reference impl.

---

## P0 — Foundations
- [~] Initialize git repo; add `pyproject.toml` (ruff, mypy, pytest), `Makefile`, `.editorconfig`.
- [x] Backend package skeleton `cryo/` per `lld.md` §1 (empty modules + `__init__`).
- [x] `cryo/config.py` (pydantic-settings) — env-driven infra config.
- [x] Postgres: SQLAlchemy base/session; Alembic init; first migration for **all** tables in
      `lld.md` §3 (sources, destinations, tables, table_stats, table_partitions, history, presets,
      preset_rules, table_assignments, runs, jobs, audit_log).
- [ ] Redis client + `queue/locks.py` (leader lease + per-table lock) + `queue/redis_queue.py`.
- [x] Domain enums + DTOs (`cryo/domain`): OperationType, EngineType, Mode, PartitionTransform,
      Operation, PartitionPredicate, MaintenancePlan, CapacitySnapshot.
- [ ] Field-level secret encryption helper (Fernet/KMS) for source/destination credentials.
- [x] Local stack `docker-compose.yml`: MinIO, Iceberg REST, Trino, **Spark**, Postgres, Redis.
- [x] Port/keep demo seeding (`scripts/create_test_tables.py`) for local Iceberg tables.
- [ ] CI: lint + typecheck + unit tests on push.
- [x] `Makefile` targets from `agents.md` §6.

## P1 — Scanner (fleet stats & health)
- [x] `sources/base.py` + `rest.py`/`hive.py`/`glue.py` (PyIceberg `load_catalog`) + `factory.py`.
- [x] `scanner/analyze.py` — PyIceberg manifest stream → stats dict. (parity: file/size/record,
      per-partition small files, delete files, manifests, properties, transform, partition detail).
- [x] `scanner/health.py` — score + A–F grade. (parity: WARN −8 / CRIT −20 deductions, thresholds).
- [x] `scanner/cost.py` — snapshot-overhead + `$0.023/GB-mo` estimate. (parity).
- [x] `needs_compaction` + `compaction_priority` formulas. (parity).
- [x] `scanner/run.py` — deterministic chunking, skip-already-scanned-today, `full_scan`, batched
      upserts to `tables`/`table_stats`/`table_partitions`/`table_stats_history`. Idempotent.
- [ ] (optional) dual-write Iceberg history table for parity with `iceberg_compaction_scan`.
- [ ] K8s `CronJob` manifest (parallel Job, `num_chunks`).
- [x] Tests: golden stats on seeded tables; health-score parity vs reference values.

## P2 — Planner + Trino engine (single-table execution)
- [x] `planner/partitions.py` — transform detection (`_day/_month/_year` + `$partitions` probe) and
      `PartitionPredicate` building (epoch-day/month/year/identity math). (parity).
- [x] `planner/chunking.py` — greedy GB-budget packer, max parts/chunk, oversize-alone, max_chunks. (parity).
- [x] `planner/planner.py` — mode→operations, unpartitioned/small-table/large paths, min_small_files. (parity).
- [x] `engines/base.py` — `MaintenanceEngine` interface (probe/render/execute/session_setup).
- [ ] `engines/trino/client.py` — per-coordinator connection (HTTPS + BasicAuth).
- [ ] `engines/trino/load.py` — `system.runtime.queries` OPTIMIZE probe. (parity).
- [x] `engines/trino/sql.py` — render OPTIMIZE (chunked/filtered), expire, orphan, manifests +
      session SETs. (parity). **Golden tests.**
- [ ] `engines/trino/engine.py` — execute + capture query id + errors.
- [~] Tests: planner unit tests; Trino renderer golden tests; parity vs dbt-rendered SQL samples;
      contract test against local Trino on seeded tables (asserts file-count drop).

## P3 — Governor + Scheduler (load-aware orchestration)
- [x] `governor/admission.py` — capacity math: caps, live vs local in-flight, off-peak gate,
      adaptive budget, cooldown, deadline. (parity).
- [~] `governor/governor.py` — wave loop, round-robin replicas, per-table lock handoff, dispatch,
      reap, finalize run summary. (parity).
- [~] `worker/worker.py` — claim job → load stats → plan → engine execute → audit → counters.
- [x] `scheduler/rules.py` — `effective_preset(table)` + `tables_for_preset(preset)` resolution.
- [~] `scheduler/scheduler.py` — cron eval → create run + jobs → enqueue; cooldown pre-filter;
      priority order; leader-elected.
- [~] Audit log writes for every action (supersedes `iceberg_maintenance_log`).
- [~] Tests: governor admission math; wave behavior with a fake engine; end-to-end happy path on the
      local stack (scan → schedule → run → audit).

## P4 — Presets & assignment API
- [x] Repositories + Pydantic schemas for sources, destinations, presets, rules, assignments.
- [x] Predicate grammar (`lld.md` §6.2): JSON → SQLAlchemy filter (explorer/selection) + in-memory
      evaluator (rule matching). Shared module.
- [x] FastAPI app factory + auth (JWT, roles admin/operator/viewer) + deps.
- [x] Routers: `/sources`, `/destinations` (+ `/test`), `/presets` (+ `/simulate`),
      `/presets/{id}/rules`, `/assignments` (filter/ids selection), `/tables/{id}/effective-preset`.
- [x] `/maintenance/preview` (dry-run → per-table rendered SQL + estimates) and `/maintenance/run`.
- [x] `/scans` trigger; `/runs`, `/jobs`, `/runs/{id}/cancel`.
- [x] Tests: selection/assignment transactional correctness (filter selects across pages);
      effective-preset resolution (pinned > rule-priority > none); preview renders without executing.

## P5 — Web UI
- [x] Vite + TS + AntD + Recharts + TanStack Query scaffold; OpenAPI client generation.
- [x] **Table Explorer**: server-side filter/sort/paginate; column filters → predicate grammar;
      `rowSelection` + "select all matching filter"; sticky **BulkAssignBar** (Assign / Run now).
- [x] **PlanPreview** modal (per-table Trino/Spark SQL + chunk/partition/load estimate).
- [x] **Presets** editor (operations/params/schedule/rules/guardrails) + **Simulator**.
- [x] **Runs / Live Console**: WebSocket; per-engine concurrency gauges; job rows; log drawer.
- [x] **Datalake Health** dashboard (grade dist, cost by schema, drift, top offenders, totals).
- [x] **Table Detail** (health card, partitions, snapshot/manifest stats, audit timeline, Run).
- [x] **Sources & Destinations** management + health checks.
- [~] WebSocket endpoint `/ws/runs/{id}` streams DB snapshots; Redis live-state fanout pending.

## P6 — Spark / EMR engine (size-based routing)
- [x] `engines/spark/sql.py` — render `rewrite_data_files` (where + options), `expire_snapshots`,
      `remove_orphan_files`, `rewrite_manifests` from the **same** plan. **Golden tests.**
- [ ] `cryo_spark_runner.py` — PySpark entrypoint executing CALL statements with Iceberg SparkSession.
- [ ] `engines/spark/emr.py` — EMR Serverless submit/poll/logs (boto3); capacity = slot cap − in-flight.
- [ ] `engines/spark/engine.py` — implement `MaintenanceEngine`; wire into worker + governor.
- [ ] Governor EMR slot governance; preset rule `size_gb > 100 → spark-large`.
- [~] Tests: Spark renderer golden tests; contract test against local Spark on seeded tables;
      routing test (large table → Spark, small → Trino).

## P7 — Reporting, alerting & hardening
- [ ] `reporting/report.py` — port HTML daily/weekly email (KPIs, schema/cost, drift, top offenders).
- [ ] Slack notifier + in-app drift/cost alerts (`reporting/alerts.py`).
- [ ] Prometheus metrics (jobs, admission, live load, scan coverage, fleet grade, cost, drift).
- [ ] Structured JSON logging across services; per-job live log stream.
- [ ] Helm chart `deploy/helm/cryo` (api, web, scheduler, governor, worker, scanner CronJob, redis;
      Postgres external) + values + secrets/configmaps + Ingress + HPAs + leader leases.
- [ ] Security pass: secret handling, RBAC, destructive-op guardrails + `force` audit.
- [ ] e2e smoke (compose): scan → assign → run (Trino + Spark) → assert audit + file-count drop.
- [ ] Docs: deployment guide, operator runbook, update `README.md`/`hld.md`/`lld.md` as needed.

---

## Cross-cutting / backlog
- [ ] AuthN via OIDC/SSO (swap from local JWT) — pending SSO confirmation.
- [ ] Additional source adapters (Polaris/Lakekeeper/Nessie) beyond REST/HMS/Glue.
- [ ] Additional Spark targets (EMR-on-EKS / Livy / Spark Connect) behind the engine interface.
- [ ] Continuous/streaming self-optimization mode (Amoro-style) — future phase.
- [ ] Format-pluggability groundwork for Hudi/Paimon/Delta (Iceberg only in v1).
- [ ] Cost model refinement (request/list costs, not just storage); savings-after-maintenance.
- [ ] Saved filters / scheduled saved views in the Explorer.

## Open decisions to confirm (see `plan.md` §7)
- [ ] Spark execution target: **EMR Serverless** (assumed) vs EMR-on-EKS / Livy / Spark Connect.
- [ ] Auth: local JWT (assumed v1) vs OIDC/SSO from day one.
- [ ] Frontend: React + AntD (assumed) vs Python-only UI (Streamlit/Reflex).
- [ ] Project name (`Cryo` placeholder).
