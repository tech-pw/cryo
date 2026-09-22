# Cryo — TODO

## Permanent recovery — 2026-09-20

- [ ] Execute the [critical-vitals incident plan](docs/CRYO_CRITICAL_VITALS_ACTION_PLAN_2026-09-20.md):
      reproduce the 59,673-failure window, classify legacy versus fresh causes, reconcile two
      stuck runs/32 stale jobs, contain confirmed missing-metadata retries, repair impact
      evidence output, restore scan coverage, and validate capacity/economic improvements.

Detailed tickets and acceptance tests: [R1–R8 recovery plan](docs/CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md).

Current closure status: [follow-up review](docs/CRYO_RECOVERY_FOLLOWUP_REVIEW_2026-09-20.md).
- [ ] Finish partial F03/F04/F05/F08/F09/F10 and original F12 isolation, alongside open
      F02/F06/F07. Do not count clean-yield tests as crash recovery or fixture restoration as
      database isolation; keep analysis Jobs disabled until the documented gates pass.

- [x] Document permanent scheduler, lifecycle, impact and metric recovery fixes against `9e015aff`.
- [ ] R1–R3: profile heavy runners; isolate bounded resumable analysis Jobs; page scoring with full coverage.
- [ ] R4–R5: make finalization idempotent; safely reconcile the exact legacy incident cohort.
- [ ] R6: independently trace zero GB freed and distinguish missing evidence from measured no-op.
- [ ] R7–R8: verify capacity efficiency, honest cohort metrics and the event-gated clean evaluation.

- [x] Implement scanner KEDA production bounds 1–8, metric Service, exclusive replica ownership,
      and bounded downscale; add Helm render regressions (2026-09-18).
- [ ] Roll out scanner autoscaling during zero demand; verify idle floor, active-scan recovery,
      burst throughput and subsequent actual cost reduction.

## Unused-cost incident — 2026-09-16

- [x] Refresh/log CUR, inspect live resources and metastore timeouts; document [the fix plan](docs/CRYO_UNUSED_COST_FIX_2026-09-16.md).
- [x] Fix native metadata-only jobs inheriting rewrite CPU; 50 sizing/packing tests pass locally.
- [ ] Restore metastore reachability; implement source-scoped breaker and bounded batch deferral.
- [ ] Route before sizing, calibrate metadata batch overhead, and verify resource-hour reduction.
- [ ] Resolve scheduler OOMs; add safe scanner pending-plus-active scaling.
- [ ] Deploy and verify cost per useful effect, unused share, and actual node-hour savings.

Phased, actionable checklist. Mirrors the roadmap in `plan.md` §6 and the sequenced work in
`docs/EXECUTION_PLAN.md`. Check items off as you go and keep this file honest. `P0`…`P7` = original
phase plan; `P8+` = post-MVP extensions from `research-and-roadmap.md` Part 1 and the 2026 feature roadmap.

Legend: `[ ]` todo · `[~]` in progress / partial · `[x]` done · `(parity)` must match the reference impl.

## Top-500 daily maintenance overhaul — 2026-09-22

Why, what changed, capacity math, and rollout order:
[top-500 daily maintenance overhaul](docs/TOP500_DAILY_MAINTENANCE_OVERHAUL_2026-09-22.md).
Goal: all four core operations on the 500 most-queried Trino tables, daily, Spark-on-K8s only.

- [x] Bound cron-triggered runs (`CRYO_MAX_JOBS_PER_SCHEDULED_RUN`, default 500) — the cron path
      never passed `build_scheduled_run`'s `budget_hint`, so one preset fired fleet-wide.
- [x] Make governor admission throughput-aware via worker heartbeats (`cryo:workers`), not just
      engine capacity.
- [x] Stop the reaper manufacturing failures: skip jobs still awaiting a worker, requeue genuinely
      dropped dispatches up to `CRYO_MAX_DISPATCH_ATTEMPTS` (`jobs.dispatch_attempts`, mig `20260922_0055`).
- [x] Per-operation isolation in the worker — one failed operation no longer kills the other three.
- [x] Fix top-500 selection: `TableQueryActivity` fallback, `EmptyHotRankingError` guard, cross-preset
      repin takeover.
