# Cryo — TODO

Phased, actionable checklist. Mirrors the roadmap in `plan.md` §6 and the sequenced work in
`docs/EXECUTION_PLAN.md`. Check items off as you go and keep this file honest. `P0`…`P7` = original
phase plan; `P8+` = post-MVP extensions from `research-and-roadmap.md` Part 1 and the 2026 feature roadmap.

Legend: `[ ]` todo · `[~]` in progress / partial · `[x]` done · `(parity)` must match the reference impl.

Last reconciled: 2026-07-04 (against `docs/current-state.md`, `docs/EXECUTION_PLAN.md`, codebase).

---

## P0 — Foundations
- [x] Initialize git repo; add `pyproject.toml` (ruff, mypy, pytest), `Makefile`, `.editorconfig`.
- [x] Backend package skeleton `cryo/` per `lld.md` §1 (empty modules + `__init__`).
- [x] `cryo/config.py` (pydantic-settings) — env-driven infra config.
- [x] Postgres: SQLAlchemy base/session; Alembic init; first migration for **all** tables in
      `lld.md` §3 (sources, destinations, tables, table_stats, table_partitions, history, presets,
      preset_rules, table_assignments, runs, jobs, audit_log).
- [x] Redis client + `queue/locks.py` (leader lease + per-table lock) + `queue/redis_queue.py`
      (+ `queue/scan_queue.py`, `queue/reconcile.py`); wired via `cryo/infra/wiring.py`.
- [x] Domain enums + DTOs (`cryo/domain`): OperationType, EngineType, Mode, PartitionTransform,
      Operation, PartitionPredicate, MaintenancePlan, CapacitySnapshot.
- [~] Field-level secret encryption helper (Fernet/KMS) for source/destination credentials — schema
      fields exist (`secret_ref`, `*_encrypted`); `cryo/api/extensions.py` still has TODO encrypt/decrypt.
- [x] Local compose split: `docker-compose.yml` (Cryo platform), `docker-compose.dev-datalake.yml`
      (MinIO, Iceberg REST, HMS, Trino, Spark — LOCAL/CI only), `docker-compose.observability.yml`.
- [x] Port/keep demo seeding (`scripts/create_test_tables.py`) for local Iceberg tables.
- [x] Local HMS integration: `hive-metastore` + `hms-postgres`, `tests/integration/hms/` fixture
      generator (MinIO-only data), `local-hms` source seed, opt-in `test_hms_catalog_integration.py`.
- [x] CI: lint + typecheck + unit tests on push (`.github/workflows/ci.yml`).
- [x] `Makefile` targets from `agents.md` §6 (+ `smoke-compact`, `start-local`, reporting targets).

## P1 — Scanner (datalake stats & health)
- [x] `sources/base.py` + `rest.py`/`hive.py`/`glue.py` (PyIceberg `load_catalog`) + `factory.py`.
- [x] `scanner/analyze.py` — PyIceberg manifest stream → stats dict. (parity: file/size/record,
      per-partition small files, delete files, manifests, properties, transform, partition detail).
- [x] `scanner/health.py` — score + A–F grade. (parity: WARN −8 / CRIT −20 deductions, thresholds).
- [x] `scanner/cost.py` — snapshot-overhead + `$0.023/GB-mo` estimate. (parity).
- [x] `needs_compaction` + `compaction_priority` formulas. (parity).
- [x] `scanner/run.py` — deterministic chunking, skip-already-scanned-today, `full_scan`, batched
      upserts to `tables`/`table_stats`/`table_partitions`/`table_stats_history`. Idempotent.
- [ ] (optional) dual-write Iceberg history table for parity with `iceberg_compaction_scan`.
- [x] Scanner scheduling moved from K8s `CronJob` values to Cryo-managed scanner config/API
      (parallel chunk queue preserves `num_chunks` behavior).
- [x] Tests: golden stats on seeded tables; health-score parity vs reference values.
- [x] **Cost accounting (Tier 1)**: extend `scanner/cost.py` with metadata byte accounting
      (live/dead/metadata split via retained-snapshot manifest walk, deduped + bounded) → exact
      expire/optimize/manifest reclaim. No data reads / no LIST. (see `lld.md` §16)
