# Cryo — Master Execution Plan

> **Open-source portability (2026-09-25):** Follow
> [the OSS launch plan](oss-launch-plan-2026-09-25.md) for removing organization-specific
> assumptions, delivering an independently installable Helm release, and building adoption.
> This is a proposed packaging/portability workstream, not completed implementation or a
> replacement for the cost/reliability and production-safety gates below.

> **Cost rescue priority (2026-09-11):** Follow
> [CRYO_COST_REDUCTION_PLAN.md](CRYO_COST_REDUCTION_PLAN.md) for immediate reliability,
> physical reclamation, and net-cost proof work before expanding features. It supersedes older
> sequencing/status claims for that scope; architecture and safety contracts remain authoritative.

> **Strategic status (2026-08-04):** Start with the
> [`Cryo 10/10 Master Plan`](./cryo-10-of-10-plan.md) for current prioritization, evidence gates,
> non-goals, and release criteria. This file remains the detailed handoff for its original scope.
> Where future sequencing or status claims conflict, the 10/10 plan wins; root architecture and
> safety contracts remain authoritative.
>
> Written 2026-07-03. **This file is a handoff document.** It exists so that another AI agent (or
> a human) can pick up this repo with no other context and execute, in order, everything decided
> during the architecture/feature/AI strategy session that produced the three docs it references.
> It does not re-argue any decision — those are already made and cited below. It only sequences
> the work, points at exact files, and defines "done" for each step.

---

## 0. Read this first, in this order

Before writing any code, read these — in order — so decisions aren't re-litigated or contradicted:

1. [`../agents.md`](../agents.md) — hard requirements and repo conventions. **Do not violate
   these** (no Airflow; sources ≠ destinations; Planner emits no SQL strings — engine-specific
   *renderers* emit SQL or, for the new native engine, a structured payload; naming conventions).
2. [`../plan.md`](../plan.md), [`../hld.md`](../hld.md), [`../lld.md`](../lld.md) — original
   vision, architecture, and schemas. Still authoritative for anything not superseded below.
3. [`../todo.md`](../todo.md) — the phased P0–P7 checklist. **Keep it updated** as items in this
   plan overlap with or complete todo.md items.
4. [`current-state.md`](./current-state.md) — what's actually implemented today vs. planned.
   **Keep it updated** as you land work.
5. [`research-and-roadmap.md`](./research-and-roadmap.md) — the feature backlog, grounded in what's already
   built vs. real gaps, with a suggested phase sequencing. Also includes performance deep-dive and
   open-source governance notes.
6. [`architecture.md`](./architecture.md) — the decided control-plane architecture (event fast-path),
   the decided execution-engine architecture (native Rust engine + Trino/Spark interoperability),
   and the decided AI layer.

**This plan does not duplicate the "why" from those docs — it only says "what, in what order, and
how do you know it's done."** If a task here seems to lack rationale, the rationale is in one of
the docs above; go read it rather than guessing or re-deciding.

---

## 1. Ground rules for whoever executes this plan

- **Don't skip Phase 1.** Every later phase assumes the core execution path actually works
  end-to-end. Building features or the native engine on top of a non-functional Trino/Worker/
  Governor loop just compounds the gap.
- **Follow existing conventions** (`agents.md` §4): ruff + mypy + pytest must pass; layering is
  `api → repositories → db`, `worker → planner + engines`, `governor → engines (probe) + queue`;
  domain/planner never import db/engine clients; naming mirrors `lld.md` (`MaintenancePlan`,
  `Operation`, `PartitionPredicate`, `OperationType`, `EngineType`, `Mode`) — don't invent parallel
  vocabularies.
- **Golden tests are not optional.** Trino and Spark renderers already have golden tests
  (`todo.md` P2/P6). Every new engine or renderer path added in this plan must extend the same
  pattern — same `MaintenancePlan` fixture in, equivalent result out — not a new, separate test
  style.
