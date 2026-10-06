# Cryo cost reduction: diagnosis and implementation plan

**September 20 recovery priority:** [Permanent recovery and metric improvement plan](CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md)
specifies the scheduler memory architecture, run lifecycle invariants and metric evidence required
after the latest OOM/legacy-run incident. Use it for immediate sequencing; retain this broader backlog.

**September 16 update:** [Unused-cost incident diagnosis and fix plan](CRYO_UNUSED_COST_FIX_2026-09-16.md)
contains fresh billing attribution, live resource/timeout evidence, a tested local CPU sizing
correction, and the ordered plan for dependency containment, batch sizing, and autoscaling.

**Updated: 2026-09-11. Code reference: `0562524f` plus the working-tree changes described below.**

This is the consolidated answer to: **what must change so Cryo reliably lowers S3 and Trino costs, including its own operating cost?** It sets the rescue work order ahead of feature expansion. Existing architecture, retention, concurrency, and data-safety contracts still apply. Earlier proposals in `cryo_fix.md` and `.cursor/plans/` are historical inputs, not proof that their diagnoses or fixes remain current.

## 1. Decision

Cryo needs a reliable maintenance execution and measurement loop before more features. The immediate priorities are:

1. Stop repeated crashes and duplicated background work; right-size actual dispatch requests.
2. Finish every required maintenance operation safely, including native-to-Spark continuations.
3. Reclaim physical S3 objects safely and distinguish that from metadata changes.
4. Spend compaction resources only on eligible partitions with sufficient expected value.
5. Use observed outcomes to schedule the next work and adjust ingestion/layout where maintenance cannot solve fragmentation.
6. Demonstrate net savings with billing, matched query evidence, and an explicit Trino capacity adjustment.

The objective is **positive net realized savings subject to correctness and query SLOs**, not maximum operations, files rewritten, or a higher health score. There is no defensible promise of a drastic percentage reduction yet: live data, required history, ingestion volume, and query demand set a cost floor.

## 2. What the evidence currently establishes

### Billing baseline

The verified 2026-09-10 CUR/Grafana pull covers August 1–September 9. Values were recorded in [the daily log](cost-tracking/daily-cost-log.csv) before reporting. Compare the same nine calendar days; billing can backfill.

| Namespace | Aug 1–9 mean/day | Sep 1–9 mean/day | Change | Sep unused share |
|---|---:|---:|---:|---:|
| Cryo | $177.69 | $132.45 | −25.5% | 83.15% |
| Trino | $565.01 | $544.23 | −3.7% | 44.07% |

Neither change passes the current comparison script's significance check. These are observations, not causal savings attributable to Cryo. Unused shares are ratios of summed unused dollars to summed total dollars, not averages of daily percentages. September 9 alone was $165.09 for Cryo, including $142.74 unused, and $572.74 for Trino. Older rescue notes contain different totals; use the logged pull above and retain collection timestamps when reconciling later revisions.

The cost API reported approximately 109.8 TB current storage, 26.7 TB modeled reclaimable storage, and $572.78 projected monthly savings at the September 10 capture. Those are **application estimates for the covered tables**, not warehouse-wide verified S3 savings. Metadata coverage was 11,502 tables versus storage-truth coverage of 1,246; orphan evidence was marked stale. Source cost-insight daily results were empty. Billing coverage is therefore a release dependency, not a finished feature.

### Operational evidence and current status

| Finding | Evidence / status | What remains |
|---|---|---|
| Inline outcome-model refresh caused worker/API memory failures | Reproduced in the earlier incident; refresh moved to a scheduled runner. Outcome reads subsequently bounded in `777f6bbf`. | Verify scheduler and API memory through full refresh cycles; moving work does not eliminate its cost. |
| Scheduler instability | OOMKilled observed September 10; later pod had nine restarts, without a captured termination reason. | Attribute each heavy runner's memory/time separately. Do not label every later restart an OOM. |
| Per-table benefit sweep repeatedly builds outcome datasets | Confirmed code path; working-tree patch skips realized-value refresh in the scheduled score sweep. | **Incomplete:** replace the lost per-table feedback with a bounded updater before treating this as a complete fix. |
| Worker executes despite another job's table lock | Entry check and atomic Redis renewal fixed in `777f6bbf`. | Test lock loss during long execution, recovery ownership, and fencing across engines. |
| Workers scaled for native watcher-managed jobs | KEDA metric corrected in `777f6bbf`. | Verify deployed metric and observed replica behavior under mixed jobs. |
| Native admission can restore excluded operations; fallback misses table sizing | Fixed in `777f6bbf`. | Add operation-aware sizing; table size alone overestimates metadata-only work. |
| Thinly spread eligible partitions blocked by table-level thresholds | Opt-in `min_partition_small_files` exists in `299e87bd`. | Validate effective presets and actual partition distributions; it cannot merge across partitions. |
| Cooldown triggered by unrelated successful operations | Reclaim-action scoping changed in `ca4f6eac`. | Move to operation-specific completion/effect clocks, preserving pending debt. |
| Trino query reference captured too late for recovery | Capture timing changed in `a497bd22`. | Kill/restart integration test and production reconciliation evidence. |
| Native expiry does not physically delete files | `engine-go/internal/ops/expire.go` uses `WithPostCommit(false)`. | Safe, observable physical reclamation and cleanup of already orphaned objects. |
| Fallback operations can be indefinitely deferred | Native request construction defers snapshot-mutating fallback when native mutations are present. Repeated native plans can repeat this deferral. | Durable sequential continuation; quantify affected production tables. |

