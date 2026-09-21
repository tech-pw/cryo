# R1–R6 review and next implementation plan

**Latest status:** [Follow-up review after the reported 146-test run](CRYO_RECOVERY_FOLLOWUP_REVIEW_2026-09-20.md).
Several fixes now exist, but additional partial findings remain beyond F02/F06/F07. Use that
document for current disposition; the findings below describe the initial review baseline.

Review date: 2026-09-20. Baseline: `9e015aff` plus the current uncommitted R1–R6 working tree.
This is a source review and implementation plan, not production validation. No deployment,
database mutation, incident reconciliation, or source-code fix was performed by this review.
Read alongside [the permanent recovery plan](CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md).

## Decision

Keep `benefit_score_sweep_via_k8s_jobs=false`. The implementation is useful groundwork, but
R2/R3 are not ready to enable: the production entrypoint bypasses the coordinator, Jobs lack
runtime configuration, and normal bounded execution leaves tasks permanently unclaimable.
The lease write paths do not yet implement the advertised concurrency contract.

“All new behavior is opt-in” is inaccurate. Finalizer selection, partition pagination/scoring,
and impact reporting change existing paths even with the Job flag disabled. Review these as
release changes in their own right. Leaving code uncommitted and undeployed is different from
proving that a deployment with defaults is safe.

The immediate next slice is **close the R1–R6 correctness gaps**, then bounded R7 capacity
work and R8 measurement work. Do not add a new orchestration framework or expand engine scope.

## Verified findings and specific repairs

P1 means close before enabling/releasing the affected path. P2 means close before relying on
its accounting or burn-in evidence. Function references identify the reviewed working tree;
line numbers will move as fixes land.

### F01 — P1: production scheduler ignores the Job switch

Evidence: `cryo/cli.py::run_scheduler` starts
`cryo.scheduler.benefit_score_updater.benefit_score_updater_loop_sync` in a thread. That loop
still runs the heavy sweep directly. The flag check is in `BenefitScoreUpdaterRunner._tick`,
which `run_serve` uses. `deploy/helm/cryo/templates/scheduler-deployment.yaml` runs
`cryo scheduler`, not `cryo serve`.

Fix: make the production entrypoint use the same flag-aware coordinator. Preserve shutdown
and leadership semantics; do not start both loops. Keep coordinator polling separate from the
six-hour analysis due interval. Persist the next due time so a restart cannot schedule another
completed sweep early. Test both real CLI entrypoints with the flag on/off and assert that
the enabled production path never calls the in-process scoring function.

### F02 — P1: analysis Jobs do not inherit the production runtime

Evidence: `cryo/analysis/dispatch.py::dispatch_analysis_job` sends no `env`/`envFrom` database
configuration. It falls back to `cryo:latest` and service account `default`; the chart-created
`-analysis` account is not wired into dispatch. Image pull and placement configuration are
not carried into the manifest. Scheduler identity is conditional on `spark.k8s.enabled`,
while the new role is conditional on `analysis.enabled`. The analysis child account also
receives Job create/delete rights despite the template comment saying it cannot create Jobs.

Fix: define one explicit analysis runtime contract: release image/digest, namespace, child
service account, required Secret/ConfigMap references, image pull configuration, placement,
resources, and deadline. Fail configuration validation instead of guessing an image. Pass
secret references, never secret values in logs. Bind dispatch rights to the scheduler identity
independently of Spark; the database-only child normally needs no Kubernetes API rights.
Render and integration-test analysis on/off crossed with Spark on/off, including a private
image and custom release name. A real child must read its task in a staging database.

### F03 — P1: bounded exit and crash recovery strand tasks

Evidence: `analysis_tasks.claim_due_task` selects only `pending`/`leased` rows.
`commit_progress` changes the row to `running`. The runner exits successfully after ten
batches without releasing that state. Neither its normal exit nor a crash after the first
checkpoint can be reclaimed, even after lease expiry. The active row also blocks creation
of a replacement task.

Fix: implement explicit yielding to a resumable pending state, preserving the cursor and
releasing the owner through a fenced transition. Recover expired running attempts after
reconciling their external Job and fencing the old owner. Distinguish “attempt yielded” from
“logical sweep succeeded.” Test more than 2,000 tables, crash after checkpoint, deadline kill,
and repeated continuation. Require final coverage, not just a zero process exit code.