- **Update docs as you go, not at the end:** tick boxes in `todo.md` and this file, keep
  `current-state.md`'s "Implemented / Partial / Planned" sections accurate. A future agent (or
  this same one, later) should be able to trust these files without re-reading all the code.
- **No speculative scope.** Each phase below lists exactly what to build. Don't add
  configurability, abstractions, or "while I'm here" extras beyond what's specified — if a good
  idea shows up mid-implementation that isn't in this plan, add it as a new item at the bottom of
  the relevant doc's backlog, don't build it inline.
- **Definition of done, every phase:** `make lint format typecheck test` pass; any new/changed
  engine or renderer has golden-test coverage; `todo.md` and `current-state.md` reflect the change;
  this file's checkboxes for the phase are ticked.

---

## 2. Execution order

### Phase 1 — Close the core plumbing gap (prerequisite for everything else) ✅

**Why:** per `research-and-roadmap.md` Part 1 §0, none of the performance or feature work matters if a
preset can't actually run against a real engine end-to-end yet.

- [x] `cryo/engines/trino/client.py` — per-coordinator connection (HTTPS + BasicAuth).
- [x] `cryo/engines/trino/load.py` — `system.runtime.queries` OPTIMIZE probe (parity with
      reference implementation per `agents.md` §2).
- [x] `cryo/engines/trino/engine.py` — implement `MaintenanceEngine.execute`; capture query id +
      errors.
- [x] Finish `cryo/governor/governor.py` — wave loop, round-robin replicas, per-table lock
      handoff, dispatch, reap, finalize run summary. Implemented with `run_wave()`, 
      `finalize_open_runs()`, and capacity provider integration.
- [x] Finish `cryo/worker/worker.py` — claim job → load stats → plan → engine execute → audit →
      counters. Implemented with `process_next()` and full job lifecycle.
- [x] Finish `cryo/scheduler/scheduler.py` — cron eval → create run + jobs → enqueue; cooldown
      pre-filter; priority order; leader-elected. Implemented with `SchedulerService.tick()`.
- [x] `cryo_spark_runner.py` — PySpark entrypoint executing CALL statements with Iceberg
      SparkSession. See `cryo/engines/spark/local.py` for local docker integration.
- [x] `cryo/engines/spark/emr.py` — EMR Serverless submit/poll/logs (boto3); capacity = slot cap
      − in-flight.
- [x] `cryo/engines/spark/engine.py` — implement `MaintenanceEngine`; wire into worker + governor.
- [x] Governor EMR slot governance; preset rule `size_gb > 100 → spark-large`. Implemented in
      `cryo/scheduler/routing.py` and `scripts/seed_local_config.py`.
- [x] CI pipeline: lint + typecheck + unit tests on push. See `.github/workflows/ci.yml`.

**Infrastructure additions:**
- [x] `cryo/infra/wiring.py` — Redis queue, locks, SQLJobStore, SQLSchedulerStore
- [x] `cryo/db/repositories/job_store.py` — Postgres-backed job store
- [x] `cryo/db/repositories/scheduler_store.py` — Scheduled runs + Redis last-run
- [x] `cryo/db/repositories/run_finalizer.py` — Governor finalizes open runs
- [x] `cryo/cli.py` — Unified CLI with `cryo governor|worker|scheduler|serve|events`

**Acceptance criteria:** ✅ Implemented — needs live Docker e2e validation with `make up`.

---

### Phase 2 — Finish the cost dashboard + cheap health-signal extensions ✅

**Why:** per `research-and-roadmap.md` Part 1 §2, the hard data work for cost accounting is already done
and persisted (`table_cost`, `table_cost_history`) — only wiring remains, making this the highest
ROI-per-hour item in the backlog. The health-signal extensions listed alongside it reuse data the
scanner already collects.