The September 10 vitals capture reported roughly 12% job success over 24 hours and 1,931 of 12,872 tables without scans in seven days. Recheck after deployment and distinguish actual engine failures from cancellations, superseded jobs, and bookkeeping failures. These historical values are not a statement of current fleet health.

**Validation boundary:** targeted tests passed for the earlier fixes. The full suite still had failures and lint/typecheck debt also present in baseline checks. The current score-sweep shortcut lacks a regression test proving its replacement feedback behavior. No claim here means all checks pass, every change is deployed, or savings are proven.

## 3. How maintenance turns into money

| Mechanism | Necessary change | Evidence that counts |
|---|---|---|
| S3 storage | Delete objects no longer referenced by any protected table state; handle retained versions separately. | Delete receipts/errors, object/version inventory change, then billed storage change with ingestion adjustment. |
| S3 GET/HEAD/LIST | Reduce files actually touched by queries; avoid wasteful rescans and cleanup listings. | Workload-normalized request counts and billed requests, including maintenance overhead. |
| S3 PUT and rewrite cost | Avoid low-value/repeated rewrites; improve writer output. | Requests/bytes written per maintained TB and payback against subsequent reads/storage. |
| Trino query CPU | Better file sizing, pruning/layout, delete handling, and useful query statistics. | Matched query CPU, physical input, requests, latency, and concurrency at comparable workload. |
| Trino infrastructure dollars | Convert reduced demand into lower requests/replicas/node hours while meeting SLOs. | Actual capacity and billing reduction; CPU falling alone is insufficient. |
| Cryo overhead | Less redundant work, bounded control-plane reads, resource requests matching operation demand. | Fully loaded cost per useful completed operation/TB, unused share, and failure/retry cost. |

Compaction generally preserves live logical data. It may temporarily **increase** physical storage because old files remain referenced by retained snapshots. Expiration can remove snapshot references without deleting objects in Cryo's native implementation. Once those references are gone, a later Spark expiry cannot be assumed to rediscover the already orphaned files.