### F04 — P1: lease updates are check-then-write, with a generation reuse bug

Evidence: `heartbeat`, `commit_progress`, and `finish_task` read with `session.get`, compare
`lease_generation` in Python, then commit ORM updates. Another owner can claim between the
read and write. `start_new_generation` resets the lease generation to zero, so the next owner
gets `1` again: an old owner with token `1` can match the new generation. Heartbeat does not
check terminal status or lease expiry and can move a terminal row back to running.

The initial claim does use `FOR UPDATE SKIP LOCKED`; that is useful. Its docstring incorrectly
describes a single conditional UPDATE. The serious problem is the subsequent writes.

Fix: use database-conditional updates checking task ID, task generation, monotonic fencing
token, owner, active status, and unexpired lease. Require exactly one affected row. Never
reuse a fence. Use database time consistently. Test actual concurrent PostgreSQL sessions,
including stale ORM objects, old-generation callbacks, completion versus heartbeat, and
claim versus checkpoint. Sequential lease-mismatch tests do not prove these races safe.

### F05 — P1: scoring outputs commit before ownership is checked

Evidence: `update_all_benefit_scores_batch` calls scoring/effectiveness functions that commit
their outputs before `runner.py` calls `commit_progress`. A stale child can write results
even if its later cursor update raises `LeaseConflict`. The runner does not heartbeat during
the expensive batch; a 200-table batch can exceed the 300-second lease. The Job deadline is
900 seconds. Table count alone is not a memory or elapsed-time bound.

Fix: split computation from persistence. Commit each bounded output page and its cursor in
one transaction protected by the current fence; roll everything back if ownership was lost.
Heartbeat independently with a fresh session while expensive computation runs. Bound rows,
working-set size, SQL duration, and invocation wall time, with time reserved for checkpoint
and shutdown. Fault injection must prove no stale writes and no skipped committed pages.

### F06 — P1: external dispatch is not reconciled durably

Evidence: `_tick_via_k8s_job` checks a stored reference/lease, but never checks Kubernetes Job
state. Dispatch creates a random name and only afterwards persists the reference. A crash
or ambiguous API response can leave an unrecorded Job; another coordinator can dispatch a
duplicate. `next_retry_at` does not govern this path. Tasks are claimed by type rather than
the particular task/attempt dispatched.