- [x] `/tables/{id}/cost` and `/dashboards/cost` API endpoints (plan-accurate `SavingsProjection`
      on `/maintenance/preview`; cost summary fields in the predicate grammar for filter/sort by
      savings; `/scans` accepts `cost_full_scan`). Implemented in `cryo/api/routes.py` and
      `cryo/api/preview_cost.py`.
- [x] Cost & Savings UI at `/cost` per [`research-and-roadmap.md` Part 7](./research-and-roadmap.md); Health page summary + link.
- [x] Surface `metadata_bytes / data_bytes` ratio as an explicit health metric. Implemented in
      `cryo/scanner/signals.py` (`metadata_to_data_ratio`) and `cryo/scanner/health.py`.
- [x] Streaming/commit-velocity detection: derive commits/day from snapshot-count deltas between
      consecutive `table_stats_history` rows; add an `is_streaming` flag. Implemented in
      `cryo/scanner/signals.py` (`detect_streaming_from_history`).
- [x] V2/V3 format-version detection: surface `format-version` and flag V2 tables with high
      `delete_file_pct` as V3-upgrade candidates. Implemented in `cryo/scanner/signals.py`
      (`detect_format_version`, `is_v3_upgrade_candidate`).
- [x] Standalone "rewrite manifests only" quick-fix action. Implemented as
      `POST /maintenance/manifest-rewrite` in `cryo/api/routes.py`.

**Acceptance criteria:** ✅ Backend complete — frontend UI pending.

---

### Phase 3 — Operations & safety features ✅

**Why:** these are the real gaps identified against the current planner/governor, per
`research-and-roadmap.md` Part 1 §2.

- [x] Hot/cold partition tracking: extend partition selection in `cryo/planner/` with a "no writes
      in N hours" filter. Implemented in `cryo/planner/planner.py` (`_is_hot_partition`).
- [x] Delete-ratio-triggered compaction mode: trigger an optimize run when `delete_file_pct`
      crosses a threshold even if small-file count alone wouldn't justify it. Implemented in
      `cryo/planner/planner.py` (`delete_ratio_trigger` param).
- [x] User-facing sequencing guardrail: API/UI-level check that blocks/warns if a user manually
      triggers ops out of order via ad-hoc runs. Implemented in `cryo/planner/guardrails.py`
      (`validate_operation_order`) and enforced in `/maintenance/preview` and `/maintenance/run`.

**Acceptance criteria:** ✅ Complete.

---

### Phase 4 — Event fast-path (control-plane architecture) ✅

**Why:** per `architecture.md` §1.2 — adds Netflix-style event-driven reaction
alongside the existing cron sweep, without replacing it.

- [x] New module `cryo/events/watcher.py` — polls `current-snapshot-id` at a tight interval for
      tables flagged "hot" (reuses the `is_streaming` flag from Phase 2); publishes a
      `TableCommitted` event to a Redis Stream on change.
- [x] New module `cryo/events/consumer.py` — Scanner and Governor subscribe as a Redis Streams
      consumer group; on `TableCommitted`, opportunistically re-score just that table and
      re-evaluate scheduling instead of waiting for the next full sweep.
- [x] `cryo/events/redis_stream.py` — Redis Streams-backed event bus.
- [x] `cryo/events/service.py` — Wiring for watcher and consumer.
- [x] `cryo cli events` — CLI command to run event loop.
- [x] The existing CronJob full-datalake scan is left unchanged as the slow-path/reliability
      backstop.

**Acceptance criteria:** ✅ Complete.

---

### Phase 5 — Native Rust execution engine (`cryo-engine`) ✅ Complete

**Why:** per `architecture.md` §1.5 — a first-class peer engine, not a
replacement for Trino/Spark.

- [x] `RenderedStatement.payload` + `EngineType.NATIVE` — backward compatible with Trino/Spark.
- [x] **`cryo-native`** (maturin/PyO3): production file-level binpack/sort compaction via DataFusion
      — `tests/test_native_engine.py` (integration tests when compiled).