- [x] `cost/model.py` (pure): prices, `bytes_to_usd`, `project_savings()` → `SavingsProjection`;
      `CostPrecision` enum + `StorageBreakdown`/`SavingsProjection` DTOs. Golden + heuristic-parity tests.
- [x] **Orphan tier (Tier 2)**: `scanner/orphans.py` — bounded, paginated storage LIST → `orphan_bytes`;
      opt-in (`--cost-full-scan`), age-guarded, cadence-gated; carry-forward last-known when skipped.
- [x] Migration (additive): `cryo.table_cost` + `cryo.table_cost_history` + `table_stats` cost summary
      columns + `audit_log.actual_bytes_freed`; persist from `scanner/run.py`.
- [x] **Scanner concurrency** (`research-and-roadmap.md` Part 1 §1.2): parallel manifest reads
      (`CRYO_SCAN_PARALLEL_MANIFESTS`) + parallel table scans (`CRYO_SCAN_PARALLEL_TABLES`).
- [x] **Health signals** (`scanner/signals.py`): `metadata_to_data_ratio`, streaming/commit-velocity
      (`is_streaming`), V2/V3 format detection + upgrade candidates.

## P2 — Planner + Trino engine (single-table execution)
- [x] `planner/partitions.py` — transform detection (`_day/_month/_year` + `$partitions` probe) and
      `PartitionPredicate` building (epoch-day/month/year/identity math). (parity).
- [x] `planner/chunking.py` — greedy GB-budget packer, max parts/chunk, oversize-alone, max_chunks. (parity).
- [x] `planner/planner.py` — mode→operations, unpartitioned/small-table/large paths, min_small_files,
      hot-partition skip, delete-ratio trigger, `COMPUTE_STATISTICS` when enabled. (parity).
- [x] `planner/guardrails.py` — operation-order validation (expire → orphan → compact → manifests).
- [x] `engines/base.py` — `MaintenanceEngine` interface (probe/render/execute/session_setup).
- [x] `engines/trino/client.py` — per-coordinator connection (HTTPS + BasicAuth).
- [x] `engines/trino/load.py` — `system.runtime.queries` OPTIMIZE probe. (parity).
- [x] `engines/trino/sql.py` — render OPTIMIZE (chunked/filtered), expire, orphan, manifests,
      `ANALYZE TABLE`, session SETs. (parity). **Golden tests.**
- [x] `engines/trino/engine.py` — execute + capture query id + errors.
- [x] Tests: planner unit tests; Trino renderer golden tests; `test_trino_engine.py`;
      routing/execution phase tests (`test_execution_phases.py`).
- [~] Contract test against local Trino on seeded tables in pytest — `make smoke-compact` covers
      Docker e2e REST path; HMS live tests via `make test-hms-integration` (opt-in, not CI default).

## P3 — Governor + Scheduler (load-aware orchestration)
- [x] `governor/admission.py` — capacity math: caps, live vs local in-flight, off-peak gate,
      adaptive budget, cooldown, deadline. (parity).
- [x] `governor/governor.py` — wave loop, round-robin replicas, per-table lock handoff, dispatch,
      reap, finalize run summary (`run_wave()`, `finalize_open_runs()`). (parity).
- [x] `worker/worker.py` — claim job → load stats → plan → engine execute → audit → counters
      (`process_next()`).
- [x] `scheduler/rules.py` — `effective_preset(table)` + `tables_for_preset(preset)` resolution.
- [x] `scheduler/scheduler.py` — cron eval → create run + jobs → enqueue; cooldown pre-filter;
      priority order; leader-elected (`SchedulerService.tick()`).
- [x] `cryo/infra/wiring.py` — Redis queue, locks, SQLJobStore, SQLSchedulerStore.
- [x] `cryo/cli.py` — unified CLI (`cryo governor|worker|scheduler|serve|events`).
- [x] Audit log writes for every action (supersedes `iceberg_maintenance_log`).
- [x] Tests: governor admission math; wave behavior (`test_queue_and_governor.py`); worker audit
      (`test_worker.py`); orchestration smoke (`scripts/smoke_cryo_e2e.py`).