- [x] Hard-gate `remove_orphan_files` on shared-S3-prefix overlap.
- [x] Per-operation coverage metric + `GET /api/v1/maintenance-coverage`.
- [x] Verify `cryo.query_logs` in prod — **0 Trino rows in 7d**, so the ranking rides the
      `TableQueryActivity` fallback (validated: 500 tables, #1 = 3,375 queries/7d, #500 = 75).
- [x] Disable the five legacy fleet-wide presets (2026-09-22; 0 enabled presets remain,
      backlog drained to 0 queued / 0 admitted).
- [x] Deploy code + migration `20260922_0055` (digest `sha256:6078408e…`, Helm rev 196; migration
      ran as the `pre-upgrade` hook before any pod rolled; 14/14 pods verified on the new digest).
- [x] Seed, repin, and enable `daily-trino-hot-tables` — 500 tables pinned, first fire 00:30 IST.
- [x] One-time cleanup: 17,760 tombstones in `cryo:admitted` left by the old reaper, which would
      have computed as zero admission headroom and blocked the fleet. Rebuilt atomically.
- [ ] Watch three days: `fully_covered_pct` climbing, `untouched_tables` ≈ 0, `stuck_admitted_no_dispatch` ≈ 0.
- [ ] Treat a pin to a *disabled* preset as vacant in `repin_trino_hot_tables` — 20 top-500 tables
      were stranded on disabled presets and had to be cleared by hand.
- [x] Replace the `cryo:admitted` list with a Redis Streams consumer group (2026-09-22). Acks at
      `mark_running`, so the pending list covers exactly the claim → RUNNING handoff and reclaim
      can be 2 minutes rather than longer than the longest operation. Deleted the reaper's
      guesswork, `jobs.dispatch_attempts` (migration `20260922_2100`), and the `cryo:workers`
      heartbeat zset — the consumer group tracks worker presence correctly, including workers
      busy on a long job, which the heartbeat could not.

### Queue / scanner / governor overhaul — 2026-09-22

Full write-up: [docs/QUEUE_SCANNER_GOVERNOR_OVERHAUL_2026-09-22.md](docs/QUEUE_SCANNER_GOVERNOR_OVERHAUL_2026-09-22.md).

- [x] Scanner CronJob runs all four chunks (`completionMode: Indexed`, `completions`/`parallelism`/
      `--num-chunks` all driven by `scanner.chunks`). It was a single pod with no arguments, so
      every run discarded 75% of the fleet: `discovered=12584, assigned=3146`.
- [x] Scanner orders candidates stalest-first instead of by catalog name, so an unfinished run is
      self-correcting rather than rescanning the same alphabetical prefix every six hours.
- [x] `run_wave` decomposed: 622 lines at depth 10 → 44 lines at depth 2, with the gate chain a
      flat ordered sequence and outcome counters incremented in one place.
- [x] Admission is deterministic again — the Thompson-sampling bandit that resampled
      `per_replica_cap` and friends every wave is behind `governor_bandit_tuning_enabled`
      (default false), which also removes a Postgres round trip per deferred job.
- [ ] Post-deploy watch: scanner `assigned` ≈ 12.6k per run and >30d staleness falling from 10.7%;
      zero `stuck_admitted_no_dispatch`; admitted-per-wave stable for unchanged fleet state.
- [ ] Still unexplained: the scanner's ~10.7% per-run failure rate (`failed=338` of 3,146) and the
      drop in daily distinct tables scanned after Sep 7.
- [ ] Decide separately on `qpp.py` (391 LOC, flag off, but has API + frontend surface) and
      `native_admit.py` (1,257 LOC, flag off in prod, but is the Go engine's dispatch path).
      `roi_gate.py` should stay — it is deliberately collecting shadow data.

### Scanner-worker scale-to-zero — 2026-09-22

Full write-up: [docs/SCANNER_WORKER_SCALE_TO_ZERO_2026-09-22.md](docs/SCANNER_WORKER_SCALE_TO_ZERO_2026-09-22.md).

- [x] `cryo-scanner-worker` scales to zero when no ad-hoc scan is in flight. Its KEDA trigger
      polled the Deployment's own Service, so one replica had to stay up to answer "should there
      be any replicas?"; it now polls the API, which is always up and is where ad-hoc scans are
      created. Last genuinely user-triggered scan was 2026-08-25, so the floor bought nothing.
- [ ] Post-deploy watch: Deployment reaches `0/0`; a UI-triggered scan wakes it within
      `pollingInterval` (15s) + pod start and the chunk is actually consumed; no ScaledObject
      trigger errors.
- [ ] **Bigger fish, unaddressed**: the untagged ephemeral bucket (Spark + native dispatch Jobs,
      which don't inherit a Deployment's workload tags) ran at **$41.41/day unused, 87.3% unused**
      over 2026-09-08→21 — ~145x the scanner-worker saving. This is where the namespace's waste
      actually is.
- [ ] **`trino-db` is up 14.8% month-over-month** (Sep days 1–21 mean $532.76 vs Aug $463.94,
      Welch's t = −2.30, flagged likely significant by `compare_mom.py`). Against the standing
      "this month below last month, with significance" goal that is a regression, and it is
      moving the wrong way while Cryo's own footprint falls.

---

## Immediate cost rescue — 2026-09-11

Implementation details and acceptance gates: [consolidated cost reduction plan](docs/CRYO_COST_REDUCTION_PLAN.md).

- [x] Consolidate observed costs, confirmed defects, fix status, and the ordered solution.
- [x] Expand the plan with CR-01–CR-12 code evidence, specific fixes, regression scenarios, and release gates.
- [ ] Finish bounded per-table feedback updates and shared model distribution; isolate scheduler memory failures.
- [ ] Prove durable cross-engine continuation and recovery, with operation-specific completion accounting.
- [ ] Close physical S3 reclamation gaps and verify protected references and retained object versions.
- [ ] Right-size dispatch by operation; tune eligible compaction cohorts and ingestion/layout fixes.
- [ ] Reconcile billing coverage and demonstrate net savings with matched workloads and Trino capacity changes.

Last reconciled: 2026-08-04 (against `docs/current-state.md`,
`docs/cryo-10-of-10-plan.md`, `docs/EXECUTION_PLAN.md`, and the codebase).

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
      hot-partition skip, delete-ratio trigger, `COMPUTE_STATISTICS` when enabled; absent partition small-file
      metrics are zero, and full-table optimize requires known in-budget table size. (parity).
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
- [x] Vite + TS + Radix UI + Tailwind CSS + Recharts + TanStack Query/Table; OpenAPI generation.
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
- [x] **Health freshness drill-down**: exclusive snapshot-age and failed/corrupt buckets, accessible
      horizontal chart/table, URL-backed Catalog lists, and complete server-filtered CSV export.
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
- [x] Spark scope guard preserves partition predicates, rejects duplicate unscoped rewrites,
      and requires scan-size evidence for direct full-table Spark execution.
- [~] Contract test against local Spark on seeded tables in pytest — covered by `make smoke-compact`,
      not yet in CI unit suite.

## P7 — Reporting, alerting & hardening
- [x] Shared S3 warehouse-path anomalies: `/anomalies` page, catalog-native harvest (Hive/Glue/REST),
      10:00 IST Slack only when overlapping groups exist (`docs/shared-s3-anomalies.md`).
- [~] `reporting/daily_report.py` + `reporting/slack.py` + `reporting/scheduler.py` — daily anomaly
      report with optional AI summary; full HTML email KPI report from `lld.md` not yet ported.
- [~] Slack notifier scaffold; in-app drift/cost alerts (`reporting/alerts.py`) not yet complete.
- [~] Worker **realized savings**: `actual_bytes_freed` captured per op in audit log; versioned
      intent/plan/pre-scan/effect/post-scan outcome lineage, leakage-free chronological cost
      holdout, cache compatibility, and domain abstention are implemented. Production calibration
      and request/compaction-heuristic tuning remain pending.
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
- [x] COW compaction via `Transaction.RewriteDataFiles` (Go); tables with delete files route
      OPTIMIZE to a compatible SQL fallback instead of being rewritten natively.
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
- [x] Iceberg maintenance intelligence RFC (2026-08-19): scan-ratio + bucket re-size in
      `partition_analyzer`; day-0 target file size via existing property recs; fast-path new-table
      sweep (`cryo/scanner/fast_path_check.py`) + Health UI. Detect-only; no auto-compact.
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

## P13 — Cryo 10/10 evidence-gated roadmap

Per `docs/cryo-10-of-10-plan.md`. External and time-based gates remain open
until real evidence exists.

### Wave 0 — Truth, focus, and baseline

- [x] Canonical typed engine-operation capability manifest with exact ownership,
      limitations, fallbacks, maturity, and evidence.
- [x] Runtime, authenticated API, Connections UI, generated docs, and CI drift
      checks consume the capability contract.
- [x] Current-state, frontend-stack, engine enum, operation enum, native
      fallback, and PTO citation claims reconciled.
- [x] Deterministic repository baseline, package/hotspot map, migration support
      statement, evidence registry, feature freeze, and stop-ship policy.
- [x] Twelve-interview plan recorded.
- [ ] Complete the first six design-partner interviews and retain approved,
      redacted evidence.
- [ ] Confirm staffing for Waves 1 and 2.
- [ ] Measure the remaining production SLO, scale, security, workload, and
      usability baselines; do not substitute local fixtures.

### Wave 1 — Safety kernel

- [x] Bind immutable intent, plan hash, policy/approval, expiry, budgets, and
      attempt lineage.
- [x] Add monotonic fencing and destination-specific external-effect strategy.
- [x] Add level-triggered reconciliation, bounded fairness, and emergency pause.
- [x] Pass repository crash-point, state-machine, duplicate-delivery, stale-worker, and
      unknown-effect tests.
- [ ] Run and retain the production-shaped 10,000-attempt stale/duplicate effect
      campaign; do not infer this result from unit tests.
- [ ] Measure deployed p99 pause propagation and prove RPO 0 across the complete
      crash matrix before enabling durable intents by default.

### Waves 2-6

- [~] Production proof: correlation, SLOs, scale/fault lab, rollout, DR,
      security, and Control Room.
  - [x] Durable end-to-end correlation, daemon/API metrics, alerts, dashboard,
        and documented evidence windows.
  - [x] Supply-chain baseline: locked production dependencies, pinned base
        images, verified Spark downloads, image vulnerability gate, SPDX SBOM,
        provenance, keyless release signatures, digest-aware production Helm,
        and Go/Rust dependency update coverage.
  - [x] Bounded configurable audit retention and indexed daily scheduler sweep.
  - [~] Customer-shaped fleet and control-plane fault evidence: deterministic
        CryoBench v1 manifest and evidence-linked matrix are implemented. Local
        rollback/retry, queue reconstruction, and database-time expiry fault
        models pass; real PostgreSQL failover, Redis process/data loss, and
        deployed clock-skew scenarios remain explicitly open.
  - [~] Backup/restore automation: authenticated chunked encryption, checksum
        manifests, secret-safe pg_dump/pg_restore commands, force-gated restore,
        scheduled manifest-committed S3 upload, scoped retention, and drill
        runbook are implemented. Production enablement, upgrade validation, and
        two retained RTO/RPO drills remain open.
  - [~] Cohort rollout safety: destination policies, minimum sample gates,
        durable attempt/effect budgets, audited automatic destination pause,
        metrics, and critical alert are implemented. Deployed forced-regression
        and rollback exercises plus remaining Control Room flows remain open.
  - [ ] External security review, representative 14/30-day soak, three pilots,
        and deployed SLO history.
- [~] Calibrated intelligence: versioned outcome lineage and bounded history reads; deterministic
      size/file-count/threshold/FIFO/random baselines with regret and confidence; leakage-free
      chronological cost holdout; incompatible-cache rejection; and explicit feature-domain
      abstention are implemented. Delete lifecycle now includes audit-derived bounded
      minor-to-major escalation and equality-delete-specific major routing. Leakage-free
      split-conformal runtime and measured rewrite-output-byte intervals now use chronological
      calibration/holdout slices, per-engine or explicitly pooled scope, finite-sample coverage,
      and missing/out-of-domain/low-confidence abstention. AutoComp now persists realized
      prediction/effect/outcome yield and feeds a bounded 0.5-1.5 multiplier back into partition
      scores only after 20 stable lineage-complete optimize outcomes; sparse or drifting evidence
      leaves scores unchanged. Delete-lifecycle evaluation now requires complete
      prediction/effect/post-scan lineage, measured rewrite output, runtime, delete-density change,
      subsequent p95 latency, and matched engines before comparing minor policy outcomes with a
      threshold-only cohort. Historical comparisons remain advisory. Production 90% lineage,
      statistically significant calibration and VMY lift, a controlled lifecycle holdout, and
      complete decision/simulation UX remain open.
- [~] Workload-aware layout: PTO B1 now provides operator-only, low-confidence
      `heuristic_v1` shadow recommendations with bounded final ranking,
      cardinality-aware partition candidates, distinct-query evidence gating,
      explicit abstention, read-only observed-file-touch simulation, and
      workload-distribution drift detection. WAIR has offline boundary detection
      and deterministic GB/file-budget selection over bounded file samples from
      both scanners plus frequency-ranked SQL range evidence. A Trino/Spark
      differential oracle now fingerprints rendered plans and blocks dropped
      partition, row-group, and clustering semantics. Operators can persist a
      versioned, input-bound dossier with an actor-attributed audit event.
      Versioned partition scopes now prove matching predicates under 10 GiB and
      500-file ceilings. Spark rewrite-only rendering removes the hidden
      table-property mismatch while holdout and production safety gates keep all
      candidates ineligible. Temporal baseline replay now records advisory
      stability evidence but explicitly refuses to claim causal counterfactual
      lift. An externally orchestrated controlled-shadow contract can satisfy
      the holdout blocker only with bound, adequately powered, regression-free
      evidence. Cross-engine output fixtures verify row/schema/spec/scope
      invariants but remain synthetic and non-production-qualified.
      Production safety is now derived from fresh internal rollout, effect,
      pause, and halt/resume-drill records and invalidates on policy changes.
      All evidence is rebound to current PTO/scope hashes. A dedicated,
      non-operator collector role, fresh snapshot/metadata identities, attestation
      binding, ingestion endpoint, and collection harness now make real deployed
      output provenance recordable without trusting ordinary operator input. The
      real deployed run remains open.
      Holdout evidence, bounded rewrite execution, approved canaries, and
      production conformance remain open; there is no auto-apply path.
- [~] GA/category leadership: a deterministic fail-closed release dossier now
      fingerprints repository evidence and is retained by the signed release
      workflow. Privacy-safe Maintenance Decision Record v1 has a stable schema,
      compatibility policy, authenticated export, and integrity binding. Signed
      releases now render, fingerprint, and retain the full transaction-wrapped
      PostgreSQL migration graph in both directions while explicitly refusing to
      treat offline SQL as deployed proof. Production data-preservation and
      availability drills, restore proof, two qualifying releases, three MDR
      consumers, external review, and case studies remain open. The public support
      policy now documents release coverage, channels, incident severity, safe
      diagnostics, and capability limits without claiming a commercial SLA.
      External gates can now be counted only from gate-context-checked, exact-revision
      Ed25519 attestations under explicitly trusted verifier keys; no missing
      evidence is synthesized. Wave 6 foundations now include an opt-in
      k-anonymous aggregate benchmark (10 tables/20 outcomes minimum, coarse
      rounded cohorts, no automatic transmission) and a versioned ecosystem
      connector manifest/conformance contract with no dynamic plugin loading.
      Policy learning, independently maintained integrations, adoption, and
      category-leadership proof remain evidence-gated.

---

## Cross-cutting / backlog
- [ ] Close R1–R6 review blockers before analysis Job rollout: isolated PostgreSQL tests,
      production entrypoint/runtime wiring, resumable fenced attempts, complete bounded scoring,
      fleet-wide refresh budget, concurrent finalization, and complete impact evidence semantics.
      See `docs/CRYO_R1_R6_REVIEW_AND_NEXT_STEPS_2026-09-20.md` (F01–F12).
- [ ] R7: measured capacity baseline, dependency containment, execution sizing, replica-floor
      canaries, and billing reconciliation, preserving throughput/freshness gates.
- [ ] R8: cohort-aware reliability, measurement coverage, matched query efficiency, and
      comparable-period net savings; retain aggregate failures and unknown-impact states.
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
- [x] Bundle IBM Plex fonts through `@fontsource` instead of a Google Fonts CDN.
- [ ] Chart `aria-label` summaries for all Recharts instances (accessibility backlog).
- [ ] Query-pattern-aware optimization at scale — blocked on query-history ingestion maturity.
- [ ] Native engine production hardening — see `architecture.md` §1.5.

## Open decisions to confirm (see `plan.md` §7)
- [ ] Spark execution target: **EMR Serverless** (assumed, implemented) vs EMR-on-EKS / Livy / Spark Connect.
- [ ] Auth: local JWT (assumed v1, implemented) vs OIDC/SSO from day one.
- [x] Frontend: React + Radix UI + Tailwind CSS — Ant Design migration completed.
- [ ] Project name (`Cryo` placeholder).