- [x] **`cryo-engine`** subprocess: JSON stdin/stdout protocol; catalog-connected implementations for
      all four v1 ops (`rewrite_data_files`, `expire_snapshots`, `remove_orphan_files`,
      `rewrite_manifests`) in `cryo-engine/src/operations.rs`.
- [x] Python adapter `cryo/engines/native/engine.py` + `payload.py`; preset routing in
      `cryo/scheduler/routing.py`.
- [x] Golden-test parity path: native engine tests + shared `MaintenancePlan` fixtures with Trino/Spark.

**Acceptance criteria:** ✅ Complete for v1 scope (subprocess protocol + dual Rust crates + Python adapter).

---

### Phase 6 — Compliance & governance features ✅

**Why:** per `research-and-roadmap.md` Part 1 §2 — real gaps, genuine differentiators, verified against
real competitor features (Ryft).

- [x] Compliance/GDPR-CCPA deletion tracking: track tables with soft-deletes pending a snapshot-
      expiry cutoff. Implemented in `cryo/compliance/governance.py` (`track_pending_deletions`)
      and exposed via `GET /tables/{id}/compliance`.
- [x] Catalog latency monitoring: add timing around existing catalog calls in `cryo/sources/`;
      surface P50/P99 per catalog in dashboards. Implemented in `cryo/sources/latency.py`
      (`CatalogLatencyMonitor`) and `cryo/sources/monitored.py`. Exposed via
      `GET /sources/{id}/catalog-latency`.
- [x] Disaster recovery / named backup snapshots: allow tagging specific snapshots as protected so
      `expire_snapshots` skips them. Implemented as `protected_snapshots` table + 
      `POST /tables/{id}/protected-snapshots`. See `cryo/compliance/governance.py` 
      (`ProtectedSnapshotRegistry`) and migration `20260703_0003`.

**Acceptance criteria:** ✅ Complete.

---

### Phase 7 — AI layer ✅

**Why:** per `architecture.md` Part 2 and its own sequencing note — build in this
order specifically because each step depends on the data/signals the previous step establishes.

- [x] **Anomaly detection first, no LLM dependency:** classical EWMA/z-score time-series detection
      over `table_stats_history` — commit velocity, delete-file growth, manifest count growth,
      cost trajectory. Implemented in `cryo/ai/anomaly.py` (`detect_anomalies`). Exposed via
      `GET /tables/{id}/anomalies`.
- [x] **`explain_health_change(table, since)`:** given a deterministic before/after diff from
      `scanner/health.py` thresholds and `table_stats_history`, narrate it in plain English and
      point at the specific operation/preset that addresses it. Implemented in `cryo/ai/explain.py`.
      Exposed via `POST /tables/{id}/explain-health`.
- [x] **`cryo-mcp` server**, read-only tools first: `get_table_health`, `list_stale_tables`,
      `list_compaction_backlog`, `explain_health_change`, `simulate_preset`. Implemented in
      `cryo/mcp/server.py` and wired through `cryo/api/extensions.py` at `/mcp/tools` and
      `/mcp/tools/call`.
- [x] **Cryo Copilot:** curated semantic layer + a small "verified questions" library over Cryo's
      own Postgres metadata store (never freeform SQL against live Iceberg tables). Implemented in
      `cryo/ai/copilot.py`. Exposed via `GET /ai/copilot/questions` and `POST /ai/copilot/ask`.
- [x] **Gated `run_preset` MCP tool** (write path) — defined in tool descriptions; execution
      requires explicit role scope through existing `/maintenance/run` endpoint.

**Explicitly do not build** (see `architecture.md` §2.7): zero-shot NL-to-SQL
against live Iceberg tables; LLM-auto-approves-DDL/schema-changes; "AI-powered black-box autopilot"
positioning of any kind; vector-native storage in Iceberg/Cryo; full RL-based scheduling; full
"self-driving" whole-system autotuning.

**Acceptance criteria:** ✅ Complete.

---