## P4 — Presets & assignment API
- [x] Repositories + Pydantic schemas for sources, destinations, presets, rules, assignments.
- [x] Predicate grammar (`lld.md` §6.2): JSON → SQLAlchemy filter (explorer/selection) + in-memory
      evaluator (rule matching). Shared module.
- [x] FastAPI app factory + auth (JWT, roles admin/operator/viewer) + deps.
- [x] Routers: `/sources`, `/destinations` (+ `/test`), `/presets` (+ `/simulate`),
      `/presets/{id}/rules`, `/assignments` (filter/ids selection), `/tables/{id}/effective-preset`.
- [x] **Trino catalog_bindings**: destination config multi-select of Cryo lakehouses (+ optional
      `trino_catalog` override); `GET /tables/{id}/compatible-presets`; assign rejects unbound Trino;
      Connections UI + table/bulk preset filtering; worker resolves SQL catalog from bindings.
- [x] `/maintenance/preview` (dry-run → per-table rendered SQL + estimates) and `/maintenance/run`.
- [x] `/maintenance/manifest-rewrite` — standalone manifest-only quick action.
- [x] `/scans` trigger; `/runs`, `/jobs`, `/runs/{id}/cancel`.
- [x] Scanner config/status API: `/scanner/config`, `/scanner/status`, scan-run filtering, chunked
      progress summaries, bounded failed-table errors, visible queue failures.
- [x] **Cost API**: extend `/maintenance/preview` with plan-filtered `SavingsProjection`; add
      `/tables/{id}/cost` + `/dashboards/cost`; cost summary fields in predicate grammar (including
      `estimated_monthly_s3_cost_usd`); `/scans` accepts `cost_full_scan` and enqueues when Redis is
      available; worker writes `actual_bytes_freed` when engines return it.
- [x] Tests: selection/assignment transactional correctness (filter selects across pages);
      effective-preset resolution (pinned > rule-priority > none); preview renders without executing.

## P5 — Web UI
- [x] Vite + TS + AntD + Recharts + TanStack Query scaffold; OpenAPI client generation.
- [x] **Table Explorer**: server-side filter/sort/paginate; column filters → predicate grammar;
      `rowSelection` + "select all matching filter"; sticky **BulkAssignBar** (Assign / Run now).
- [x] **Table Explorer multi-catalog**: catalog column + catalog filter (`source_id` predicate);
      qualified `catalog.schema.table` identifiers on API/UI; `/tables?source=` deep link from Catalogs.
- [x] **PlanPreview** modal (per-table Trino/Spark SQL + chunk/partition/load estimate).
- [x] **Presets** editor (operations/params/schedule/rules/guardrails) + **Simulator**.
- [x] Preset drawer uses described fields for params/guardrails instead of raw JSON text areas.
- [x] **Runs / Live Console**: WebSocket; per-engine concurrency gauges; job rows; log drawer.
- [x] **Datalake Health** dashboard (grade dist, cost by schema, drift, top offenders, totals).
- [x] **Cost & Savings** page at `/cost` (canonical full experience): waterfall, savings by op, schema
      bars, trend (incl. realized when available), top opportunities with filter-based bulk select,
      full cost scan trigger, precision badge. Health page keeps KPI summary + link to `/cost`.
      Explorer columns/filters; Table Detail cost card + mini waterfall; PlanPreview savings summary.
- [x] **Table Detail** (health card, partitions, snapshot/manifest stats, audit timeline, Run).
- [x] **Table Detail scan UX**: pressing Scan should not redirect immediately — show in-progress
      animation on the scan/run button; navigate to the scan run only after the user follows through
      on the animated button (not on the initial click).
- [x] **Scanner** page (`/scans`): schedule form, scan status, scan-only run list, progress console;
      ad-hoc scans from scanner page, table selection, and table detail.
- [x] **Sources & Destinations** management + health checks.
- [x] **Lakehouse schema filter**: per-source `maintenance_schema_names` allowlist on Connections;
      empty = all namespaces; scanner + scheduler + ad-hoc runs honor the filter.
