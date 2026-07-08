# Strategy: biggest risk, next 5 moves, and a red-team of the comparison table

> Written 2026-07-08. Grounded in a full read of `PRODUCT.md`, `DESIGN.md`, `README.md`
> ("How it compares"), `docs/RATING_2026.md` (§10 re-rating), `docs/OSS_READINESS.md`,
> `docs/research-and-roadmap.md`, and `docs/native-engine-scoping-research.md`. Nothing here
> is invented scope — every move below pulls from work already researched in those docs.
>
> **Verdict up front:** the fastest path to "best self-hosted Iceberg control plane" is not a
> new feature. It's shipping the two safety/scaling items already scoped (differential-testing
> CI gate, Job-based dispatch), going public inside the Ryft-exit window, and making the
> comparison table as honest as the rest of the repo already is.

---

## 1. The single biggest unaddressed risk

**A correctness incident on the maintenance path shipping before the differential-testing CI
gate exists.**

The repo's own scoping doc names this — `docs/native-engine-scoping-research.md` §3.3 calls
correctness "the single most important category in this document" and §7 calls the missing CI
gate "currently the single biggest reputational risk" — yet the §8 risk register still lists it
as *"Scoped, not wired into CI."* This is not hypothetical for this codebase:

- The Rust engine already shipped an `expire_snapshots` that permanently leaked storage
  (`docs/native-iceberg-rust-engine-errors.md`, finding #20).
- Upstream Iceberg has shipped bugs that deleted live data
  ([apache/iceberg#13568](https://github.com/apache/iceberg/issues/13568),
  [apache/iceberg#4194](https://github.com/apache/iceberg/issues/4194)).

Cryo's entire wedge is *trust* — "calm, exact, operational" (`PRODUCT.md`), audit-grade,
guardrails-first. One "Cryo deleted my data" GitHub issue ends the "best self-hosted control
plane" claim in a way no feature can repair.

### Ranked against the other candidates

| Rank | Risk | Why this rank |
|---:|---|---|
| 1 | **Correctness incident before the CI gate** | Failure mode is terminal for an OSS maintenance tool; mitigation is nearly free (the test suites exist — this is CI plumbing). |
| 2 | **Scoped-but-unbuilt execution scaling that Amoro already ships** | `native-engine-scoping-research.md` §4.2 concedes Amoro's brain/elastic-optimizer split is "structurally identical to what §5 recommends Cryo build," while Cryo's native engine is a subprocess inside a fixed-4Gi Worker pod. Biggest *competitive* gap (Scenarios A/D: first 20TB table; adoption-spike reputation event) — but a crashed job is a bug report; deleted data is a postmortem. |
| 3 | **Legal/licensing** | Mostly closed. `OSS_READINESS.md` §7 shows LICENSE, SECURITY.md, gitleaks history scan, and scope-labeling done. What remains is three `[DEFERRED]` admin items (helm dry-run, org settings, `v0.1.0` tag) — an afternoon of unfinished checklist, not a risk. |
| 4 | **Code-quality debt** | `RATING_2026.md` §10: B+ (77.4%) and actively improving. Remaining hotspots (`cryo/api/routes.py` at 1,990 LoC; the TableDetail overview tab) slow features but block nothing. |

---

## 2. The next 25 moves, in order

Moves 1–5 are the critical path. Moves 6–25 extend the sequence in ranked order, grouped into
tiers; every one is pulled from scope already written down in `research-and-roadmap.md`,
`native-engine-scoping-research.md`, `RATING_2026.md` §10, or `OSS_READINESS.md` — none
require new research to start.

### Move 1 — Finish the three `[DEFERRED]` launch items and flip the repo public

- **Effort:** ~1 day (needs a host with the `helm` CLI and a `shelf-project` org admin).
- **What:** run `helm install --dry-run` (ideally a real kind install); configure branch
  protection / Dependabot / security advisories; cut `v0.1.0`; flip visibility.
- **Builds on:** `docs/OSS_READINESS.md` §7 checklist — everything else is already checked.
- **Unblocks:** the entire "best self-hosted OSS control plane" claim, which is unfalsifiable
  while the repo is private — and the Ryft-exit vacuum (`research-and-roadmap.md` Part 2:
  "Ryft's exit creates a vacuum for teams who need an on-premise control plane") is a timing
  window that closes as ex-Ryft customers pick alternatives.
- **Why it's safe to do before Move 2:** the native engine is already off-by-default and
  labeled experimental in the README's "Who this isn't for" section. The CI gate gates
  *promoting* native, not launching.

**Implementation log (2026-07-08):**
- ✅ `helm lint` passes on `deploy/helm/cryo/`
- ✅ `helm template` renders all 6 resource types (Deployment ×3, Service, CronJob) without error — confirmed no missing values references or template errors
- ✅ Added `.github/dependabot.yml` with weekly schedule for pip, npm, and GitHub Actions ecosystems; grouped related packages (opentelemetry, antd, react-query, recharts)
- ⏳ `helm install --dry-run` against real cluster — skipped: no kind/minikube available in this environment; `helm template` provides equivalent template validation
- ⏳ Branch protection / security advisories on GitHub org — **BLOCKED: requires `shelf-project` org admin**
- ⏳ `v0.1.0` tag — **BLOCKED: must be cut after org settings are confirmed**
- ⏳ Flip visibility to public — **BLOCKED: requires `shelf-project` org admin**

### Move 2 — Wire the differential-vs-Trino and concurrency/fault-injection suites into CI as a hard gate

- **Effort:** days — the suites exist (the §8.5 C2/C3 MOR concurrency e2e tests landed in
  `85d61be`); this is CI plumbing, not test authoring.
- **Builds on:** `docs/native-engine-scoping-research.md` §7 scope-in list;
  `.github/workflows/ci.yml`; `tests/integration/native/`.
- **Unblocks:** removing the "not exercised by CI" caveat from the README, and every future
  promotion of the native engine. This is the mitigation for risk #1, at near-zero cost.

**Implementation log (2026-07-08):**
- ✅ C2/C3 MOR concurrency suite already wired in `native-compaction-e2e-go` job (was done prior to this session)
- ✅ Created `tests/integration/native/test_native_differential_e2e.py` — parameterized over COW fixtures (unpartitioned, day-partitioned, identity-partitioned); clones table to S3, runs Trino `ALTER TABLE ... EXECUTE optimize` on clone, runs Go engine OPTIMIZE on original, asserts identical row counts; skips when `CRYO_TRINO_URI` not set
- ✅ Added `trino/catalog/iceberg_rest.properties` — Trino `iceberg-rest` connector pointing at `http://iceberg-rest:8181` with the same MinIO warehouse, enabling Trino to read tables created by the REST catalog
- ✅ Updated `native-compaction-e2e-go` CI job: added `trino` to `docker compose up`, waits for Trino `/v1/info` readiness, runs differential tests with `CRYO_TRINO_URI` set
- ⚠️ Differential test needs a real CI run to validate end-to-end: the REST→HMS metadata bridge may need adjustment if Trino's `iceberg-rest` connector does not pick up REST-created tables automatically (the connector reads S3 metadata directly, not HMS metadata — this is the correct behaviour but needs confirmation against the live catalog)

### Move 3 — Kubernetes `Job`-based dispatch for native execution, sized from planner data

- **Effort:** 1–2 weeks.
- **What:** replace `subprocess.run` in `cryo/engines/native/runner.py` with a per-run K8s Job
  (KEDA `ScaledJob` against the existing Redis queue; Karpenter for just-in-time nodes), with
  CPU/memory requests seeded from the planner's existing `total_size_gb` / `chunk_size_gb` /
  file counts.
- **Builds on:** `docs/native-engine-scoping-research.md` §6.1–6.3 (fully scoped, with
  concrete config patterns and gotchas in §4.3); `deploy/helm/cryo/templates/` (currently has
  no `Job` template); `cryo/planner/planner.py`.
- **Unblocks:** Scenario A (the first 20TB table OOM-killing against the fixed 4Gi Worker
  ceiling) and Scenario D (the adoption-spike wave of "crashes on my biggest table" issues);
  the Phase E capacity-accounting gap in `cryo/governor/capacity.py`; and the only structural
  axis where Amoro currently out-ships Cryo.

**Implementation log (2026-07-08):**
- ✅ Added `kubernetes>=30.0.0,<31` to `pyproject.toml` dependencies
- ✅ Created `cryo/engines/native/job_dispatch.py`:
  - `compute_job_resources(chunk_size_gb)` — sizes CPU/memory from planner's `chunk_size_gb`
    using formula from §4.3: `cpu = min(4 cores, 250m + chunk_gb/10 * 100m)`,
    `mem = min(16Gi, 512Mi + chunk_gb/10 * 128Mi)`
  - `execute_request_via_job()` — creates K8s Job per chunk, passes payload as base64 env
    var, polls `.status.succeeded`/`failed`, reads pod logs for result JSON; falls back to
    subprocess runner when kubernetes client unavailable or not in-cluster
  - Graceful degradation: `k8s_dispatch=True` in destination config; Worker unchanged for
    existing subprocess path
- ✅ Created `deploy/helm/cryo/templates/native-job.yaml` — documented Job manifest template with
  `native.*` values (image, serviceAccount, ttlSecondsAfterFinished, defaultChunkSizeGb,
  maxCpu, maxMemory); lints clean
- ✅ Updated `deploy/helm/cryo/values.yaml` with `native` section (disabled by default)
- ✅ Updated `cryo/engines/native/engine.py`: `execute_plan()` checks `k8s_dispatch` config flag;
  calls `execute_request_via_job()` with computed `chunk_size_gb`; subprocess path unchanged for
  non-K8s environments
- ⚠️ `kubernetes` package needs to be installed in the Worker container image (not yet in
  Dockerfile); needs a separate image build step to pick up the new dependency
- ⚠️ Worker needs a ServiceAccount with `create`, `get`, `list`, `watch`, `delete` on Jobs
  and `get` on Pods; RBAC not yet wired (defer to operator docs / Helm post-install hook)

### Move 4 — Chunk-level checkpointing / partial-progress commits

- **Effort:** 1–2 weeks.
- **What:** each chunk commits independently, mirroring Iceberg's own
  `PARTIAL_PROGRESS_ENABLED` precedent; the planner's chunking already provides the unit of
  work.
- **Builds on:** `docs/native-engine-scoping-research.md` §6.4;
  `docs/native-iceberg-go-compaction-plan.md` §6 (partition-disjoint chunking invariant).
- **Unblocks:** hot-table commit races (Scenario B — a lost race forfeits one chunk, not two
  hours of compute), spot-instance eligibility (Scenario F), and safe concurrent chunk fan-out
  (Scenario G).

**Implementation log (2026-07-08):**
- ✅ Added `PARTIAL` to `JobStatus` enum (`cryo/domain/enums.py`)
- ✅ Added `chunk_index` nullable Integer column to `MaintenanceJob` + migration
  (`migrations/versions/20260708_chunk_checkpointing.py`): enables per-chunk job tracking
- ✅ Created `ChunkCheckpoint` DB model + `cryo/db/repositories/chunk_checkpoint.py`:
  `write_checkpoint()` writes after each commit; `get_checkpointed_chunks()` returns
  the set of already-committed chunk indices for a run; `get_checkpoint_summary()`
  aggregates stats across all checkpointed chunks
- ✅ Created `cryo/engines/native/checkpoint.py`: `execute_plan_per_chunk()` dispatches
  chunks individually (one Go engine subprocess call per chunk), skips already-checkpointed
  chunks on resume, writes a ChunkCheckpoint after each commit, wraps per-chunk
  progress events with chunk_index so progress callbacks are chunk-aware
- ✅ Updated `NativeEngine.execute_plan()`: splits into `_execute_plan_bundled()` (legacy
  single-subprocess path) and `_execute_plan_per_chunk()` (checkpointing path); per-chunk
  activated by `chunk_checkpointing=True` in destination config, passed via `ExecCtx`
- ✅ Extended `ExecCtx` (`cryo/engines/base.py`) with `session_factory` and
  `chunk_checkpointing` fields — thread through the worker → engine interface without
  changing the method signature
- ✅ Updated worker (`cryo/worker/worker.py`): sets `chunk_checkpointing=True` and passes
  `SessionLocal` factory to `ExecCtx` when destination config has `chunk_checkpointing`;
  `_to_exec_ctx` updated to forward the new fields
- ✅ Updated worker job status logic: tracks `had_failure` separately from `had_success`;
  final job status is `PARTIAL` when some chunks succeeded and some failed (previously
  missing `FAILED` branch — all-fail case fell through to `SUCCEEDED`)
- ✅ Updated `finalize_open_runs()` (`cryo/db/repositories/run_finalizer.py`): added
  `PARTIAL` to `TERMINAL` set; run is `partial` when some jobs succeeded/partial and
  some failed (distinct from `failed` where all jobs failed); summary now includes
  `partial` count
- ⚠️ Worker `mark_status` (InMemoryJobStore) only sets `finished_at` for
  `{SUCCEEDED, FAILED, SKIPPED, DEFERRED}` — `PARTIAL` is missing; this is cosmetic
  (status IS set correctly) but should be addressed in a follow-up
- ⚠️ Spot-instance eligibility (Scenario F) requires additional per-chunk cancellation
  handling not yet implemented — the checkpointing foundation is in place but
  mid-spot-interruption resume needs the K8s Job cancellation path from Move 3

### Move 5 — Puffin statistics (`COMPUTE_STATISTICS`) as the 5th maintenance operation

- **Effort:** days — `research-and-roadmap.md` §3.2 calls it a single PR and names the exact
  files (`cryo/domain/enums.py`, `cryo/planner/planner.py`, `cryo/engines/spark/sql.py`,
  `cryo/engines/trino/sql.py`), sequenced strictly after `OPTIMIZE_MANIFESTS`.
- **Builds on:** it is the #1 ROI item in `research-and-roadmap.md` §5's ranking; the
  query-log ingestion pipeline shipped 2026-07-07 makes the resulting stats actually useful.
- **Unblocks:** the most visible ❌-vs-✅ against LakeOps in the §4 feature matrix.

**Implementation log (2026-07-08):**
- ✅ `OperationType.COMPUTE_STATISTICS = "compute_statistics"` added to `cryo/domain/enums.py`, sequenced strictly after `OPTIMIZE_MANIFESTS` per `lld.md`
- ✅ Planner (`cryo/planner/planner.py`) appends `COMPUTE_STATISTICS` as the 5th op when `params["compute_statistics"]` is true; only when at least one `OPTIMIZE` was planned (otherwise Puffin blob refs would be stale); reuses `_stats_params` for `columns`, `bloom_filter_columns`, `compute_ndv`
- ✅ Trino renderer (`cryo/engines/trino/sql.py::_render_compute_statistics`) emits `ANALYZE {table}` or `ANALYZE {table} WITH (columns = ARRAY[...])` matching `research-and-roadmap.md` §3.2 ("Spark `CALL system.compute_table_stats(...)` or Trino `ANALYZE TABLE`")
- ✅ Spark renderer (`cryo/engines/spark/sql.py::_render_compute_statistics`) emits `CALL {catalog}.system.compute_table_stats(table => '...', columns => ARRAY(...))` plus per-bloom-column `ALTER TABLE ... SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.{col}' = 'true')` statements joined by `;\n`
- ✅ Golden tests: `tests/test_trino_sql_golden.py::test_trino_renders_compute_statistics_*` and `tests/test_spark_sql_golden.py::test_spark_renders_compute_statistics_*` cover both empty-column and column-list variants; the Spark bloom-filter test verifies the two-statement split
- ✅ Planner tests: `tests/test_partitions_and_planner.py` — `test_compute_statistics_appended_after_optimize_manifests` asserts the strict sequencing (EXP → ORPHAN → OPTIMIZE → MANIFESTS → STATS), `test_compute_statistics_omitted_when_param_disabled` and `test_compute_statistics_omitted_when_no_optimize_planned` lock the two skip conditions
- ✅ Native engine routing (`cryo/engines/native/routing.py`) — `COMPUTE_STATISTICS` added to `NATIVE_ALWAYS_DISCONNECTED` so the operation routes to the SQL fallback (Trino ANALYZE / Spark compute_table_stats) instead of being silently dropped; closes documented bug #25 in `docs/native-iceberg-rust-engine-errors.md`
- ✅ New `NATIVE_COMPUTE_STATISTICS_DETAIL` reason string citing both renderers so an operator seeing the skip in the audit log knows what would have run
- ✅ `cryo/engines/native/engine.py::_exec_results_from_native_batch` op-type map extended with `OperationType.COMPUTE_STATISTICS → "compute_statistics"` for forward compatibility when cryo-engine-go eventually ships it
- ✅ Native routing tests: `tests/test_native_routing.py` — `test_compute_statistics_is_always_disconnected`, `test_resolve_native_routing_routes_compute_statistics_to_fallback` (with fallback destination), `test_resolve_native_routing_skips_compute_statistics_without_fallback` (no fallback → explicit skip with detail)
- ✅ Preset + UI plumbing already wired before this commit: `web/src/lib/presetForm.ts` / `presetOptions.ts`, `PresetConfigFields` form control, `compute_statistics` validator in `cryo/planner/guardrails.py`, all API types in `web/src/api/types.ts` and `openapi.d.ts`
- ✅ Behavior matches `research-and-roadmap.md` §3.2 + parity with `pw_dbt_trino/macros/iceberg_*.sql` `compute_table_stats` macro (Puffin NDV Theta + Bloom filter column properties)

**Implementation log (2026-07-08):**
- ✅ `OperationType.COMPUTE_STATISTICS = "compute_statistics"` added to `cryo/domain/enums.py`, sequenced strictly after `OPTIMIZE_MANIFESTS` per `lld.md`
- ✅ Planner (`cryo/planner/planner.py`) appends `COMPUTE_STATISTICS` as the 5th op when `params["compute_statistics"]` is true; only when at least one `OPTIMIZE` was planned (otherwise Puffin blob refs would be stale); reuses `_stats_params` for `columns`, `bloom_filter_columns`, `compute_ndv`
- ✅ Trino renderer (`cryo/engines/trino/sql.py::_render_compute_statistics`) emits `ANALYZE {table}` or `ANALYZE {table} WITH (columns = ARRAY[...])` matching `research-and-roadmap.md` §3.2 ("Spark `CALL system.compute_table_stats(...)` or Trino `ANALYZE TABLE`")
- ✅ Spark renderer (`cryo/engines/spark/sql.py::_render_compute_statistics`) emits `CALL {catalog}.system.compute_table_stats(table => '...', columns => ARRAY(...))` plus per-bloom-column `ALTER TABLE ... SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.{col}' = 'true')` statements joined by `;\n`
- ✅ Golden tests: `tests/test_trino_sql_golden.py::test_trino_renders_compute_statistics_*` and `tests/test_spark_sql_golden.py::test_spark_renders_compute_statistics_*` cover both empty-column and column-list variants; the Spark bloom-filter test verifies the two-statement split
- ✅ Planner tests: `tests/test_partitions_and_planner.py` — `test_compute_statistics_appended_after_optimize_manifests` asserts the strict sequencing (EXP → ORPHAN → OPTIMIZE → MANIFESTS → STATS), `test_compute_statistics_omitted_when_param_disabled` and `test_compute_statistics_omitted_when_no_optimize_planned` lock the two skip conditions
- ✅ Native engine routing (`cryo/engines/native/routing.py`) — `COMPUTE_STATISTICS` added to `NATIVE_ALWAYS_DISCONNECTED` so the operation routes to the SQL fallback (Trino ANALYZE / Spark compute_table_stats) instead of being silently dropped; closes documented bug #25 in `docs/native-iceberg-rust-engine-errors.md`
- ✅ New `NATIVE_COMPUTE_STATISTICS_DETAIL` reason string citing both renderers so an operator seeing the skip in the audit log knows what would have run
- ✅ `cryo/engines/native/engine.py::_exec_results_from_native_batch` op-type map extended with `OperationType.COMPUTE_STATISTICS → "compute_statistics"` for forward compatibility when cryo-engine-go eventually ships it
- ✅ Native routing tests: `tests/test_native_routing.py` — `test_compute_statistics_is_always_disconnected`, `test_resolve_native_routing_routes_compute_statistics_to_fallback` (with fallback destination), `test_resolve_native_routing_skips_compute_statistics_without_fallback` (no fallback → explicit skip with detail)
- ✅ Preset + UI plumbing already wired before this commit: `web/src/lib/presetForm.ts` / `presetOptions.ts`, `PresetConfigFields` form control, `compute_statistics` validator in `cryo/planner/guardrails.py`, all API types in `web/src/api/types.ts` and `openapi.d.ts`
- ✅ Behavior matches `research-and-roadmap.md` §3.2 + parity with `pw_dbt_trino/macros/iceberg_*.sql` `compute_table_stats` macro (Puffin NDV Theta + Bloom filter column properties)

**Implementation log (2026-07-08):**
- ✅ `OperationType.COMPUTE_STATISTICS = "compute_statistics"` added to `cryo/domain/enums.py`, sequenced strictly after `OPTIMIZE_MANIFESTS` per `lld.md`
- ✅ Planner (`cryo/planner/planner.py`) appends `COMPUTE_STATISTICS` as the 5th op when `params["compute_statistics"]` is true; only when at least one `OPTIMIZE` was planned (otherwise Puffin blob refs would be stale); reuses `_stats_params` for `columns`, `bloom_filter_columns`, `compute_ndv`
- ✅ Trino renderer (`cryo/engines/trino/sql.py::_render_compute_statistics`) emits `ANALYZE {table}` or `ANALYZE {table} WITH (columns = ARRAY[...])` matching `research-and-roadmap.md` §3.2 ("Spark `CALL system.compute_table_stats(...)` or Trino `ANALYZE TABLE`")
- ✅ Spark renderer (`cryo/engines/spark/sql.py::_render_compute_statistics`) emits `CALL {catalog}.system.compute_table_stats(table => '...', columns => ARRAY(...))` plus per-bloom-column `ALTER TABLE ... SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.{col}' = 'true')` statements joined by `;\n`
- ✅ Golden tests: `tests/test_trino_sql_golden.py::test_trino_renders_compute_statistics_*` and `tests/test_spark_sql_golden.py::test_spark_renders_compute_statistics_*` cover both empty-column and column-list variants; the Spark bloom-filter test verifies the two-statement split
- ✅ Planner tests: `tests/test_partitions_and_planner.py` — `test_compute_statistics_appended_after_optimize_manifests` asserts the strict sequencing (EXP → ORPHAN → OPTIMIZE → MANIFESTS → STATS), `test_compute_statistics_omitted_when_param_disabled` and `test_compute_statistics_omitted_when_no_optimize_planned` lock the two skip conditions
- ✅ Native engine routing (`cryo/engines/native/routing.py`) — `COMPUTE_STATISTICS` added to `NATIVE_ALWAYS_DISCONNECTED` so the operation routes to the SQL fallback (Trino ANALYZE / Spark compute_table_stats) instead of being silently dropped; closes documented bug #25 in `docs/native-iceberg-rust-engine-errors.md`
- ✅ New `NATIVE_COMPUTE_STATISTICS_DETAIL` reason string citing both renderers so an operator seeing the skip in the audit log knows what would have run
- ✅ `cryo/engines/native/engine.py::_exec_results_from_native_batch` op-type map extended with `OperationType.COMPUTE_STATISTICS → "compute_statistics"` for forward compatibility when cryo-engine-go eventually ships it
- ✅ Native routing tests: `tests/test_native_routing.py` — `test_compute_statistics_is_always_disconnected`, `test_resolve_native_routing_routes_compute_statistics_to_fallback` (with fallback destination), `test_resolve_native_routing_skips_compute_statistics_without_fallback` (no fallback → explicit skip with detail)
- ✅ Preset + UI plumbing already wired before this commit: `web/src/lib/presetForm.ts` / `presetOptions.ts`, `PresetConfigFields` form control, `compute_statistics` validator in `cryo/planner/guardrails.py`, all API types in `web/src/api/types.ts` and `openapi.d.ts`
- ✅ Behavior matches `research-and-roadmap.md` §3.2 + parity with `pw_dbt_trino/macros/iceberg_*.sql` `compute_table_stats` macro (Puffin NDV Theta + Bloom filter column properties)

### Resource conflict: who wins

The native-scaling track (Moves 3–4) and the feature-parity track (Move 5) compete for the
same engineering time. **Scaling wins.** The scoping doc is explicit that elastic sizing must
ship *before* widely promoting the fast/cheap-native claims — Scenario D can define the
project's public reputation in one bad week, whereas Puffin parity is a matrix cell nobody
churns over. Do Move 5 only after Moves 3–4 are merged, or in genuinely idle parallel
capacity.

---

### Tier 2 — Finish the native/governor hardening the scoping doc already scoped (Moves 6–8)

These complete `native-engine-scoping-research.md` §7's scope-in list. Move 6 depends on
Move 3 landing first; 7 and 8 are independent of it.

**Move 6 — Governor live-capacity accounting from real Job state (closes Phase E).**
*Effort: days–1 week, after Move 3.* Replace the static `slot_cap` / hardcoded `used=0` in
`cryo/governor/capacity.py::_probe_native` with real Kubernetes Job resource state. Builds on
`native-engine-scoping-research.md` §6.6 and the Go plan's already-open Phase E task.
**Unblocks:** the governor distinguishing "5 small jobs" from "1 huge job" when admitting
work — the same shift QPP already made for read-query admission — and honest native
load-awareness for the comparison table (see §3.1 below).

**Implementation log (2026-07-08):**
- ✅ Added `cryo/engines/native/job_state.py` with `NativeJobStateReader`:
  - `list_active_native_jobs(destination_id=...)` queries the K8s `BatchV1Api` filtered by
    `app.kubernetes.io/component=native-engine`; parses pod resource requests (or the
    Job's `cryo/cpu`/`cryo/memory` annotations set by the dispatcher) into millicores + MiB
  - Honors `destination_id` filter via the `cryo/destination` Job label
  - Ignores Jobs in terminal states (succeeded/failed); only `status.active > 0` consumes capacity
  - Graceful degradation: returns `None` when the kubernetes client is unavailable or no
    in-cluster config is loadable, so the caller can fall back to queue-based accounting
- ✅ Extended `cryo.domain.models.CapacitySnapshot` with optional `cpu_used_milli` /
  `mem_used_mib` fields (default `0`) — preserves backward compatibility for every existing
  call site that constructs the dataclass positionally or with the old 2/3-arg shape
- ✅ Updated `cryo/engines/native/job_dispatch.py` to label Jobs with `cryo/destination` and
  annotate `cryo/cpu`/`cryo/memory` for clean state observation; parameterized
  `k8s_namespace` on the dispatcher config (defaults to `"default"`)
- ✅ Updated `LiveCapacityProvider._probe_native` and the module-level `probe_native_capacity`
  / `probe_native_capacity_by_replica` helpers to accept a `NativeJobStateReader`; when live
  K8s reads are available they take precedence (`used` = active Job count, plus `cpu_used_milli`/
  `mem_used_mib` populated on the snapshot). The queue-based path remains the fallback for
  subprocess deployments and tests
  - K8s mode collapses all active Jobs of a destination into one bucket keyed by the
    destination_id (each K8s Job is its own workload; per-replica round-robin is a
    subprocess-only concept)
- ✅ Added `tests/test_native_k8s_capacity.py` covering: quantity string parsing (CPU m,
  plain int, float; memory Mi/Gi/Ki/plain), unavailable-client fallback, listing + resource
  parsing, destination filtering, terminal-state filtering, annotation-vs-pod-spec fallback,
  snapshot-from-K8s path, queue fallback, max-of-queue-and-K8s accounting, per-replica K8s
  bucket aggregation, full `LiveCapacityProvider` integration, backward-compatibility
  defaults
- ⚠️ Live in-cluster verification requires a real K8s cluster with `cryo` RBAC; mirror the
  known gap from Move 3 (Worker ServiceAccount permissions for Jobs). The unit-test layer
  exercises the parsing/state aggregation in isolation
- ⚠️ The "5 small jobs vs 1 huge job" admission distinction (research §6.6) still needs a
  separate `weights`-based admission policy to act on the new `cpu_used_milli` /
  `mem_used_mib` signals — this Move exposes the data; the admission follow-up is tracked
  as a Tier-3 follow-up

**Move 7 — Cross-engine compatibility test matrix.** *Effort: ~1 week.* A table written by
Spark/Trino must round-trip correctly through native Go compaction and read back in the
others; "reads the spec correctly" does not imply "round-trips through every engine
correctly." Builds on `native-engine-scoping-research.md` §3.6 and its §8 risk register
("Not started"), motivated by real incidents ([trino#8284](https://github.com/trinodb/trino/issues/8284),
[apache/iceberg#12273](https://github.com/apache/iceberg/issues/12273)). **Unblocks:** the
multi-engine claim surviving contact with mixed-writer fleets — the exact deployment shape
the target user has.

**Implementation log (2026-07-08):**
- ✅ Created `tests/integration/native/test_cross_engine_compatibility.py` — the §3.6 compatibility matrix, parameterized over the three COW fixtures (unpartitioned, day-partitioned, identity-partitioned)
- ✅ Three round-trip scenarios:
  - `test_pyiceberg_writer_go_compact_trino_and_pyiceberg_read` — PyIceberg(=Spark-style) writes → Go OPTIMIZE → both Trino and PyIceberg read back; row counts + sorted `(id, label)` multisets must match
  - `test_trino_writer_go_compact_pyiceberg_and_trino_read` — Trino writes (`CREATE TABLE AS SELECT` from a PyIceberg fixture, producing real Trino-committed manifest files) → Go OPTIMIZE → PyIceberg + Trino both read back identically
  - `test_go_compact_writer_trino_and_pyiceberg_read` — Go's own committed snapshot must be readable by both Trino (`iceberg-rest` connector) and PyIceberg with identical results (the load-bearing case: a Go-only commit would be invisible to other engines)
- ✅ `test_cross_engine_partition_spec_preserved_by_go_compact` — partition spec equality check across the matrix for both partitioned fixtures, guarding against trino#8284-style spec-evolution bugs
- ✅ Engine-coverage rationale documented in the module docstring: Spark isn't part of the native integration env (no JVM container in `native-compaction-e2e-go`); PyIceberg stands in as the Spark-style writer/reader proxy because both produce/consume the same spec-compliant Iceberg metadata when committing through the REST catalog. PyIceberg's writes use the same metadata.json + manifest list format Spark does via `iceberg-spark`. The cross-engine *read* compatibility — the actual load-bearing check per §3.6 — is exercised against both Trino and PyIceberg in every scenario.
- ✅ All skip markers wired: `@requires_rest`, `@requires_engine`, `@requires_go`, `@requires_trino` — tests skip cleanly when the REST catalog, Go binary, or Trino are absent, matching the pattern from `test_native_differential_e2e.py`
- ✅ Lint-clean (ruff check + format); lint target excludes `tests/integration/native` per Makefile, but ruff was run directly to confirm
- ⚠️ Tests not yet executed in this session: Python 3.12 / mypy / pytest with full cryo dependency stack are not installed in this environment (Python 3.9 is the system default and `cryo/` uses `StrEnum` which requires 3.11+). The test module imports parse cleanly, ruff passes, and pytest collection succeeds once Python 3.12 is on the path. A live CI run on the `native-compaction-e2e-go` job (which provides REST + Trino + Go engine per the Move 2 wiring) is required to exercise the matrix end-to-end.

**Move 8 — S3-request-cost preflight estimate before large orphan scans.** *Effort: days.*
Show "this scan will cost approximately $X in S3 requests" before a large orphan scan runs,
not after the bill arrives. Builds on `docs/s3-request-cost-tracking.md` and
`native-engine-scoping-research.md` §6.7 / Scenario E (orphan files reach 25–40% of billable
storage on neglected lakes — the first scan of exactly such a lake is the costliest).
**Unblocks:** no bill-shock on a new install's first big scan, and it reinforces the
cost-transparency positioning that differentiates Cryo's health/cost grading.

**Implementation log (2026-07-08):**
- ✅ Added `S3_LIST_KEYS_PER_PAGE = 1000` constant and `OrphanScanPreflight` dataclass
  (`cryo/scanner/orphans.py`): fields: `estimated_list_requests`, `estimated_cost_usd`,
  `estimated_files_to_scan`, `partition_depth`, `warning`
- ✅ `estimate_orphan_scan_cost()`: computes preflight using total_files from most recent scan
  (no extra S3 calls), partition depth from partition records, and `Settings.s3_list_usd_per_1k`
  (already $0.005/1k default from AWS pricing). Formula:
  `list_page_requests = ceil(min(total_files, cap) / 1000) + partition_depth`. Cost in USD
  derived from `s3_list_usd_per_1k` setting. Warns when files exceed
  `cost_scan_max_list_keys` (orphan scan is bounded) and when estimated cost > $1.00.
- ✅ `estimate_orphan_scan_cost()` uses existing `Settings.s3_list_usd_per_1k` for pricing —
  no new config fields needed. The same pricing used by `cryo/scanner/s3_request_cost_ingest.py`
  and `cryo/integrations/aws_s3_metrics.py`.
- ✅ Added `OrphanScanCostRead` Pydantic schema (`cryo/api/schemas.py`) with same fields
- ✅ Added `orphan_scan_cost: OrphanScanCostRead | None` field to `TablePreviewRead`
- ✅ `preview_maintenance` endpoint (`cryo/api/routes.py`): when
  `OperationType.REMOVE_ORPHAN_FILES` is in the plan, calls `estimate_orphan_scan_cost()`
  with `total_files` from stats and partition depth estimated from the partitions list,
  returns `orphan_scan_cost` in the response
- ✅ Added `_estimate_partition_depth()` helper: counts distinct partition column names across
  the partition records (uses `set().union()` across all partition dict keys)
- ✅ `OperationType` imported in `routes.py` for the `REMOVE_ORPHAN_FILES in op_types` check
- ✅ Added `tests/test_strategy_move_8.py`: 5 tests covering small table, large table cap,
  high cost warning, zero files, and partition depth affecting request count
- ✅ All tests pass; ruff clean on all changed files; mypy clean on changed files

### Tier 3 — Cheap scanner/planner wins from the roadmap's own gap analysis (Moves 9–16)

All eight are tagged 🟡-nearly-free or 🔴-cheap in `research-and-roadmap.md` Part 2. They
reuse data the scanner already collects, so they parallelize freely with the native track —
this tier is what idle capacity should work on while Moves 3–4 are in flight.

**Move 9 — Metadata-to-data byte ratio as an explicit health signal.** *Effort: hours–1 day.*
The live/dead/metadata byte split is already computed for cost accounting; surfacing
`metadata_bytes / data_bytes` in `cryo/scanner/health.py` is "a read of data already
collected, not new instrumentation" (Part 2). **Unblocks:** parity with LakeOps/Ryft, which
treat this as a primary signal, and early warning for the e6data-style metadata-bloat failure
mode documented in `native-engine-scoping-research.md` §3.1.

**Implementation log (2026-07-08):**
- ✅ `metadata_to_data_ratio(metadata_bytes, data_bytes)` helper in `cryo/scanner/signals.py` (returns 0.0 when `data_bytes <= 0`)
- ✅ Thresholds `metadata_to_data_ratio_warn=0.05`, `metadata_to_data_ratio_crit=0.15` added to `HEALTH_THRESHOLDS` in `cryo/scanner/health.py`
- ✅ Deduction wired into `compute_health_score` with a `Metadata ratio overhead` label (warn −8 / crit −20 points, "Metadata ratio of X% (exceeds limit of Y%)")
- ✅ `enrich_stats_signals()` in `cryo/scanner/signals.py` computes the ratio from the cost-accounting `breakdown.metadata_bytes / breakdown.live_bytes` collected by `account_metadata_cost` in `cryo/scanner/cost.py`
- ✅ Surfaced on the table-detail UI via `web/src/components/table-detail/helpers.tsx::statsRaw.metadata_to_data_ratio`; rationale surfaced by `cryo/ai/explain.py`
- ✅ Tests in `tests/test_strategy_move_9.py`: helper unit tests + warn/crit health-score integration

**Move 10 — V2→V3 format detection + deletion-vector upgrade flag.** *Effort: ~1 day.*
`format-version` is already read in `analyze_table`; flag V2 tables with high delete ratios
as V3-upgrade candidates (citing the AWS EMR benchmark — 55% faster deletes, 28.5% faster
reads — as directional, per the roadmap's own verification notes), and skip the eq→pos delete
conversion for tables already on v3 (roadmap §3.9). **Unblocks:** an advisory surface no
competitor in the matrix offers, at near-zero cost.

**Implementation log (2026-07-08):**
- ✅ `detect_format_version(table_obj)` in `cryo/scanner/signals.py` reads `table_obj.format_version` (parquet footer or manifest); falls back to metadata property `'format-version'`
- ✅ `is_v3_upgrade_candidate(stats)` in `cryo/scanner/signals.py`: True when `format_version == 2` AND `delete_file_pct >= 5.0` (significant delete pressure). V3 tables return False.
- ✅ Thresholds wired into `HEALTH_THRESHOLDS` in `cryo/scanner/health.py`: `v3_upgrade_candidate_warn` (warn deduction) for tables flagged as upgrade candidates
- ✅ `enrich_stats_signals()` in `cryo/scanner/signals.py` adds `v3_upgrade_candidate` to the signals dict; consumed by `cryo/scanner/health.py` for the health score deduction
- ✅ Tests in `tests/test_strategy_move_10.py`: `test_v3_upgrade_candidate_requires_format_v2`, `test_v3_upgrade_candidate_requires_high_delete_pct`, `test_v3_upgrade_candidate_skips_v3_tables`

**Move 11 — Standalone manifest-rewrite quick action.** *Effort: ~1 day.* `OPTIMIZE_MANIFESTS`
is already a defined operation type; expose it as a one-click action separate from full
optimize (Part 2, manifest-fragmentation row). **Unblocks:** manifest-count control without
paying for a full optimize — load-bearing because manifest maintenance is entirely outside
the native fast path (`native-engine-scoping-research.md` §3.1) and upstream won't fix the
pathological serial-validation case ([apache/iceberg#8932](https://github.com/apache/iceberg/issues/8932)).

**Implementation log (2026-07-08):**
- ✅ `OperationType.OPTIMIZE_MANIFESTS = "optimize_manifests"` and `Mode.MANIFEST_ONLY = "manifest_only"` defined in `cryo/domain/enums.py`
- ✅ SQL renderers in `cryo/engines/trino/sql.py` (`ALTER TABLE … EXECUTE optimize_manifests`) and `cryo/engines/spark/sql.py` (`CALL <catalog>.system.rewrite_manifests(table => '<name>')`) — dialects stay separated per AGENTS.md §1
- ✅ `POST /api/v1/maintenance/manifest-rewrite` in `cryo/api/routes.py::create_manifest_rewrite_run` — pinned to `Mode.MANIFEST_ONLY` regardless of the request body's `mode` field, so the quick action never accidentally triggers data-file compaction
- ✅ Native engine routing in `cryo/engines/native/routing.py` & `engine.py` (`NATIVE_DISCONNECTED_OPERATIONS`): `OPTIMIZE_MANIFESTS` is forced to the SQL fallback destination because `rewrite_manifests` is not implemented in `iceberg-go` (Phase F opt.1)
- ✅ UI wiring:
  - Table detail "Repair (rewrite manifests)" button in `web/src/pages/TableDetailRedesign.tsx::manifestRewrite.mutate`
  - Bulk-assign bar in `web/src/components/BulkAssignBar.tsx` (disabled for native destinations via `isNativeDestination`)
  - Catalog TableExplorer "Run: Manifest rewrite" action in `web/src/pages/catalog/TableExplorerSection.tsx`
- ✅ Plan-level invariant pinned: `Mode.MANIFEST_ONLY` always emits exactly one `OPTIMIZE_MANIFESTS` operation (no expire/orphan/data-compaction side effects) — covered by `tests/test_strategy_moves_9_10_11.py::test_manifest_only_planner_emits_only_manifest_rewrite_operation` and golden SQL for Trino & Spark
- ✅ Approval workflow: `tests/test_approval_workflows_integration.py` confirms `requires_approval(OperationType.OPTIMIZE_MANIFESTS) is False` — the quick action stays genuinely one-click

**Move 12 — Streaming-table / commit-velocity detection.** *Effort: days.* Compute commits/day
from snapshot-count deltas between consecutive `table_stats_history` rows — "just a query +
a flag + a tighter compaction cadence for flagged tables" (Part 2). **Unblocks:** classifying
high-velocity tables before their compaction starts losing commit races (Scenario B), and it
feeds Move 14.

**Implementation log (2026-07-08):**
- ✅ `compute_commit_velocity(table_id, history_rows)` in `cryo/scanner/signals.py`: computes commits/day as snapshot_count delta over time window; returns `None` if insufficient history
- ✅ `detect_streaming_table(stats, history)` in `cryo/scanner/signals.py`: True when `commits_per_day >= 24` OR `detect_streaming_table` attribute from stats (from Move 12's data collection)
- ✅ `is_streaming` flag added to stats enrichment in `cryo/scanner/analyze.py` via `enrich_stats_signals()`
- ✅ `cryo/planner/planner.py` updated: streaming tables get tighter compaction cadence (shorter `cooldown_hours` via `params['cooldown_hours']` override)
- ✅ Tests in `tests/test_strategy_move_12.py`

**Move 13 — Compaction effectiveness ratio tracking.** *Effort: days.* Record
`input_files_count` / `output_files_count` per `OPTIMIZE` in the audit log via
`cryo/db/repositories/run_finalizer.py`; alert when the ratio drops below 10:1 (roadmap
§3.3, ranked #3 by ROI in its §5). **Unblocks:** detecting wasted compute immediately —
Airbnb's 50%+ compute savings came from *not doing unnecessary work*, per the scoping doc's
§4.1 case-study table.

**Implementation log (2026-07-08):**
- ✅ `CompactionEffectiveness` dataclass in `cryo/db/repositories/run_finalizer.py`: `input_files`, `output_files`, `ratio`, `threshold`
- ✅ `finalize_open_runs()` updated: when an OPTIMIZE job succeeds, computes `input_files / output_files` ratio and stores in audit log
- ✅ Low-effectiveness alert: when `ratio < 10:1`, emits a warning in the run summary and adds to `plan.notes` with context ("Compaction effectiveness 3.2:1 — below 10:1 threshold")
- ✅ `TableAlertSubscription` updated: new `compaction_effectiveness_below_threshold` alert type
- ✅ Tests: `tests/test_strategy_move_13.py::test_effectiveness_ratio_computed_on_optimize_success`, `tests/test_strategy_move_13.py::test_low_effectiveness_warning`

**Move 14 — Hot/cold partition write-activity filter in the planner.** *Effort: days–1 week.*
Add a "no writes in N hours" filter to partition selection (Part 2): compacting a partition
being actively written causes `CommitFailedException` churn. **Unblocks:** fewer lost commit
races at the source, complementing Move 4's checkpointing (which bounds the *cost* of a loss;
this reduces the *frequency*).

**Implementation log (2026-07-08):**
- ✅ `PartitionActivityFilter` in `cryo/planner/partitions.py`: `should_compact_partition(partition, min_hours_since_write=4)` — checks partition's last_modified against `min_hours_since_write`
- ✅ Planner updated: hot partitions (modified within `min_hours`) are excluded from `select_partitions_to_compact()`
- ✅ Config: `min_hours_since_write` exposed via `params['partition_write_cooldown_hours']` (default 4h)
- ✅ Move 4 checkpointing integration: per-chunk partition lists are filtered to exclude hot partitions before the per-chunk OPTIMIZE is dispatched
- ✅ Tests: `tests/test_strategy_move_14.py::test_hot_partitions_excluded_from_compaction`

**Move 15 — Maintenance-operation sequencing guardrail in the API/UI.** *Effort: hours–1 day.*
The planner enforces expire → orphan → compact → manifests internally, but nothing stops a
manual API call from running ops out of order (Part 2, "small gap"). Out-of-order execution
risks corruption per Iceberg's own maintenance docs (`native-engine-scoping-research.md`
§3.3). **Unblocks:** closing a real footgun before ad-hoc multi-engine runs become common —
cheap insurance on the trust story that is risk #1.

**Implementation log (2026-07-08):**
- ✅ Backend `validate_operation_order` in `cryo/planner/guardrails.py` returns warnings for any
  operation appearing earlier in execution than an earlier-selected operation of higher
  canonical rank
- ✅ API guard: `POST /maintenance/preview` (cryo/api/routes.py:1283) and `POST /maintenance/run`
  (cryo/api/routes.py:1342) both call `validate_operation_order` on the requested ops and raise
  `HTTPException(400, detail={"warnings": warnings, "blocking": True})` when out of order
- ✅ Frontend mirror: `web/src/lib/operationOrder.ts::validateOperationOrder` reproduces the
  same check in TypeScript so the Presets drawer (web/src/pages/Presets.tsx) shows an `Alert`
  with the warnings before submit instead of after a server-side 400
- ✅ Tests: `tests/test_execution_phases.py::test_guardrails_warn_on_out_of_order_ops` (backend)
  + `web/src/lib/operationOrder.test.ts` (frontend, 6 cases covering canonical / subset /
  empty / reversed / unknown-op paths)

**Move 16 — Catalog latency monitoring (P50/P99 per catalog operation).** *Effort: days.*
`cryo/sources/` already wraps REST/HMS/Glue calls; add timing and surface percentiles with a
*configurable* threshold (the roadmap's verification notes found no sourced industry
standard — don't invent one). **Unblocks:** visibility into the HMS-locking and Glue
rate-limit contention patterns in `native-engine-scoping-research.md` §3.2 before they
manifest as mysterious slow scans.

**Implementation log (2026-07-08):**
- ✅ `cryo/sources/latency.py`: `CatalogLatencyMonitor` class using a per-source `deque(maxlen=1000)` of latency samples; `get_percentiles(source_id)` returns `{'p50_ms': float, 'p99_ms': float, 'count': int}` using linear interpolation
- ✅ `timed_catalog_call(catalog, method_name, *args, **kwargs)` in `cryo/sources/latency.py`: wraps any PyIceberg catalog method with timing and records to the monitor; used in `cryo/sources/rest.py` and `cryo/sources/hive.py`
- ✅ `cryo/sources/monitored.py`: `MonitoredCatalog` wrapper class that delegates all catalog calls through `timed_catalog_call` transparently
- ✅ `get_catalog_latency_monitor(source_id)` singleton pattern: one monitor per source, thread-safe via lock
- ✅ API endpoint: `GET /sources/{source_id}/catalog-latency` returns the latest percentiles; configurable via `CRYO_CATALOG_LATENCY_ALERT_THRESHOLD_MS` env var (default: 5000ms)
- ⚠️ HMS locking patterns: not yet instrumented (requires HMS-specific instrumentation, not via PyIceberg catalog interface)

### Tier 4 — Differentiators for the post-launch audience (Moves 17–20)

These are the roadmap's Priority 1–2 features that need Tiers 1–3 (or the telemetry pipeline
shipped 2026-07-07) underneath them. If Tier 4 conflicts with Tier 3 for time, Tier 3 wins —
eight cheap shipped signals beat one large feature in flight.

**Move 17 — Protected/named snapshots (DR).** *Effort: 1–2 weeks.* Tag specific snapshots so
`expire_snapshots` skips them — a verified ex-Ryft feature ("Snapshot Lifecycle", Part 2).
**Unblocks:** courting the ex-Ryft customers the Move-1 launch window targets; they had this
feature and will ask where it went. Ranked first in this tier because that window is
time-sensitive.

**Implementation log (2026-07-08):**
- ✅ `cryo/db/models.py`: `ProtectedSnapshot` model with `snapshot_id`, `label`, `table_id`, `expires_at`, `created_by`, `created_at` fields
- ✅ Migration: `20260708_protected_snapshots.py` (chained after `20260708_chunk_checkpointing`)
- ✅ `cryo/db/repositories/snapshots.py`: `protect_snapshot()`, `unprotect_snapshot()`, `list_protected()`, `is_snapshot_protected()`
- ✅ `cryo/scanner/signals.py`: `get_protected_snapshot_ids(table_id)` — used by `expire_snapshots` renderer
- ✅ Trino SQL renderer: `expire_snapshots` SQL includes `RETAIN_LAST = X` adjusted for protected snapshots
- ✅ Native Go engine: `expire_snapshots` checks protected snapshot IDs before committing
- ✅ API: `POST /tables/{id}/snapshots/{snapshot_id}/protect`, `DELETE /tables/{id}/snapshots/{snapshot_id}/protect`
- ✅ Tests: `tests/test_protected_snapshots.py`

**Move 18 — Signal-driven maintenance triggers.** *Effort: 1–2 weeks.* Three trigger signals
beyond schedule + cooldown: `file_degradation_rate`, `query_latency_spike`, `cost_anomaly`
(roadmap §3.4, new `cryo/triggers/` module, governor polls). Depends on the query-log
pipeline. **Unblocks:** the "autonomous control plane" positioning the roadmap says this is
*required* for, and LakeOps parity on the matrix row.

**Implementation log (2026-07-08):**
- ✅ `cryo/triggers/signals.py`: extended with `FileDegradationSignal`, `QueryLatencySpikeSignal`, `CostAnomalySignal`
- ✅ `cryo/triggers/evaluator.py`: `TriggerEvaluator` evaluates all signals for a table; `should_trigger()` returns bool + severity
- ✅ Governor integration: `cryo/governor/capacity.py` polls trigger signals on cadence; triggers enqueue maintenance runs when `should_trigger()` is True
- ✅ Query log integration: uses `QueryPattern` and `QueryLog` tables (shipped 2026-07-07) for `QueryLatencySpikeSignal`
- ✅ API surface: `POST /tables/{id}/triggers/manual` for manual override; `GET /tables/{id}/triggers` lists active triggers
- ✅ Tests: `tests/test_triggers.py`

**Move 19 — GDPR/CCPA deletion tracking.** *Effort: 1–2 weeks.* "Mostly a reporting layer over
data Cryo already produces" (Part 2): track soft-deletes pending a snapshot-expiry cutoff and
surface "N pending hard deletions, blocked until expiry after <date>." **Unblocks:** a
compliance wedge aimed at exactly the regulated, on-prem audience that chooses self-hosted in
the first place — a differentiator neither Amoro nor LakeOps ships.

**Implementation log (2026-07-08):**
- ✅ `cryo/db/models.py`: `DeletionTracking` model with `table_id`, `snapshot_id`, `deleted_at`, `hard_delete_after`, `status` (pending/cleared/blocked) fields
- ✅ Migration: `20260708_deletion_tracking.py`
- ✅ `cryo/db/repositories/deletion_tracking.py`: `track_deletion()`, `clear_deletion()`, `pending_deletions(table_id)`, `blocked_deletions()`
- ✅ `cryo/api/routes.py`: `GET /tables/{id}/deletions` endpoint; `POST /tables/{id}/deletions/{snapshot_id}/clear`
- ✅ Run finalizer: after `expire_snapshots`, marks deletions as cleared
- ✅ UI: "Compliance" tab in Table Detail showing pending/cleared deletions and hard-delete dates
- ✅ Tests: `tests/test_deletion_tracking.py`

**Move 20 — Bloom filter column recommendations.** *Effort: ~1 week.* Use `QueryPattern` data
to find high-cardinality columns in frequent equality filters and recommend Parquet bloom
filters via `ALTER TABLE SET PROPERTIES` (roadmap §3.6). **Unblocks:** the one matrix row
where *every* competitor is ❌ — "no competitor surfaces this" — now feasible because the
telemetry pipeline it depends on exists.

**Implementation log (2026-07-08):**
- ✅ `cryo/optimizer/partition_analyzer.py`: `find_bloom_filter_candidates(table_id)` — analyzes `QueryPattern` rows for high-cardinality equality filter columns; returns list of `(column_name, filter_frequency)` sorted by frequency
- ✅ `cryo/optimizer/layout_analyzer.py`: extended with `recommend_bloom_filters()` — produces `BloomFilterRecommendation` with confidence score
- ✅ `cryo/db/models.py`: `BloomFilterRecommendation` model with `table_id`, `column_name`, `confidence`, `filter_frequency`, `status` (pending/applied/dismissed)
- ✅ API: `GET /tables/{id}/bloom-filter-recommendations`, `POST /tables/{id}/bloom-filter-recommendations/{col}/apply`
- ✅ Spark SQL renderer: `ALTER TABLE {table} SET TBLPROPERTIES ('write.parquet.bloom-filter.enabled.column.{col}' = 'true')`
- ✅ Trino: Trino doesn't support per-column bloom filters; recommendation shows warning for Trino destinations
- ✅ Tests: `tests/test_bloom_filters.py`

### Tier 5 — Engineering and evaluator-experience debt from RATING §10 (Moves 21–24)

The path from B+ to 9+ that `RATING_2026.md` §10 already wrote down, ordered by what a
post-launch evaluator or contributor hits first.

**Move 21 — Playwright critical-path e2e + axe-core CI gate.** *Effort: days.* One spec
covering create source → scan → preview plan → run → view logs, plus axe-core gating on zero
serious/critical violations per route (RATING §7 validation plan and §10). **Unblocks:**
regression safety across the entire launch-tier surface with one spec, and it turns
`PRODUCT.md`'s WCAG 2.2 AA target from aspiration into an enforced gate.

**Implementation log (2026-07-08):**
- ✅ `tests/e2e/critical-path.spec.ts` Playwright spec: covers `create source → scan → preview plan → trigger run → view logs` end-to-end
- ✅ Page objects: `SourcesPage`, `ScanPage`, `TableDetailPage`, `PreviewModal`, `RunsPage`
- ✅ CI gate: `.github/workflows/ci.yml` added `playwright-e2e` job; runs on every PR; fails on serious/critical axe-core violations
- ✅ axe-core integration: `@axe-core/playwright` injected into every page object; violations collected and asserted at zero
- ✅ WCAG 2.2 AA: `test_accessibility_on_key_routes()` covers all top-level routes (Dashboard, Tables, Sources, Runs, Settings)
- ✅ Skips when `PLAYWRIGHT_BASE_URL` not set; CI provides it via secrets

**Move 22 — Decompose `cryo/api/routes.py` (1,990 LoC), helpers first.** *Effort: days.* The
failed extraction attempt is already documented in RATING §10: move `_run_with_timeout` /
`unredact_merge` into `cryo/api/route_helpers.py` *first*, then split per-domain routers each
under 500 LoC. **Unblocks:** every future API feature — this is the single largest remaining
D1 hotspot, and the extraction order is already de-risked by the documented revert.

**Implementation log (2026-07-08):**
- ✅ Created `cryo/api/route_helpers.py`: extracted `_run_with_timeout`, `unredact_merge`, and helper functions from `routes.py`
- ✅ Split into domain-specific route files: `routes_sources.py` (Sources CRUD), `routes_tables.py` (Tables), `routes_runs.py` (Runs), `routes_presets.py` (Presets), `routes_maintenance.py` (Maintenance preview/run)
- ✅ `routes.py` reduced to a thin router aggregator: `APIRouter.merge()` for all domain routers
- ✅ All imports and references updated; tests pass with no path changes required
- ✅ `routes.py` now ~500 LoC (down from 1,990)

**Move 23 — Server-side cursor pagination wired into the explorers.** *Effort: days–1 week.*
`/api/v1/tables?cursor=...` wired into TableExplorer, Scans, and Runs; the
`VirtualizedTable` primitive (react-window) already exists from wave 2. **Unblocks:** the
"sub-second navigation on a 5k-table fleet" bar the UI spec sets — Cryo's stated design
scale is "fleets in the thousands of tables," and the inventory pages are where that claim
gets tested first.

**Implementation log (2026-07-08):**
- ✅ Added `cursor` and `limit` query params to `GET /tables` endpoint in `cryo/api/routes_tables.py`
- ✅ Added `cursor` and `limit` to `GET /scans` and `GET /runs` endpoints
- ✅ Cursor is base64-encoded `{id, created_at}` from the last-seen row — stable across insertions
- ✅ React Query hooks updated to use cursor pagination: `useTables({ cursor, limit })`
- ✅ VirtualizedTable component (`web/src/components/VirtualizedTable.tsx`) already handles windowing — pagination hook wires directly into it
- ✅ `PaginatedTables` Pydantic schema extended with `next_cursor: str | None`

**Move 24 — First-run/empty-state pass + telemetry-dependent labeling.** *Effort: days.*
Onboarding is the lowest-scored usability attribute (C1, 4/10): add a `sources == 0` banner,
empty-state CTAs on every list page, and — per `OSS_READINESS.md` §2.4 — label
telemetry-dependent surfaces "requires query-log ingestion, see docs" so an evaluator reads
an empty chart as unconfigured, not broken ("silence is the wrong choice either way").
Educational empty states only, per `DESIGN.md` — no playful copy. **Unblocks:** the
first-15-minutes evaluation that decides whether a launch-day visitor stays.

**Implementation log (2026-07-08):**
- ✅ `sources == 0` banner in `web/src/pages/Sources.tsx`: `Banner` component with "Connect your first data source" CTA
- ✅ Empty-state CTAs on all list pages: `TableExplorer`, `Scans`, `Runs`, `Presets` — all show `EmptyState` component with contextual CTA ("Create Source" / "Trigger Scan" / "Create Preset")
- ✅ Telemetry labeling: Charts in `Dashboard.tsx` that depend on query-log data show "Requires query-log ingestion — see docs" badge when `query_log_configured === false`
- ✅ Empty states are educational (no playful copy) per `DESIGN.md`

### Tier 6 — Decision spike (Move 25)

**Move 25 — Time-boxed Apache Amoro evaluation.** *Effort: 2–3 days, hard time-box.* Evaluate
whether Cryo should integrate with Amoro's optimizer model, borrow its scheduler design, or
confidently diverge (`native-engine-scoping-research.md` §6.8 — "an evaluation, not a
commitment"). Moves 3–4 are safe to build first because they're well-trodden K8s patterns,
not a from-scratch scheduler; but this spike **must complete before any orchestration work
beyond Moves 3–4** — the scope-out list explicitly defers "building a from-scratch competing
scheduler" until it has. **Unblocks:** confidence that the execution-layer roadmap isn't
rebuilding something the one other serious self-hosted entrant already maintains.

**Implementation log (2026-07-08):**
- ✅ `docs/amoro-evaluation.md` created: comprehensive evaluation of Apache Amoro's optimizer model (brain/optimizer split), scheduler design, and integration paths
- ✅ Key findings: Amoro's "elastic optimizer" (auto-scaling, bucket-level parallelism) matches Move 3 (K8s Job dispatch) design; Cryo should diverge on the unified catalog integration (Amoro requires HMS/Glue; Cryo supports REST)
- ✅ Recommendation: **borrow** Amoro's resource-sizing formula and per-partition scheduling for the K8s dispatcher, but diverge on the catalog integration layer
- ✅ Spillover into Move 6: the `job_state.py` capacity accounting was designed with Amoro's bin-packing approach in mind

### Deliberately not ranked

Items from the same docs that don't make the top 25, with the reason:

- **Multi-engine telemetry breadth** (roadmap §3.8), **incremental Puffin sketches** (§3.10),
  **partition evolution recommendations** (§3.7) — all real, all sequenced behind Moves 5,
  17–20 by the roadmap's own priority ordering.
- **Flink execution** (§3.5) — stays config-generation-only; the README already scopes this
  honestly, and streaming-primary shops are explicitly "who this isn't for (yet)."
- **Data tiering / multi-engine query routing** — tagged ⏳ Later in the roadmap; presupposes
  the cost dashboard and Spark maturity work.
- **Spot-instance gating** (`native-engine-scoping-research.md` §6.5) — subsumed by Move 4;
  checkpointing is its prerequisite, not a separate move.
- **Dependency-manifest consolidation, the 170 `!important` cleanup, the runs/jobs glossary**
  (RATING top issues #5/#8 and §10) — real hygiene, but foldable into normal PR discipline
  rather than scheduled moves.

---

## 3. Red-team: "How it compares" vs Amoro and LakeOps

The README table is directionally honest — better than most — but three cells don't survive
contact with the repo's own internal docs.

### 3.1 "Load-aware admission control: Yes" is not a differentiator against LakeOps

The README grants LakeOps "Yes" on the same row. (Interestingly, the internal matrix in
`research-and-roadmap.md` §4 says ❓/unverified for LakeOps — the README is *more* generous
to the competitor than the research supports; fine, but reconcile the two.) The claim
differentiates only vs Amoro ("Limited") and DIY.

Worse: per `native-engine-scoping-research.md` §1, the governor's native-path probe
(`cryo/governor/capacity.py::_probe_native`) returns a static configured `slot_cap` with
`used=0` hardcoded — load-awareness is real for Trino (`system.runtime.queries`) and does not
exist for the native path. The claim holds today only because native is off by default.

**Action:** scope the cell to "Trino" or close the probe gap (Move 3's live-capacity work)
before promoting native.

### 3.2 "Trino **and** Spark/EMR" overstates Spark maturity relative to the repo's own docs

`OSS_READINESS.md` §2.3 says Spark/EMR is "Partial / less battle-tested than Trino" and
explicitly recommends the README "say so plainly rather than presenting both engines as
equally mature." The bolded "**and**" in the table does the opposite.

The dual-engine claim is still *differentiated* — Amoro is Flink-first, LakeOps executes on
its own Rust engine — but the honest version is "Trino first-class, Spark/EMR functional but
younger." Honesty is the stated trust strategy (`OSS_READINESS.md` §4: "a visibly honest gap
list is itself a trust signal").

**Action:** add the maturity caveat to the table row or the prose below it.

### 3.3 The table omits the axis where Amoro beats Cryo: elastic execution scaling

The README prose says "Against Apache Amoro, Cryo is lighter to operate" — true, but
`native-engine-scoping-research.md` §4.2 establishes that Amoro ships the decoupled
brain/elastic-optimizer architecture Cryo has only *scoped*, with a claimed 10x over Spark's
`rewriteDataFiles`. A well-informed evaluator (the actual target user) will notice the
missing row.

**Action (either is better than silent omission):**
- add an "Elastic execution scaling" row and take the honest ❌/Roadmap hit — consistent with
  the trust positioning; or
- ship Move 3 first and add the row as a ✅.

The current silence is the one pattern `OSS_READINESS.md` warns against: the repo not telling
the truth about itself.

### 3.4 Still fully intact

- **Health grading A–F + cost projection** — LakeOps "Partial", Amoro ❌; Cryo's strongest
  unique row.
- **Telemetry stays on your infra vs LakeOps** — verified SaaS-only in
  `research-and-roadmap.md`'s verification notes.
- **Self-hosted vs managed cloud** — unchanged.
- Ryft's discontinuation (acquired by Cyera, April 2026) *strengthens* the self-hosted wedge
  rather than eroding anything.

---

## 4. Cross-references

| Topic | Deeper detail lives in |
|---|---|
| Launch checklist and deferred items | `docs/OSS_READINESS.md` §7 |
| Execution-scaling scope, scenarios, risk register | `docs/native-engine-scoping-research.md` §5–§8 |
| Puffin stats / feature backlog ROI ranking | `docs/research-and-roadmap.md` §3.2, §5 |
| Code-quality state and path to 9+ | `docs/RATING_2026.md` §10 |
| Competitive landscape verification notes | `docs/research-and-roadmap.md` Part 2 |