### Phase 8 — Long-term / optional ✅

Only pursue these once Phases 1–7 are stable and there's a concrete driver (a real user request,
external contributor interest) — don't build ahead of need.

- [x] Multi-engine query routing (cost/latency-aware routing across Trino/Spark/native).
      Implemented in `cryo/scheduler/adaptive_routing.py`; preset guardrails `adaptive_routing`,
      `engine_pool`, `engine_preferences`; API `POST /api/v1/routing/preview`; scheduler wired.
- [x] Data tiering / lifecycle policies (move cold partitions to cheaper storage classes).
      `cryo/tiering/` + `tier_policies` table; API CRUD `/tier-policies` and
      `GET /tables/{id}/tier-candidates`. Migration `20260703_0004`.
- [x] Apache-style open governance formalization — **pre-v1 scope:** documented GitHub-first
      governance in `docs/GOVERNANCE.md` + updated `CONTRIBUTING.md`. Full PMC/CLA deferred per
      `research-and-roadmap.md` Part 5 until external contributor volume warrants it.

---

### Phase 9 — Embedding-based intelligence & research-backed AI track ✅ Complete

**Why:** added 2026-07-05, per `architecture.md` §2.5/§2.6 — real, verified
precedent (not speculation) for going beyond Phase 7's rule/threshold-based AI layer. Each item
below cites the specific paper/product precedent behind it; read §2.5/§2.6 for the full "why," not
just the "what."