- [x] **UI/UX polish** per `research-and-roadmap.md` Part 6: dark theme toggle, always-dark sidebar,
      IBM Plex typography, focus rings, reduced-motion, log-line animation.
- [x] WebSocket endpoint `/ws/runs/{id}` streams run snapshots plus Redis-backed live log events.
- [x] **Log streaming** for scan runs and maintenance runs — live log tail with replay and polling
      fallback; wired into `/scans` and `/runs` consoles/drawers.
- [~] Run console realized-savings line — worker records `actual_bytes_freed`; UI subtitle notes pending
      full realized-vs-projected display.

## P6 — Spark / EMR engine (size-based routing)
- [x] `engines/spark/sql.py` — render `rewrite_data_files` (where + options), `expire_snapshots`,
      `remove_orphan_files`, `rewrite_manifests`, `compute_statistics` from the **same** plan.
      **Golden tests.**
- [x] `cryo_spark_runner.py` — PySpark entrypoint executing CALL statements with Iceberg SparkSession.
- [x] `engines/spark/emr.py` — EMR Serverless submit/poll/logs (boto3); capacity = slot cap − in-flight.
- [x] `engines/spark/engine.py` + `engines/spark/local.py` — implement `MaintenanceEngine`; wire into
      worker + governor.
- [x] `engines/factory.py` — routes to Spark/Trino/Native/Flink based on destination config.
- [x] Governor EMR slot governance; preset rule `size_gb > 100 → spark-large`
      (`cryo/scheduler/routing.py`, `scripts/seed_local_config.py`).
- [x] Tests: Spark renderer golden tests; routing test (large table → Spark, small → Trino).
- [~] Contract test against local Spark on seeded tables in pytest — covered by `make smoke-compact`,
      not yet in CI unit suite.

## P7 — Reporting, alerting & hardening
- [~] `reporting/daily_report.py` + `reporting/slack.py` + `reporting/scheduler.py` — daily anomaly
      report with optional AI summary; full HTML email KPI report from `lld.md` not yet ported.
- [~] Slack notifier scaffold; in-app drift/cost alerts (`reporting/alerts.py`) not yet complete.
- [~] Worker **realized savings**: `actual_bytes_freed` captured per op in audit log; predicted-vs-
      realized trend + model calibration (`query_gets_per_file`, compaction heuristic) pending.
- [ ] Prometheus metrics (jobs, admission, live load, scan coverage, datalake grade, cost, drift,
      projected savings by op, reclaimable bytes, realized-vs-projected, orphan bytes + staleness).
- [ ] Structured JSON logging across services; per-job live log stream (beyond DB polling).
- [~] Helm chart `deploy/helm/cryo` — `Chart.yaml`, `values.yaml`, `_helpers.tpl` only; no service
      deployment templates, Ingress, HPAs, or leader-lease manifests yet.
- [~] Security pass: JWT auth works; secret encryption, RBAC hardening, destructive-op `force` audit
      incomplete.
  - [~] e2e smoke (`make smoke-compact`, `scripts/smoke_cryo_e2e.py`) exists; not yet in CI pipeline.
- [x] Native expire + orphan e2e (`tests/integration/native/`, `make test-native-e2e-local`, CI `native-e2e` job).
- [~] Docs: `docs/operator-guide.md` exists; deployment guide +
        README/`hld.md`/`lld.md` polish pending.

---

## P8 — Event fast-path (control plane) ✅
Per `docs/EXECUTION_PLAN.md` Phase 4 / `architecture.md` §1.2 — Netflix-style event reaction
alongside cron sweep.

- [x] `cryo/events/watcher.py` — polls `current-snapshot-id` for hot/streaming tables; publishes
      `TableCommitted` to Redis Streams.
- [x] `cryo/events/consumer.py` — consumer group; opportunistic re-score + re-schedule on commit.
- [x] `cryo/events/redis_stream.py` + `cryo/events/service.py` — event bus wiring.
- [x] `cryo cli events` — CLI entrypoint; cron full-scan remains slow-path backstop.