Fix: atomically reserve a dispatch attempt, give it a deterministic task/generation/attempt
name, and pass that identity to the child. Reconcile the existing name on retry and after
restart. Persist retry/backoff and next-due state. Do not interpret lease expiry as proof that
the old process stopped. Test two coordinators, create-success/response-loss, scheduler crash
before reference persistence, Pending Pods, failed Jobs, and deletion/TTL races.
Kubernetes itself requires applications to tolerate repeated execution; a Job is not an
exactly-once boundary ([official Job documentation](https://kubernetes.io/docs/concepts/workloads/controllers/job/)).

### F07 — P1: pagination does not establish complete or comparable scores

Evidence: `update_partition_benefit_scores` processes one ordered page of at most 10,000
partitions. The fleet runner advances the table cursor after that page, even when more
partitions remain. A new latest scan resets the partition cursor. A large frequently scanned
table can therefore repeatedly score its first page and never reach the remainder.
`compute_moop_scores` min/max-normalizes the current page: scores can depend on arbitrary
page membership, rather than a consistent table-wide population.

Fix: persist table + scan watermark + partition cursor, and finish all pages for that input
before declaring table coverage complete. Keep input generations available long enough to
finish, or explicitly mark superseded generations incomplete and ensure bounded progress
against stable partition identities. Compute table-wide normalization aggregates in a bounded
first pass and score pages in a second pass. Pin other inputs that affect cross-page scores.
Test over 10,000 partitions, scan replacement midway, empty final page, and score invariance
under different page sizes/order. A fleet task cannot succeed with unexplained missing pages.

### F08 — P1: expensive feedback budget multiplies across batches

Evidence: `_DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP` is 100. The new batch function
resets that budget on every 200-table batch. Roughly 13,000 tables can now allow roughly
6,500 expensive refreshes per fleet generation instead of 100, subject to eligibility.
The comment claiming unchanged per-sweep semantics is wrong.

Fix: persist one generation-wide refresh budget and its consumption, including retries.
Choose stale eligible tables fairly across the fleet; taking the first pages alone would
starve later IDs. Treat any higher budget as a deliberate, measured capacity change.
Test the sum across multiple invocations, restart, and retry; it must not exceed the configured
generation budget. This is directly relevant to Cryo's own cost and analysis memory pressure.

### F09 — P1: R4 fixes sequential re-entry, not concurrent terminalization

Evidence: `finalize_open_runs` now selects only running parents and preserves a non-null
`finished_at`. These are valid improvements. However, two sessions can both load a running
parent with no finish timestamp before either commits. The Python null check alone does not
make the timestamp immutable under that interleaving.

Fix: use a conditional terminal transition or a shared parent-row locking protocol. Recheck
child eligibility under the same protocol used by child admission/reconciliation. Confirm
all writers participate; inspecting only reconcile's parent-state check is insufficient.
Test finalizer versus finalizer and finalizer versus child requeue/admission using PostgreSQL
barriers. Preserve the first finish timestamp and prohibit runnable children under a terminal
parent. Do not lengthen the grace window again as a substitute for this proof.

### F10 — P1: R6 still presents incomplete evidence as measured impact

Evidence: `DailyImpact.bytes_freed_is_known` becomes true if *any* successful action has byte
evidence. One measured action plus 999 unmeasured actions is therefore treated as known.
`gb_freed` remains numeric zero for missing measurements. `vitals.py::cryo_vitals_to_payload` and
several `daily_report.py` payload/table paths still emit that number; the Slack “GB Reclaimed”
cell still formats it as GB even when the narrative says unknown.

Fix: expose measurement state (`complete`, `partial`, `unmeasured`, `no_actions`) and telemetry
freshness, with measured subtotal, eligible/sampled counts, and explicit coverage. Use null
for an unknown total. Suppress total-impact percentage comparisons unless coverage and
measurement method are comparable. Keep measured zero distinct from missing evidence.
Update every API/report/UI consumer and generated types. Test no evidence, partial evidence,
genuine zero, no actions with fresh telemetry, and stale telemetry across current/baseline days.

R6 repairs interpretation; it does not supply Trino deletion receipts or prove S3 savings.
Plan operation-specific evidence: snapshot expiry, orphan deletion, and compaction have
different effects. Compaction can improve reads while retained snapshots still reference old
files. Respect connector retention constraints and keep expensive evidence collection off
hot request paths ([Trino Iceberg maintenance documentation](https://trino.io/docs/current/connector/iceberg.html)).

### F11 — P2: incident-window membership is not confirmed incident causality

Evidence: the seed script selects every run created September 17–18, with implicit timestamp
timezone interpretation. This is broader than an evidence-confirmed zombie-run cohort.
Persistence without job/run mutation is the correct scope. The reported 37 runs and two
remaining stuck runs were not independently re-queried in this review.

Fix: distinguish candidate window members from confirmed affected runs; persist explicit UTC
bounds, exact IDs, evidence, classification version, and reconciliation outcome. Only verified
members qualify for the incident-specific view. Always retain the aggregate. Drain through
existing recovery after engine/receipt checks, never by blanket terminal-state updates.

### F12 — P1 validation: new PostgreSQL tests need an isolated database contract

Evidence: the availability check uses the configured `SessionLocal` and `SELECT 1`. Runner
tests invoke the real `benefit_score_sweep` type, traverse active tables in that database,
and delete tasks by that type during cleanup. Checking for no active task does not make a
shared database safe. Other cleanup matches broad `test_%` patterns.

Fix: provision a dedicated disposable PostgreSQL database, apply migrations, inject its
session factory, and fail closed unless the test database is explicitly identified. Clean
only fixture-owned IDs. Require PostgreSQL concurrency tests in CI, without silent skips.
Add the missing multi-batch, fencing, dispatch, and production-entrypoint cases above.

## R1 evidence still required

The dispatch module calls its resources placeholders pending profiling. This review does not
establish an R1 production measurement baseline. Record scheduler RSS/working set versus
uptime, each background runner's duration and allocation peaks, child peak memory, rows and
bytes processed, query duration, checkpoint age, and exit reason. Include global-model and
other scheduler threads: moving benefit scoring does not prove that every growth source is gone.
Use bounded metric labels; task/table IDs belong in logs or database records.

## Bounded next delivery sequence

| Slice | Concrete scope | Exit evidence |
|---|---|---|
| A: safe verification | F12 isolated PostgreSQL harness; R1 baseline instrumentation | Reproducible test database, migrations, concurrency failures reproduced safely |
| B: durable execution | F03–F06; task/attempt identity, transaction fences, lifecycle reconciliation | Full multi-invocation sweep survives crashes and duplicate dispatch without stale writes |
| C: complete bounded scoring | F07/F08; input pinning, normalization, generation budget | Complete large-table coverage; stable scores; fixed total refresh budget and bounded RSS |
| D: runtime integration | F01/F02; actual CLI and chart matrix | Staging scheduler dispatches correctly configured child; no heavy sweep in scheduler |
| E: trustworthy lifecycle/impact | F09–F11; finalize race, all impact outputs, exact cohort | Concurrent lifecycle tests and API/Slack fixtures pass; no false numeric zero |
| F: R7 capacity | Deliverables below, one lever per canary | Lower allocation waste without weaker throughput, latency, or reliability |
| G: R8 economics | Deliverables below, retain aggregate history | Auditable comparable-window net benefit, with uncertainty and coverage |

Measurement definitions for G can be written while B–E are implemented. Do not wait until
after rollout to establish the baseline. No slice is “done” solely because its happy-path
unit tests pass.

## R7: containment and right-sizing, without another subsystem

1. **Capture the real baseline.** For api, worker, governor, scheduler, scanner, analysis,
   native, and Spark: replicas, requests/limits, p50/p95/p99 active CPU/working set, restart
   reasons, pending duration, queue age, successful useful work, and request-seconds.
   Separate working, waiting-on-dependency, and idle periods. Include ephemeral Jobs whose
   workload-name billing tag is empty. Use the current Helm release and rendered manifests;
   do not copy historical replica counts as if they were current.
2. **Stop dependency failures from reserving more capacity.** Extend existing catalog-scoped
   admission with capped backoff and limited recovery probes. Do not dispatch another fleet
   of large Pods while the same catalog is unavailable. Preserve governor load checks,
   per-table locks, deadlines, and partition-aware planning. Test outage, partial recovery,
   and one bad catalog alongside a healthy catalog.
3. **Right-size each execution class.** Measure successful heavy-tail cases before reducing
   memory. Make analysis resources configurable after F05/F08. Recheck native packing
   estimates against actual batch peaks. Replace static Spark fallback sizing with bounded
   metadata-derived profiles and a conservative unknown-input profile; maintain minimums,
   ceilings, and outlier handling. Never rerun a potentially completed write solely to resize
   its Pod without reconciling its engine receipt.
4. **Tune replica floors against service obligations.** Scanner backlog/freshness controls
   scanner scaling; governor admission controls real maintenance concurrency. Do not scale
   workers merely because an external engine is saturated. Lower standby requests based on
   failover needs as well as steady idle usage. Do not simultaneously shrink memory, replicas,
   and admission caps: otherwise regressions cannot be attributed.
5. **Verify bill effects.** Report `unused / (used + unused)` and, separately, `unused / used`;
   the old “3.4x” refers to the latter. These are allocation metrics, not proof of lower AWS
   spend. Check node consolidation and total reconciled cost; lower requests alone may only
   move allocation between namespaces. AWS explains the allocation model in its
   [split-cost documentation](https://docs.aws.amazon.com/cur/latest/userguide/split-cost-allocation-data.html).
   Append every fresh measurement to `docs/cost-tracking/daily-cost-log.csv`, retaining billing
   completeness timestamps. No fresh billing figures were collected in this source review.

R7 acceptance: compared with the baseline, lower unused allocation and cost per useful work
unit, no new OOM/eviction trend, no worse scheduling/freshness SLO, and no growing dependency
reservation backlog. Set numerical resource values from measurements; do not invent a
universal memory request or savings percentage.

## R8: scorecard contract that measures outcomes honestly

| Metric | Required definition / evidence |
|---|---|
| Scheduler stability | OOM/eviction/restart counts plus RSS trend and scheduling lag; show other background runners |
| Analysis health | Due-to-complete latency, oldest checkpoint, table and partition coverage for a pinned input, retry cost |
| Fresh-run reliability | Runs created after the recorded cutover; succeeded / (succeeded + failed) terminal jobs, with open/aged/deferred/partial counts separately visible |
| Execution reliability | Engine-started attempts and their outcomes, separate from admission deferrals and legacy reconciliation |
| Historical reliability | Existing all-job rolling view retained, with explicit versioned cohort attribution; no deletion of inconvenient failures |
| Reclaim impact | Eligible action coverage, measurement state, observed deleted bytes versus estimated/logically obsolete bytes |
| S3 efficiency | Storage byte-days and actual GET/PUT/LIST/DELETE request counts/cost for attributable scope, including maintenance overhead |
| Trino efficiency | Matched query fingerprints/workloads: bytes read, CPU/query, p95 latency, failures and demand; exclude maintenance SQL from user-query cohort |
| Cryo efficiency | Used/unused/total cost including anonymous Job bucket, request-seconds, successful useful work; failed/retried work is a cost |
| Net benefit | Comparable-period avoided storage/request/Trino cost minus incremental Cryo and maintenance cost; distinguish measured from projected |

Use GiB when dividing bytes by 1024³; do not silently label it decimal GB. De-duplicate
receipts by action/attempt/object evidence, prevent overlap between expiration and orphan
cleanup claims, and account for S3 versioning or retention before claiming billed storage fell.
Avoid full-bucket LIST/HEAD sweeps merely to make a dashboard number: use existing receipts,
metadata and bounded asynchronous verification, with its request cost included.

Demand changes, ingestion/deletion changes, retention policy, instance rates and node counts
can change bills independently of Cryo. Record those covariates. A matched holdout or phased
table rollout gives stronger evidence than an unadjusted before/after bill. Use the existing
cost-tracking methodology and its minimum data requirement; statistical significance alone
does not establish causality. No evidence here supports promising a drastic percentage saving.

## Rollout and burn-in gates

Keep additive migrations compatible with the prior binary. Test migration application on an
existing populated schema and recovery from interruption before deployment. Do not drop
analysis/cohort evidence tables on application rollback.

Before enabling Jobs: all P1 findings closed, required quality checks pass, complete staging
fleet/large-table and fault-injection runs pass, and actual production CLI/chart wiring is
verified. Canary one analysis generation with bounded dispatch and a measured resource budget.
Record task coverage, maximum RSS, coordinator lag, duplicate attempts, and database load.

Rollback must pause new analysis dispatch and fence/reconcile outstanding attempts. Merely
turning the flag off can restart the old heavy in-process path, so explicitly define a paused
analysis mode or equivalent operational control. Preserve cursors for later recovery.

Start the clean ten-day evaluation only after scheduler stability, useful maintenance,
measurement freshness/coverage, and lifecycle invariants qualify. Retain the recovery plan's
24-hour wait after the last legacy terminal event for the aggregate 24-hour view; fresh-work
metrics can be collected immediately with the cutover clearly labeled. A new scheduler OOM
or lifecycle violation resets readiness. Unknown impact blocks a savings claim, not truthful
reporting of progress.

## Validation performed by this review

Executed successfully:

```text
.venv/bin/pytest -q tests/test_benefit_score_integration.py tests/test_impact_signals.py tests/test_run_finalizer.py
69 tests passed (exit 0)
```

These tests establish covered sequential behavior, not PostgreSQL concurrency, Kubernetes
integration, large-fleet completeness, bounded production memory, or economic benefit.
New PostgreSQL tests were not rerun against the configured database because of F12. No full
lint/typecheck/full-suite, live capacity query, or deployment was performed in this docs-only
review. The review intentionally preserves the existing implementation for focused follow-up
fixes rather than changing it under a documentation request.