**Zero-scan data quality signals (extends §2.4) — ✅ implemented 2026-07-05:**
- [x] Extended `scanner/analyze.py`'s existing manifest walk (`cryo/scanner/quality.py`,
      new module) to extract per-column `null_value_counts`/`value_counts` and decode
      `lower_bounds`/`upper_bounds` via `pyiceberg.conversions.from_bytes` using the schema's real
      field type (verified round-trip, not assumed) — zero additional data scans. Persisted to
      `table_stats.raw["column_quality"]` + `table_stats_history`; feeds `detect_anomalies` as a
      new `avg_null_rate` metric (`cryo/api/extensions.py`). Precedent:
      [arXiv:2605.30308](https://arxiv.org/pdf/2605.30308v1) (FORMATS '26 workshop paper).
      Puffin NDV stays a safe no-op stub (`_puffin_ndv_counts`) — real Puffin sketch reading needs
      a separate feasibility check (it's a table-level `statistics-files` artifact, not a
      per-`DataFile` field), not implemented.
- [x] **Real bug found and fixed along the way:** `cryo/quality/profiler.py` (a pre-existing,
      more sophisticated profiler with baseline/spike/schema-drift detection, exposed at
      `GET /data-quality/{table_id}`) was reading from `raw["manifest_stats"]`, a key nothing in
      the scanner ever wrote — dead code in production, passing its own tests only because they
      hand-seeded the phantom key directly. Rewired to consume the real `column_quality` data
      (`tests/test_quality_profiler_integration.py` updated to seed the real shape). Removed a
      simpler, now-redundant `GET /tables/{id}/column-quality` endpoint that had been added
      alongside the scanner work, in favor of this one, more capable, now-actually-working
      endpoint.
      **Acceptance criteria met:** 19 tests across `test_scanner_quality.py`,
      `test_scanner.py`, and `test_quality_profiler_integration.py` pass, including a real
      PyIceberg-type bounds round-trip test (not a mocked assumption); full backend suite green
      (114 passed); `ruff`/`mypy` clean on every touched file.

**Embedding-based fleet intelligence (§2.5) — ✅ implemented 2026-07-05:**
- [x] Embedding-based PII/sensitive-column classification — `cryo/ai/pii.py` + `cryo/ai/embeddings.py`;
      API: `GET /api/v1/tables/{id}/pii-classification`. Precedent: Databricks Unity Catalog Data Classification.
- [x] Embedding fallback for Cryo Copilot — `cryo/ai/copilot.py::answer_question` + embedding index;
      API: `POST /api/v1/ai/copilot/search`. Still never generates SQL against live Iceberg tables.
      Precedent: OpenMetadata semantic search.
- [x] Fleet-wide table/column similarity search — `cryo/ai/similarity.py`;
      API: `GET /api/v1/tables/{id}/similar` (>90% cosine similarity threshold).

**Research-backed track (§2.6) — ✅ implemented 2026-07-05 (production-wired 2026-07-05):**
- [x] Contextual-bandit tuning — `cryo/governor/bandit.py` + `bandit_feedback.py` records outcomes
      on admit (`register_admission`) and job completion (`finalize_admission` in worker); defers
      feed `record_deferred_admission`. API: `GET /api/v1/governor/bandit/status`.
- [x] Learned cost model — `cryo/ml/cost_model.py` + `cost_integration.py` caches model and applies
      via `apply_learned_cost` in `savings_projection_from_stats`; worker refreshes cache after jobs.
      API: `GET /api/v1/ai/cost-model/evaluate`.
- [x] AutoComp adaptive scheduling — `compare_scheduling_strategies` + planner benefit-score ranking.
- [x] TabPFN-style health classifier — `cryo/ml/health_classifier.py`; API:
      `GET /api/v1/tables/{id}/health-classification`.

**Embedding-based fleet intelligence (§2.5) — production-wired:**
- [x] PII classification feeds maintenance gating — `cryo/compliance/pii_gate.py` checked in
      `governor/governor.py` before admission (skip with `force` / `ignore_pii_gate`).
- [x] MCP tool surface complete per §2.2 — `get_table_schema`, `get_snapshot_history`, `run_preset`
      added to `cryo/mcp/server.py` + `extensions.py` handlers.

**Explicitly deferred, not just deprioritized (see §2.6 for why):**
- Full RL-based scheduling (Decima/Park-style) — real sim-to-real fragility, use the bandit above
  instead.
- Time-series foundation models (Chronos-2/TimesFM/Moirai) for per-table forecasting — a 2025 paper
  found these degrade on exactly Cryo's sparse, per-entity data shape; worth a research spike only,
  not a committed feature.
- TabPFN-TS (tabular-regression-as-forecasting, using table metadata as covariates rather than
  forecasting each table's series in isolation) — the most-tailored-fitting idea found for Cryo's
  exact data shape (per-table series + rich per-table metadata), but very recent and unproven
  outside benchmarks; a dedicated research spike, not a committed feature, precisely because it's
  the best fit not despite it.
- GNN-based automatic join/lineage discovery (OmniMatch et al.) — real and promising, but needs
  engineering investment (fleet-scale sampling, model serving) beyond what's scoped here.
- Causal inference for realized-savings rigor (Sawmill) — do this only after the learned cost
  model above has enough outcome history to support it.

---

### Phase 10 — AWS S3 free-tier cost/usage integration ✅ Complete

**Why:** added 2026-07-05, user-requested — get real S3 cost/usage signal into Cryo's cost
projections. Hard constraint from the user, honored throughout: **zero paid AWS API calls, ever**
(no Cost Explorer, no CUR, no CloudWatch request-metrics configuration, no `get_metric_data`).

- [x] `AWSIntegration` model (per-`Source`, opt-in, encrypted-secret-ref, not raw secret storage) —
      `cryo/db/models.py`; migration `20260705_0005_aws_integration.py`.
- [x] Connection test via `sts:get_caller_identity` (free, no resource access needed) —
      `cryo/integrations/aws_s3_metrics.py::test_aws_connection`.
- [x] Bucket size + object count via CloudWatch's daily auto-published storage metrics
      (`BucketSizeBytes` per storage class, `NumberOfObjects`), read with `get_metric_statistics`
      — deliberately **not** `get_metric_data`, which AWS bills unconditionally even inside the
      free tier. Enforced in code and tested (`test_aws_s3_metrics.py` asserts `get_metric_data`
      is never called).
- [x] Real S3 $/GB pricing via the free `pricing:GetProducts` API, queried against `us-east-1`
      regardless of the bucket's actual region (an AWS API constraint, not a Cryo choice).
- [x] Full CRUD + test + usage API — `cryo/api/routes.py`
      (`/sources/{id}/aws-integration*`, `/sources/{id}/aws-usage`).
- [x] Frontend: `web/src/components/AwsIntegrationPanel.tsx` (form + "Test STS" + live metrics),
      embedded in `web/src/pages/Connections.tsx` behind the edit-mode `Collapse` section.
- [x] Tests: `tests/test_aws_s3_metrics.py` (8 tests, mocked `boto3.Session`),
      `tests/test_aws_integration_api.py` (9 tests against the route handlers directly).

---

## 3. Summary — Implementation Status

| Phase | Status | Notes |
|-------|--------|-------|
| **1 — Core plumbing** | ✅ Complete | All components wired; needs live Docker e2e validation |
| **2 — Cost dashboard** | ✅ Backend done | Frontend UI pending |
| **3 — Ops & safety** | ✅ Complete | Guardrails, hot partition skip, delete-ratio trigger |
| **4 — Event fast-path** | ✅ Complete | Redis Streams watcher/consumer |
| **5 — Native engine** | ⚠️ Partial | Python adapter done; Rust crate is scaffold only |
| **6 — Compliance** | ✅ Complete | Protected snapshots, latency monitoring, deletion tracking |
| **7 — AI layer** | ✅ Complete | Anomaly, explain, MCP, Copilot |
| **8 — Long-term** | ✅ Complete | Adaptive routing, tiering, governance docs |
| **9 — Embedding/research-backed AI** | 🔴 Not started | Added 2026-07-05; each item needs its own validation pass |

---

## 4. Quick reference — doc-to-phase map

| Phase | Primary doc(s) |
|---|---|
| 1 (core plumbing) | `../todo.md` P2/P3/P6, `research-and-roadmap.md` Part 1 §0 |
| 2 (cost + cheap health signals) | `research-and-roadmap.md` Part 1 §2, `research-and-roadmap.md` Part 7 |
| 3 (ops & safety) | `research-and-roadmap.md` Part 1 §2 |
| 4 (event fast-path) | `architecture.md` §1.1–1.2 |
| 5 (native engine) | `architecture.md` §1.5, `research-and-roadmap.md` Part 4 |
| 6 (compliance/governance features) | `research-and-roadmap.md` Part 1 §2 |
| 7 (AI layer) | `architecture.md` Part 2 (§2.1–2.4, §2.7) |
| 8 (long-term) | `research-and-roadmap.md` Part 1 §2 (⏳ Later), `research-and-roadmap.md` Part 5 |
| 9 (embedding/research-backed AI) | `architecture.md` §2.5–2.6 |

---

## 5. If something in this plan conflicts with the code you find

The codebase is the ground truth for *what currently exists*; this plan and its referenced docs
are the ground truth for *what to build next and why*. If you find the code has moved on since
2026-07-03 (new modules, renamed files, completed items this plan assumes are pending), trust the
code, update `current-state.md` and this file to match reality, and proceed with the intent of the
plan rather than its exact file-path assumptions.

---

## 6. Remaining Work

### Must-do before v1:
1. **Live Docker e2e validation** — `make up && make smoke-compact`
2. **Frontend Cost UI** — implement `research-and-roadmap.md` Part 7 spec
3. **Native engine Rust implementation** — integrate iceberg-rust + DataFusion

### Nice-to-have:
- Full golden-test parity across all three engines
- MCP server as standalone process (currently API-embedded)
- Tiering execution (currently identifies candidates only) — researched and phased in
  [`ARCHIVE_TIERING_RESEARCH_2026-10-01.md`](./ARCHIVE_TIERING_RESEARCH_2026-10-01.md)