## P9 — Native engine (`cryo-engine-go` / legacy `cryo-engine`) ⚠️
Per `architecture.md` §1.5 and `docs/native-iceberg-go-compaction-plan.md` Phase F.

- [x] `EngineType.NATIVE`; `RenderedStatement.payload` field; `cryo/engines/native/engine.py` adapter.
  - [x] `engine-go/` Go module — JSON stdin/stdout protocol (default subprocess).
  - [~] `cryo-engine/` Rust crate — expire/orphan only; **deprecated**; remove after Phase E §8 gate.
- [x] Worker subprocess adapter (`cryo/engines/native/runner.py`) + `CRYO_ENGINE_IMPL` (default `go`).
- [x] Native preset `default-native-deep` seeded on fresh migrate (`engine_impl=go`).
- [x] COW + MOR compaction via `Transaction.RewriteDataFiles` (Go).
- [x] `optimize_manifests` routed to SQL fallback (Go analyze-only manifest count).
- [x] Optional sort compaction validates table sort order / `sort_columns`; z-order routes to SQL.
- [x] Web UI: split Native (iceberg-go) / Native (iceberg-rust) engine options; `engine_impl` on
  destination form; preset labels + grey-out `optimize_manifests` / manifest-only for native.
- [x] Native K8s Job create-on-admit: stats-based estimator (≤16Gi), bin-pack grouped runs,
      `native_dispatches`, JobWatcher (`cryo/governor/native_admit.py`, `engines/native/{resources,packing,job_dispatch,watcher}.py`).
- [ ] Retire Rust `cryo-engine/` (Docker stage, CI job) after Phase E consecutive green CI.
- [ ] Golden-test parity across native / Trino / Spark for all operation types.
- [ ] Production hardening: retries, Prometheus metrics, distributed mode, benchmark suite.

## P10 — Compliance & governance ✅
- [x] `cryo/compliance/governance.py` — pending deletion tracking, protected snapshot registry.
- [x] `protected_snapshots` table + migration; `GET/POST /tables/{id}/compliance`,
      `POST /tables/{id}/protected-snapshots`.
- [x] Catalog latency monitoring (`cryo/sources/latency.py`, `monitored.py`);
      `GET /sources/{id}/catalog-latency`.

## P11 — AI layer ✅
Per `architecture.md` Part 2 — no freeform NL-to-SQL against live Iceberg.

- [x] `cryo/ai/anomaly.py` — EWMA/z-score over `table_stats_history`; `GET /tables/{id}/anomalies`.
- [x] `cryo/ai/explain.py` — `explain_health_change()`; `POST /tables/{id}/explain-health`.
- [x] `cryo/mcp/server.py` — read-only MCP tools; `/mcp/tools`, `/mcp/tools/call`.
- [x] `cryo/ai/copilot.py` — verified questions over Postgres metadata; `/ai/copilot/*`.
- [ ] MCP server as standalone process (currently API-embedded).
- [ ] Gated `run_preset` MCP write tool with explicit role scope (endpoint exists; MCP wiring TBD).

## P12 — Adaptive routing, tiering & extended platform
Per `docs/EXECUTION_PLAN.md` Phase 8 and `docs/COMPREHENSIVE_FEATURE_ROADMAP_2026.md`.

- [x] Multi-engine adaptive routing (`cryo/scheduler/adaptive_routing.py`);
      `POST /api/v1/routing/preview`; preset `engine_pool` / `adaptive_routing` guardrails.
- [x] Data tiering candidates (`cryo/tiering/`, `tier_policies` table, S3 analyzer);
      `GET /tables/{id}/tier-candidates`; tier policy CRUD.
- [ ] Tiering **execution** — move cold partitions to cheaper storage classes (candidates only today).
- [x] Signal-driven maintenance triggers (`cryo/triggers/`); governor signal bypass of cooldown/off-peak.
- [x] AutoComp benefit scoring (`cryo/planner/benefit_score.py`); partition ranking in planner.
- [x] Compaction effectiveness ratio tracking in audit log.
- [x] `COMPUTE_STATISTICS` operation (Puffin NDV + Bloom filters) in planner + both renderers.
- [~] Flink engine (`cryo/engines/flink/`) — generates TableMaintenance config; deploy/execute path
      is team-operated, not Cryo-worker executed.