Trino optimization works within partitions. Thousands of partitions with one small file each cannot be consolidated into a few large files under the unchanged partition specification. Manifest optimization can help planning, but does not merge data files. Validate options against the deployed Trino version. [Trino Iceberg connector](https://trino.io/docs/current/connector/iceberg.html).

Snapshot expiry must preserve retained snapshots and protected references. Orphan cleanup must account for concurrent writers and sufficiently old unreferenced files. Use supported engine semantics and explicit ownership checks. [Iceberg maintenance](https://iceberg.apache.org/docs/latest/maintenance/), [Spark maintenance procedures](https://iceberg.apache.org/docs/latest/spark-procedures/).

For versioned buckets, deleting an object can leave a delete marker and billable noncurrent versions. Inspect versioning, lifecycle, Object Lock, and replication before equating delete acknowledgments with reclaimed billed bytes. Never apply an age-based lifecycle deletion rule to active Iceberg data. [S3 versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html).

## 4. Implementation sequence

### P0 — Stabilize execution and bound Cryo's cost

**Primary areas:** `cryo/cli.py`, `cryo/scheduler/`, `cryo/planner/benefit_score.py`, `cryo/intelligence/outcomes.py`, `cryo/db/repositories/job_store.py`, `cryo/engines/spark/`, Helm requests/autoscaling.

- Replace fleet-wide ORM materialization with narrow, keyset-paged IDs and bounded per-batch histories. Avoid loading large plan/raw JSON on queue/list polling. Batch the job-list N+1 lookups.
- Give expensive background runners durable next-run state, single-owner leases, bounded concurrency, and actual process memory/time limits. A timed-out thread does not guarantee work stopped. Prevent every restart from triggering all full-fleet analyses again.
- Finish score/feedback separation: incrementally update per-table `autocomp_realized_value` from completed outcomes with persisted cursors, bounded batches, freshness timestamps, and restart-safe progress. Test that score-only updates preserve existing feedback and that the independent updater advances it.
- Treat global cost-model training separately. `CostModelRefreshRunner` does **not** replace per-table feedback. Audit its local cache distribution: publish a versioned shared artifact or database record and verify every consuming process reads the same model version.
- Size Spark/native jobs by operation and chunk work, not just whole-table bytes. Metadata operations and data rewrites need different profiles. Use observed peak RSS and CPU, bounded floors/ceilings, conservative handling of missing stats, and explicit override precedence.
- Attribute the empty workload-name CUR bucket to ephemeral Jobs/Pods; named deployments alone miss dispatch waste. Right-size requests gradually and confirm that released capacity results in node/capacity reductions.

**Exit gate:** 48 hours including the heavy scheduled cycles without OOM or duplicate sweeps; bounded RSS and backlog; injected restarts preserve work and feedback progress. Provisional waste targets are below 50%, then below 30%, subject to memory headroom and SLOs. These are engineering targets, not predicted savings. Do not increase all worker memory or replicas as the permanent fix.

### P1 — Make maintenance completion durable and truthful

**Primary areas:** `cryo/engines/native/`, `cryo/governor/native_admit.py`, `cryo/worker/`, `cryo/queue/locks.py`, intent/effect persistence and migrations.

- Persist the required ordered operations and their individual states: planned, executing, completed, no-op, deferred, failed. Record engine reference, snapshot boundary, attempt, and effect evidence.
- Resume native-to-Spark work from the first unfinished operation. Preserve the table lease and ordering across engines. Never allow native cleanup and Spark rewrite to mutate a table concurrently; removing the existing concurrency guard would reintroduce a historical corruption risk.
- Use job-specific ownership and loss-of-lease handling throughout execution, including recovery. Test crash after external submission, crash after engine completion before DB acknowledgment, lost lease, and duplicate delivery.
- Use per-operation cooldowns. A property update, no-op, or metadata-only result must not mask unfinished compaction/reclamation. Coalesce duplicate outstanding table/action work while retaining fairness and bounded aging.
- Show the effective plan before dispatch: engine capability, mode exclusions, preset exclusions, retention checks, and fallback requirements. Unsupported work must not disappear into a green aggregate status.

**Exit gate:** native-plus-fallback canary completes all requested effects through injected interruptions, with no concurrent table mutations and no lost/duplicated work. Queues converge; completion counts reconcile with engine references and operation states.

### P2 — Close the physical S3 reclamation gap

**Primary areas:** native expiry/orphan operations, engine renderers, maintenance receipts, scanner storage truth, cost reporting.

- Inventory each managed bucket/prefix, table location, shared location, protected branch/tag, retention policy, versioning, and legal retention setting. Publish coverage and freshness.
- Choose a supported physical deletion path for newly expired data. Do not simply enable native post-commit deletion fleet-wide: prove reference and concurrent-writer safety first.
- Build safe discovery for files already orphaned by metadata-only expiry. Evaluate S3 Inventory versus bounded listings by availability, staleness, request cost, and scale. Confirm candidates against sufficiently current catalog references before deletion, with age/ownership guards and a dry-run receipt.
- Record candidate bytes, protected bytes, deletion acknowledgments, failed deletes, and outstanding version bytes separately. Failed deletion must remain retryable maintenance debt.
- Validate delete-file handling by actual affected partition/chunk coverage. Suppress a separate conversion only when the scheduled rewrite provides the equivalent effect; operation presence alone is insufficient.

**Exit gate:** fixture tests cover branches/tags, concurrent writers, shared locations, partial delete failures, and versioned buckets. A bounded production canary shows reduced obsolete physical bytes without missing referenced files. Storage billing confirmation follows its reporting lag. Never delete backup/temp tables or S3 prefixes merely because of their names.

### P3 — Make compaction selective and stop creating the same debt

**Primary areas:** planner, partition statistics, workload signals, effective presets, governor admission, ingestion guidance.

- Measure eligible small-file count and bytes per partition, distribution coverage, delete burden, query heat, and writer activity. Preserve precomputed scanner inputs, GB budgets, partition caps, oversize isolation, and load-aware admission.
- Apply `min_partition_small_files` only to genuinely mergeable partitions. For singleton-dominated tables, investigate writer batching/target files and partition evolution rather than repeatedly lowering a threshold.
- Give chunks durable progress so a maximum-chunk limit does not repeatedly select the same leading partitions. Verify no-op completion changes eligibility only when fresh evidence supports it.
- Estimate operation cost including job startup, rewrite I/O, retained duplicate storage, and expected read benefit. Apply bounded admission budgets with fairness, aging, and overdue safety maintenance. Unknown workload signals must not be interpreted as zero value or free work.
- Correct ingestion-rate inference: maintenance since the previous scan is not evidence that writers are inactive. Distinguish writer commits from maintenance commits and retain an explicit unknown state.
- Evaluate layout/PTO using actual query predicates and scan reduction. Use opt-in canaries and account for the full rewrite payback. Validate Trino statistics where useful and supported.
- Make scanner cadence adaptive to table change and evidence age; avoid paying for repeated unchanged manifest scans. Account for scanner requests and log ingestion in savings.

**Exit gate:** representative hot and cold cohorts show fewer repeated no-ops and lower cost per useful effect. Query gains exceed maintenance overhead over the chosen payback window, with no latency regression. Continue AutoComp before broader PTO, then secondary workload-aware features, as specified in the existing project roadmap.

### P4 — Prove savings and turn lower Trino demand into lower bills

**Primary areas:** cost ingestion, outcome lineage, reporting/dashboard, deployment capacity policy.

- Map table locations to the exact billed buckets/prefixes and publish unmatched spend. Reconcile physical storage truth with cost coverage; do not compare a subset of tables against unrelated warehouse totals.
- Match query families, data volume, partition filters, cache state, concurrency, and ingestion rate before comparing CPU, bytes read, requests, and p95 latency. Include an untreated control cohort where practical.
- Keep daily namespace CUR logging and add attributable S3 storage/request costs plus maintenance overhead. Include Spark/native jobs, database, scanner, logs, and Athena costs where attributable.
- Reduce Trino capacity in controlled steps once demand/headroom supports it; verify query SLOs and actual node-hour savings. A shift from used to unused allocation is not a savings outcome.
- Report estimated benefit, observed physical/query effect, billed change, and net attributable savings as separate fields, with timestamps, coverage, and confidence.

**Exit gate:** enough complete billing days and comparable workloads to support the claim. Use at least the existing seven-day minimum for preliminary comparisons; prefer a 28-day window covering weekly patterns. The current Welch check is preliminary evidence, not causal attribution. Publish negative or inconclusive results as such.

## 5. Canary and rollout order

1. Reproduce correctness and restart behavior on disposable fixture tables, including versioned storage and delete files.
2. Select a small native/Spark mixed cohort plus matched controls. Include a metadata-only case, mergeable small-file partitions, a delete-heavy table, and singleton-heavy partitions.
3. Inspect one known large table as a diagnostic target because of prior recovery problems. Verify current schema/name and partition evidence for other proposed large-table targets before enrolling them. Large problem tables should not be the first destructive canaries.
4. Record the effective presets and assignment precedence. Existing native-deep, Spark-medium/large, cold-fleet, and dedicated-table schedules can overlap; measure actual ownership before tuning thresholds or frequency.
5. Roll out P0 independently, then operation correctness, then bounded reclamation and compaction cohorts. Expand only when each gate passes. Pause new dispatch on correctness/SLO regressions, reconcile already running work, and preserve audit receipts.

Retention changes require their own workload/recovery evidence. Do not bundle shorter retention with the first cost canary: that would obscure attribution and change recovery guarantees.

## 6. Success accounting

Use a defined counterfactual and scope:

`net savings = comparable baseline total cost − observed total cost`

Both totals must include S3, Trino, and attributable Cryo/maintenance overhead. Include preexisting maintenance in the baseline if applicable. Avoid double-counting maintenance compute or requests already included in those totals. Separate total savings since Cryo adoption from incremental savings caused by this rescue release.

The operating dashboard should show:

- Physical obsolete bytes removed and retained-version bytes still billed.
- S3 requests and query CPU per comparable workload unit.
- Useful completed effects, no-ops, failures, retries, and deferred debt age.
- Cryo used/unused cost, dispatch resource utilization, and cost per useful effect.
- Gross modeled benefit, measured effect, billed change, and fully loaded net savings.

Keep the existing health grade for continuity; do not alter parity scoring to make results appear better. “GB freed” remains an operational signal, but must carry its measurement basis and must not be presented as billed savings.

## 7. Competitive direction and scope

Amoro documents minor/major/full optimization, resource management, and table scheduling. Cryo should learn from that separation of maintenance needs and bounded resources, then prove comparable execution reliability. [Amoro self-optimizing](https://amoro.apache.org/docs/latest/self-optimizing/).

Cryo's potential differentiation is **auditable net savings across native, Spark, and Trino**, with safe orchestration, workload-aware choices, and low self-hosting overhead. That is a product direction, not a demonstrated competitive advantage today. “Lakehouse” does not identify a specific competing product/version; a precise comparison needs that identification.

Prioritize durable operation continuation, physical reclamation receipts, operation-aware resources, actionable cost attribution, and writer/layout recommendations. Defer new engines, general AI features, and dashboard expansion unless they directly unblock these gates. A successful release is one where the measured customer cost falls and the data remains correct—not one with more feature checkboxes.

## 8. Next implementation checklist

- [ ] Complete bounded per-table feedback replacement and shared model distribution; test freshness and restarts.
- [ ] Attribute scheduler memory per runner; bound execution and prevent restart-triggered repeated sweeps.
- [ ] Verify shipped worker/KEDA/query-reference fixes in mixed-engine production behavior.
- [ ] Implement durable sequential fallback and operation-specific effect/cooldown accounting.
- [ ] Validate physical expiry/orphan reclamation including protected references and object versions.
- [ ] Add operation-aware dispatch sizing and record utilization for ephemeral jobs.
- [ ] Tune mergeable-partition cohorts; route irreducible fragmentation to writer/layout changes.
- [ ] Reconcile S3 billing coverage, run matched cohorts, and adjust Trino capacity with SLO checks.
- [ ] Publish net results with billing lag, coverage, and uncertainty; expand only after the gates pass.

This document consolidates the solution, but implementation and production proof remain outstanding wherever the checklist is open.

## 9. Specific defects and implementation contracts

This section expands the plan into reviewable engineering changes. **Confirmed** means the behavior was found in current source, not that its fleet-wide financial impact has been quantified. **Risk** means an execution path needs a reproducer or deployment verification. **Design gap** means the present contract is insufficient for the cost goal. Paths are relative to the repository; function names are the stable navigation anchors. The graph index returned older signatures for some functions, so findings below were checked against working-tree source.

### CR-01 — Deferred fallback loses durable representation — P0, confirmed

**Code:** [`build_native_request`](../cryo/governor/native_admit.py), `NativeRequestResult`, `_dispatch_fallback_operations`, native watcher completion, existing maintenance intent/effect and step ledgers.

**Trigger:** a plan contains native snapshot mutations and fallback snapshot mutations. The builder removes deferred operations from `fallback_operations`. Its return value contains the native request and remaining fallback operations, with no separate deferred-operation field. Repeating the same plan can repeat the removal indefinitely. The safety guard is necessary; the assumption that the next schedule will finish the work is not sufficient.

**Consequence:** the fleet can repeatedly pay for cleanup/startup while the desired rewrite never happens. Completion of the submitted native subset is insufficient evidence of plan completion.

**Preferred fix:** extend the existing intent/effect system rather than create another competing job ledger. Persist all ordered required effects before dispatch, including route, dependency, reason for deferral, retry eligibility, and policy version. Return deferred effects explicitly from routing. On native completion, reconcile its receipt and enqueue the next dependency-ready effect under the same logical maintenance intent. Preserve `expire → orphan → optimize → manifests` order; insert other operations according to the existing planner contract. Missing fallback configuration must produce an actionable blocked/partial result.

Use a transactional outbox or equivalent existing DB-to-queue reconciliation so a crash between saving progress and enqueueing cannot lose the continuation. Idempotency must identify the intent and specific effect/chunk, not only table plus operation type: one table can have many legitimate optimize chunks. Retries reconcile a known external execution before submitting again.

**Required regression:** plan native expiry plus fallback optimization; complete expiry, kill coordinator before enqueue, restart, complete fallback, and replay delivery. Assert one external execution per reconciled effect, preserved exclusions/order, and no success while required work is unfinished. Repeat the next scheduler cycle and assert it does not erase pending work.

### CR-02 — Failed lock renewal is ignored — P0, confirmed

**Code:** [`Worker._renew_table_lock`](../cryo/worker/worker.py), SQL statement loop, `_run_native_plan`, `_run_native_fallback_operations`, [`locks.py`](../cryo/queue/locks.py).

The initial ownership check has been fixed, but `_renew_table_lock()` discards the renewal boolean. Calls before subsequent work therefore do not stop that work when the lock has expired or changed owner. Long blocking external execution also needs a renewal mechanism independent of the statement loop. This is a correctness risk, not merely wasted compute.

**Preferred fix:** return an explicit ownership result and fail closed before every submission. Run bounded heartbeat renewal during external work using an isolated client/session. On lease loss, stop new submissions and request cancellation when the engine supports it. Keep the table in reconciliation until the external query/job is known terminal; a cancellation request alone is not proof it stopped. Use owner/attempt generations for DB transitions so a stale worker cannot finalize a new attempt.

Redis fencing cannot by itself stop an already submitted Trino/Spark query from committing. Catalog optimistic concurrency and an unresolved-execution admission barrier are both necessary. Inspect all recovery paths for the same rule.

**Required regression:** renewal returns false between statements; Redis is unavailable during a long query; another worker receives the job after TTL expiry; old worker reports success after ownership changes. Assert no second mutation is admitted until the first execution is reconciled.

### CR-03 — Metadata expiry is not physical reclamation — P0, confirmed behavior / missing end-to-end guarantee

**Code:** [`expireOptions`](../engine-go/internal/ops/expire.go) uses `table.WithPostCommit(false)`; native result receipts and reporting need to describe that effect correctly.

**Preferred near-term solution:** explicitly route physical expiry to an engine with supported deletion semantics after capability and retention validation, or retain native metadata-only expiry with an explicitly required, durable physical-cleanup effect. Do not silently label the latter “reclaimed.” Choose the route based on safety and measured startup/scan cost, not an assumption that native is always cheaper.

For already orphaned objects, use a candidate-discovery/reconciliation workflow. Track inventory time, reference-set time, object age, ownership, and deletion receipt. Fail closed if the catalog cannot be read or locations overlap ambiguously. Revalidate candidates against current protected state and writer safety requirements. Inventory is a candidate source, not deletion authority.

**Required regression:** metadata expiry succeeds but no delete occurs: report zero acknowledged physical reclamation. Partial batch deletion fails: preserve unsuccessful object debt. Test retained snapshots, branch/tag references, concurrent writers, versioned objects, and shared locations. Never infer reclaimed bytes from expired snapshot count.

### CR-04 — Score-sweep shortcut stops per-table learning — P1, confirmed working-tree gap

**Code:** [`run_benefit_score_update`](../cryo/scheduler/benefit_score_updater.py) passes `refresh_realized_value=False`; [`update_table_effectiveness`](../cryo/planner/benefit_score.py) then skips `raw.autocomp_realized_value`. [`refresh_cost_model_cache`](../cryo/ml/cost_integration.py) writes a different, global model artifact.

**Preferred fix:** enqueue affected table IDs after outcome reconciliation, coalesce duplicates, and process them in bounded batches. Persist a cursor and the latest consumed outcome watermark per table. Include a bounded sweep for missed events. Advance progress only with committed feedback; tolerate duplicate processing. Keep feedback freshness/confidence visible to admission and use the existing conservative heuristic when stale.

Do not apply partial or ambiguous outcome lineage as if it were a clean training example. Record why samples are excluded and the coverage of eligible outcomes. Otherwise the optimizer can learn from successful/easy jobs only and systematically underprice failures.

**Required regression:** a newly reconciled outcome changes the table's feedback without any full-fleet update; score-only updates preserve feedback; crashes cannot skip a table; stale feedback produces an explicit fallback. Correct the current comment suggesting the global runner replaces this field before merging the shortcut.

### CR-05 — Model training and readers use pod-local files — P1, confirmed default / deployment-dependent impact

**Code:** [`_state_dir`, `refresh_cost_model_cache`, `load_cached_cost_model`](../cryo/ml/cost_integration.py). Default storage is `/tmp/cryo/cost_model_cache.json`. The inspected Helm templates do not declare a shared model volume. Atomic replacement already protects local writes; it does not distribute them.

**Preferred fix:** store this small model in a versioned Postgres artifact record, or use an existing shared artifact service if one is already supported. Include schema/version, training watermark, timestamp, sample count, quality checks, checksum, and active-version pointer. Readers cache locally and periodically refresh the pointer. Promote only validated models; retain the previous valid version for rollback. Avoid a filesystem read on every projection when a bounded in-memory cache is sufficient.

**Required regression:** train in scheduler process A and predict in API/worker process B with separate temporary directories. Assert B sees the promoted version, handles incompatible/corrupt artifacts, and falls back after maximum allowed staleness. Log the model version used in each decision receipt.

### CR-06 — Snapshot-count deltas incorrectly represent writer activity — P1, confirmed

**Code:** [`detect_streaming_from_history`](../cryo/scanner/signals.py) returns `(False, 0.0)` whenever maintenance occurred between scans, and estimates commits from the net number of retained snapshots otherwise.

Writers can run during maintenance. Snapshot expiration can offset new commits in a count delta. Insufficient history and missing timestamps also currently collapse to zero, although they mean unknown. The comment that every maintenance action creates a snapshot is not a reliable model of Iceberg commit semantics.

**Preferred fix:** carry a typed activity signal with observation window, evidence source, writer-commit count/rate when known, and confidence/unknown status. Prefer writer telemetry or identified non-maintenance commits; use prior observations with explicit aging when full history is unavailable. Do not infer idle solely from net snapshot counts. Map unknown to conservative hot-writer handling; do not shorten retention automatically from this signal.

**Required regression:** 100 writer commits plus expiry leave the snapshot count unchanged; maintenance and streaming overlap; timestamps are absent; only one scan exists. All must avoid falsely asserting observed inactivity. Test downstream budget, cooldown, and streaming guard decisions too.

### CR-07 — Resource floors can violate configured ceilings — P1, confirmed

**Code:** [`scale_executor_profile`](../cryo/engines/spark/sizing.py) returns `max(500, configured_cpu × fraction)` and `max(1024, configured_memory × fraction)`. A configured maximum of 250m CPU or 768MiB therefore produces 500m/1024MiB. This contradicts the function's stated ceiling contract.

**Preferred fix:** define and validate minimum supported engine resources separately from user ceilings. Reject an impossible profile clearly when minimum exceeds maximum; never silently exceed a hard ceiling or clamp below a runtime safety minimum. For valid profiles enforce `minimum ≤ request ≤ configured ceiling`. Validate negative/nonfinite size inputs rather than letting them select a tiny tier.

Then replace whole-table-only sizing with operation profiles: driver metadata demand from manifests/snapshots/file counts, executor demand from selected rewrite groups, concurrency, and measured memory amplification. Unknown metadata size requires a capped conservative profile, not a falsely precise small-table estimate. Include memory overhead/sidecars in pod accounting.

**Required regression:** ceilings below/equal/above floors, unknown and invalid sizes, tier boundaries, metadata-only maintenance on a very large table, and a small selected chunk on a large table. Verify generated Kubernetes requests, not just the profile DTO.

### CR-08 — Background work can restart together and retain unnecessary objects — P1, confirmed paths / OOM attribution incomplete

**Code:** [`DaemonRunner.run`, `run_scheduler`, `CostModelRefreshRunner`](../cryo/cli.py), [`update_all_benefit_scores`](../cryo/planner/benefit_score.py).

The daemon base runs a tick immediately on startup. `run_scheduler` starts several independent threads. The model runner has no leadership check in its own tick. A rolling deployment or repeated restart can therefore restart expensive work independently of the main scheduler's leadership. Audit each runner individually before claiming replica-safe behavior.

The score sweep calls `.all()` for active `Table` objects and retains the `tables` list after extracting IDs. `expunge_all()` detaches them but does not release references held by that list. This is a confirmed avoidable allocation, not proof it explains the entire OOM.

**Preferred fix:** select only IDs with keyset pagination; make each batch transaction bounded. Centralize runner registration for `scheduler` and `serve` so production wiring cannot diverge. Give heavyweight analyses persisted next-run/cursor state and independent leases, with one shared concurrency budget. Isolate heavy work in processes/jobs with enforceable deadlines instead of assuming thread timeouts release memory.

**Required regression:** start two scheduler instances; restart during training; run multiple heavy jobs due at the same time. Assert one owner per task, bounded concurrent memory, and resumable progress. A load test with realistically large history JSON/partition cardinality must demonstrate bounded peak memory. Garbage collection calls are not a substitute for bounded reads.

### CR-09 — Broad job summaries multiply control-plane work — P2, confirmed query pattern

**Code:** [`_job_record`, `_table_autocomp_summary`](../cryo/db/repositories/job_store.py).

Per-job assignment, AutoComp, and intent lookups turn frequent list polling into repeated per-row queries. Full execution plans in a polling response also consume memory and bandwidth unnecessarily.

**Preferred fix:** add a narrow listing projection with batched lookups or set-based joins. Keep execution details on a separate detail read using existing API conventions. Keyset-page large result sets and ensure filters have appropriate indexes verified with query plans. Do not expose full `raw`/plan blobs by default. Regenerate frontend API types when the contract changes.

**Required regression:** a list of 100 jobs has bounded query count rather than one or more extra queries per job. Verify payload size, query latency, and RSS on production-shaped fixtures. Confirm detail views and workers still receive all required execution fields.

### CR-10 — Repeated rewrites can outrun their payback — P1, design gap to validate in admission

**Code areas:** planner candidate/chunk selection, benefit scores, governor admission, operation receipts, scanner watermarks.

**Preferred decision rule:** compare conservative benefit over an explicit horizon against startup + execution + GET/PUT/LIST + temporary retained storage + retry risk. Require adequate eligible files/bytes and observe the actual output file distribution. Separate mandatory overdue safety work from discretionary compaction so an uncertain ROI model cannot disable maintenance indefinitely.

Store a no-op fingerprint using the selected partition/chunk, operation parameters, relevant data/delete evidence, and policy version. A repeated fingerprint should defer until relevant evidence or a bounded reevaluation deadline changes. A metadata-only snapshot change need not invalidate every compaction decision. Do not use a permanent blacklist: new delete files or writer output must make a partition eligible again.

**Required regression:** unchanged data after a no-op does not dispatch another rewrite every cron cycle; new eligible files reopen it; one-file partitions do not become large full-table rewrites; changed delete coverage is reconsidered. Keep native/Spark/Trino renderers separate and add applicable golden/parity coverage.

### CR-11 — “Success” and “bytes freed” can reward the wrong behavior — P1, evidence contract gap

**Code areas:** `cryo/intelligence/outcomes.py`, maintenance receipts, impact signals, cost dashboard, reporting.

**Preferred fix:** keep execution status separate from effect and financial evidence. A successful manifest rewrite can have planning value and zero reclaimed bytes; a successful metadata-only expiry has zero acknowledged object deletion. Neither should invent dollar savings. Conversely, zero reclaimed bytes must not automatically make a query-efficiency operation a failure.

Extend existing receipt/outcome schemas additively with measurement basis, before/after watermark, reference scope, physical deletion counts/bytes, measurement interval, source, and coverage. Represent unknown values as unknown rather than zero. Avoid counting the same bytes once at snapshot expiry and again at orphan deletion. Deduplicate receipts by effect/object identity where appropriate.

**Required regression:** overlapping maintenance windows, concurrent ingestion, delayed post-scan, duplicate receipt, metadata-only result, and versioned delete. A dashboard must not convert these directly into verified net savings. Separate estimated monthly run rate from actual dollars accrued in the observation period.

### CR-12 — Capacity left allocated can erase Trino savings — P1, operational design gap

**Code/config areas:** reporting cost attribution, Trino fleet capacity configuration, Cryo admission budgets, deployment requests.

**Preferred fix:** establish two gates: first workload-normalized query demand improves; then allocated capacity falls while p95 latency, queue time, spill, and failure rates remain acceptable. Track requested/allocated and used capacity independently. Use observed peaks and headroom rather than matching requests to a single `kubectl top` sample.

Set a bounded daily discretionary maintenance spend allowance and separate overdue safety work. As an initial operating rule, expansion pauses when the canary's full maintenance cost exceeds its conservative expected benefit; overdue reclamation remains visible and explicitly scheduled rather than silently discarded. Determine the dollar ceiling from actual covered spend and workload evidence, not a made-up fleet-wide percentage.

**Required verification:** lower query CPU with unchanged nodes is labeled efficiency improvement, not realized compute savings. After controlled scale-down, reconcile node hours and CUR cost, including effects on unused allocation and other shared workloads.

## 10. Changes that would actively undermine the goal

| Tempting response | Why it can hurt | Required alternative |
|---|---|---|
| Increase all memory/replicas after OOM | Hides unbounded work and raises idle cost. | Bound reads/concurrency, then size from peak usage. |
| Enable every maintenance operation at every schedule | Adds startup, scans, requests, and overlapping work. | Evidence-based per-operation eligibility and durable completion. |
| Remove native/fallback exclusion to make missing work run | Allows concurrent table mutations. | Sequential durable continuation, CR-01/02. |
| Enable native physical deletion with one flag | Changes data-safety behavior without proving protected-reference handling. | Capability validation and bounded reclamation canary. |
| Set the small-file threshold extremely low everywhere | Rewrites irreducible partitions or perpetually recreates retained data. | Eligible partition groups and measured payback. |
| Stop learning to fix scheduler memory | Produces stale decisions while appearing stable. | Bounded incremental learning with freshness signals. |
| Shorten retention globally to show freed bytes | Changes recovery/time-travel guarantees and may conflict with writers/readers. | Per-table policy evidence and explicit retention validation. |
| Train directly on modeled or ambiguous savings | Creates a feedback loop that rewards its own estimates. | Measured, lineage-qualified labels and holdout checks. |
| Declare savings because used allocation fell | The capacity may still be billed as unused. | Actual capacity reduction and reconciled billing. |
| Add more engines/AI/UI before fixing the execution loop | Multiplies paths and operating burden without trustworthy outcomes. | Complete the rescue gates first. |

These are failure modes to prevent, not allegations of intentional sabotage. Several originate in individually reasonable fixes whose end-to-end consequences were not represented in the system.

## 11. Suggested change sets and release evidence

Keep each change independently reviewable; do not merge a single rewrite of the platform.

| Change set | Scope | Required evidence before expansion |
|---|---|---|
| A | CR-07 resource ceiling validation; CR-08 narrow score reads | Boundary tests, rendered resource checks, bounded memory profile. |
| B | CR-04 incremental table feedback; CR-05 shared model | Restart/cross-process tests, freshness and model-version telemetry. |
| C | CR-02 lock-loss handling; CR-01 durable continuations | Engine lifecycle crash/recovery tests, no concurrent mutation, no missing effects. |
| D | CR-03 physical reclamation; CR-11 receipts | Protected-reference fixtures, partial-failure recovery, physical object evidence. |
| E | CR-06 activity signals; CR-10 selective rewrites | Mixed writer/maintenance fixtures and matched partition/query canaries. |
| F | CR-09 control-plane efficiency; CR-12 billing/capacity | Bounded polling query counts, cost coverage, actual net result with SLO evidence. |

CR-02's immediate pre-submit failure check should land early; its full external-execution reconciliation is developed with C. CR-11's measurement contract must be agreed before D/E can make savings claims. A–F are implementation slices, not permission to ignore the P0 safety priorities.

For every slice, attach: source revision/image digest; effective configuration; test results with baseline failures separated; deployment time; canary/control membership; before/after evidence timestamps; and rollback/reconciliation result. Mark **implemented**, **deployed**, **operationally verified**, and **financially verified** separately. This prevents another cycle in which a fix exists in source but the production entrypoint, configuration, or reporting path never uses it.