- [x] Query telemetry ingestion (`cryo/telemetry/`); **Query Performance** UI (`/query-performance`).
- [x] Extended UI pages: `JobDetail`, `MultiCatalog`, `CostAttribution`, `SLAStatus`,
      `LayoutOptimization`.
- [~] Query-pattern-aware sort/layout optimization (`cryo/optimizer/`) — logic exists; needs sustained
      query-log volume for calibration.
- [x] **Tiered query-log pipeline** (`cryo/telemetry/iceberg_writer.py`, `curator.py`, `query_buffer.py`)
      — Redis Streams buffer -> Iceberg archive (`cryo_internal.queries`) -> curated Postgres `query_logs`
      -> `query_patterns`. Per-destination config block in Connections drawer; retention via
      `expire_snapshots`; multi-coordinator poll; deterministic sample by `query_id`. Migration
      `20260707_0018_query_log_config.py`. 33 new unit tests in `tests/test_query_log_pipeline.py`.
- [ ] Spark History Server integration for `query_log` mode on `spark_emr` destinations — out of
      scope for this PR; flag in `todo.md` so the next person picks it up.
- [~] Approval workflows (`cryo/workflows/approvals.py`) — backend scaffold; full UI flow TBD.
- [~] GitOps policy loader (`cryo/gitops/policy_loader.py`) — YAML loader; Git watcher not wired.
- [~] Multi-tenant isolation (`cryo/multi_tenant.py`) — session scoping scaffold.
- [~] Cross-engine ABAC (`cryo/security/abac.py`) — policy evaluation scaffold.
- [~] Data lineage (`cryo/lineage/tracker.py`) — graph builder; visualization UI TBD.
- [~] FinOps forecast (`cryo/finops/forecast.py`) — projection logic; dashboard integration TBD.

---

## Cross-cutting / backlog
- [ ] AuthN via OIDC/SSO (swap from local JWT) — pending SSO confirmation.
- [ ] Field-level Fernet/KMS encryption for stored credentials and webhook URLs.
- [ ] Additional source adapters (Polaris/Lakekeeper/Nessie) beyond REST/HMS/Glue.
- [ ] Additional Spark targets (EMR-on-EKS / Livy / Spark Connect) behind the engine interface.
- [ ] Continuous/streaming self-optimization mode (Amoro-style) — future phase.
- [ ] Format-pluggability groundwork for Hudi/Paimon/Delta (Iceberg only in v1).
- [x] Cost model refinement (request/list costs, not just storage); savings-after-maintenance —
      specced in `lld.md` §16, `docs/cost-savings.md`; Tier 1+2 accounting done.
      (Remaining: multi-region price tables, non-storage AWS cost lines like data transfer.)
- [ ] Saved filters / scheduled saved views in the Explorer.
- [x] Redis WebSocket fanout for live run console (run events via Redis Streams + `/ws/runs/{id}`).
- [x] **Worker step ledger + orphan recovery** — write-ahead `cryo.job_steps`, quiet-window gates for
      dangerous ops, job-owner Redis leases, Trino/Spark engine probes, governor reaper skips leased/
      started/active jobs. See `docs/WORKER_STEP_LEDGER.md`. Migration `20260727_0033`.
- [ ] Self-host IBM Plex fonts (woff2) instead of Google Fonts CDN (`research-and-roadmap.md` Part 6 §8).
- [ ] Chart `aria-label` summaries for all Recharts instances (accessibility backlog).
- [ ] Query-pattern-aware optimization at scale — blocked on query-history ingestion maturity.
- [ ] Native engine production hardening — see `architecture.md` §1.5.

## Open decisions to confirm (see `plan.md` §7)
- [ ] Spark execution target: **EMR Serverless** (assumed, implemented) vs EMR-on-EKS / Livy / Spark Connect.
- [ ] Auth: local JWT (assumed v1, implemented) vs OIDC/SSO from day one.
- [x] Frontend: React + AntD (assumed) — **decided and shipped**.
- [ ] Project name (`Cryo` placeholder).
