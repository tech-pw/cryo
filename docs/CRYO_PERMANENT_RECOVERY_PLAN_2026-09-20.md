# Cryo permanent recovery and metric improvement plan

**Current implementation status:** See the
[follow-up review](CRYO_RECOVERY_FOLLOWUP_REVIEW_2026-09-20.md). The reported 146 passing tests
do not close all ownership, crash recovery, evidence, or test-database isolation requirements.

**Implementation review added 2026-09-20:** The R1–R6 working-tree implementation needs
correctness and integration repairs before rollout. See
[R1–R6 review and next steps](CRYO_R1_R6_REVIEW_AND_NEXT_STEPS_2026-09-20.md) for confirmed
findings, regression scenarios, delivery order, and concrete R7/R8 scope. Historical behavior
described below is the original design baseline, not the status of every current function.

**Evidence baseline:** repository `9e015aff`, including its September 20 10:49 UTC scorecard
entry and current source inspection. Incident numbers below are from that recorded investigation,
not a new live measurement. This document specifies implementation; it does not claim the fixes
are shipped. It takes priority for immediate recovery over the broader
[cost reduction roadmap](CRYO_COST_REDUCTION_PLAN.md).

## 1. Decision: repair execution, not the appearance of the scorecard

The recorded situation is severe: vitals 12/100, 1,912 successes versus 68,644 failures in a
rolling day, zero reported GB freed yesterday, and scheduler OOM/eviction after hours of growth.
The investigation traced roughly 2,058 `orphaned_deferred` events/hour to 20 runs created during
September 17–18. That is credible evidence of historical cleanup in that sampled failure class;
it is not proof that every failure in the day is historical or that current dispatch is healthy.

Three separate repairs are required:

1. **Bound scheduler memory and isolate heavy analysis from scheduling.** Do not increase memory
   again as the permanent fix, or disable feedback indefinitely.
2. **Reconcile old work once and prevent new orphaned work structurally.** Longer grace periods
   mitigate an outage; they do not establish correct parent/child lifecycle semantics.
3. **Restore useful maintenance and prove its effects.** A better success percentage after old
   failures age out is not evidence that storage, query efficiency, or net cost improved.

There are two corrections to the previous narrative:

- Failure volume affects the success-rate denominator. It does **not mathematically drive GB
  freed to zero**: `impact_signals._day_impact` separately sums successful actions' byte fields.
  Zero needs its own investigation: no useful execution, missing receipts, excluded operation,
  stale ingestion, a genuinely empty reclaim opportunity, or incorrect measurement.
- `unused_cost / split_cost` measures allocation efficiency, not net savings. Do not subtract a
  daily infrastructure bill from a projected monthly storage-saving number.

## 2. Confirmed implementation gaps

| Area / source | Current behavior | Consequence | Permanent repair |
|---|---|---|---|
| `cryo/cli.py::run_scheduler` | Starts benefit scoring, global model refresh, and other services as threads in one process. | Heavy allocations and failures share the scheduler's lifetime and resource boundary. | Lightweight scheduling coordinator; separate bounded analysis executions. |
| `cryo/scheduler/benefit_score_updater.py` | Immediately starts a fleet sweep and repeats after an interval. | Restarts can restart expensive work without durable progress. | Persist due time, lease, attempt, cursor, and completion. |
| `cryo/planner/benefit_score.py::update_all_benefit_scores` | Materializes active Table objects with `.all()`; keeps `tables` after extracting IDs. Per-sweep feedback cap does not bound all partition scoring. | Detaching ORM objects does not release the list; work remains fleet-sized. | Narrow keyset pages and bounded child lifetime. |
| `update_partition_benefit_scores` | Reads `.limit(10000)` partitions from the latest scan, without an ordering/cursor in that query. | A memory cap can silently leave a large table partly scored; repeated sweeps need not cover the remainder. | Stable pagination pinned to a scan watermark, with coverage recorded. |
| `cryo/db/repositories/run_finalizer.py::finalize_open_runs` | Selects every run except cancelled, including already finished runs; assigns a new run `finished_at` when finalizing again. | Historical runs are repeatedly scanned and completion timestamps can move. | Open-run selection, immutable terminalization, atomic parent/child coordination. |
| `cryo/reporting/vitals.py::_success_rate_pulse` | Counts succeeded/failed jobs by `finished_at` in the rolling window. | Cleanup of old jobs obscures new-work reliability. | Preserve aggregate view; add explicitly defined cohort and execution views. |
| `cryo/reporting/impact_signals.py::_day_impact` | Uses successful audit rows and coalesces a byte sum to zero. | Missing byte evidence may look like measured zero. | Coverage/freshness, distinct unknown state, operation-specific receipts. |

The current realized-value cap (default 500 tables/sweep, stalest first) is useful but incomplete.
It is not equivalent to bounding RAM, query-result bytes, or all tables' partition work. Profiling
is still required to assign the scheduler's growth to individual runners; do not assert the
benefit sweep is the only contributor simply because it is an expensive path.

## 3. Permanent scheduler design

### Chosen production architecture

Keep the scheduler pod as a small coordinator. Run heavy analysis as **short-lived Kubernetes
Jobs with bounded batches**, independently limited from the scheduler. Use the same image and
an explicit one-shot CLI entrypoint, not a second scheduler that starts every daemon again.
Start with benefit/partition scoring and realized-value refresh; isolate global model training
and other proven heavy analysis next. Lightweight checks can remain in-process when measured
to be bounded.

A child process inside the same pod is a useful interim step but shares that pod's memory
budget. It can still trigger pod eviction/OOM before exiting. One child running an entire
multi-hour fleet sweep merely moves the existing failure. Separate Job resource boundaries
plus resumable work units are the production solution.

Use the existing application scheduler rather than introduce Airflow or an independently
scheduled CronJob that competes with it. A single task registry must serve both `cryo scheduler`
and `cryo serve` so production wiring cannot omit a newly added runner again.

### Persisted task contract

Extend existing durable task facilities if suitable; otherwise introduce an additive migration
for analysis tasks. Minimum fields:

`task_type, scope, generation, status, due_at, input_watermark, cursor, lease_owner,
lease_generation, lease_until, heartbeat_at, attempt, external_job_ref,
started_at, finished_at, processed_count, failure_reason, next_retry_at`.

- One active logical task per type/scope/generation; enforce uniqueness in the database.
- Claim with a transactional compare-and-set/row lock. Dispatch intent and outbox state commit
  together. Use a deterministic Job name so an uncertain create response can be reconciled.
- A child receives IDs/cursors, not a giant serialized fleet or ORM graph. Create fresh DB
  connections in the child. A process-based interim implementation should use exec/spawn,
  not reuse a multithreaded parent's database pool across fork.
- Commit output and cursor progress atomically for each work unit. Only advance after output
  persistence succeeds. Lease generation rejects writes by an obsolete attempt.
- On restart, reconcile the external Job before resubmitting. Kubernetes Jobs are not an
  exactly-once system; repeated execution must be idempotent.
- Start with one heavy analysis Job at a time for the fleet. Keep its capacity pool separate
  from data-rewrite capacity; healthy analysis must not exhaust maintenance resources.
- Persist retry backoff and next due time. Restarting the scheduler must not make every task due.

### Bound both data and time

Select table IDs in deterministic keyset pages. Process partitions by `(table_id, scan_at,
stable_partition_key)` against a pinned snapshot of scan evidence; do not use OFFSET for an
evolving fleet. If a new scan invalidates the generation, finish or discard that generation
explicitly and schedule the new one. Never mix generations silently.

Use a small initial table batch and partition page size in staging, then tune to measured
memory/time budgets. Batch size alone is insufficient because one table may have hundreds of
thousands of partitions. A single oversized table must make resumable progress in bounded pages.
Where a scoring algorithm needs whole-table normalization, compute aggregate statistics in a
first pass and page updates in a second, preserving existing scoring semantics.

Select scalar fields only; do not fetch complete history/raw JSON when a numeric projection
suffices. Stream or aggregate in SQL with a statement timeout. Close sessions at batch boundaries.
Remove references to processed batches. Keep per-table feedback separate from global model
training and publish a versioned shared model artifact to all consumers.

Initial job policy: finite `activeDeadlineSeconds`, explicit memory/CPU requests and limits,
bounded retries (prefer application-controlled retries with Kubernetes `backoffLimit: 0`), and
`ttlSecondsAfterFinished` after diagnostic retention. Set actual sizes from representative
peak-memory tests; do not declare a universal 512Mi/1Gi budget without evidence. Grant only
the Job and log permissions needed by this runner. [Kubernetes Job lifecycle](https://kubernetes.io/docs/concepts/workloads/controllers/job/).

Do not return unbounded child stdout/results into the coordinator's memory. Stream bounded
logs and persist bulk output in the database. For a subprocess interim path, timeout handling
must terminate/reap the process tree; a timeout on `communicate()` alone does not kill it.
[Python subprocess documentation](https://docs.python.org/3/library/subprocess.html).

### Instrumentation and failure tests

Record task type, batch size, scanned rows/bytes, wall time, CPU seconds, peak RSS, cgroup memory,
retry count, cursor age and outcome. Use task type/outcome for metric labels; put table/job IDs
in logs or traces to avoid high-cardinality metrics. Separate anonymous memory and file cache
when diagnosing growth, and record OOM versus node eviction distinctly.

Required tests:

- Production-shaped large partition/history fixtures complete with bounded peak memory.
- Kill after output commit and before queue acknowledgment: replay produces no duplicate effects.
- Crash/timeout/OOM one analysis Job: coordinator keeps scheduling, cursor survives, retry backs off.
- Run two coordinator replicas or roll deployments: at most one owner commits each batch.
- Ten consecutive analysis cycles: coordinator post-cycle memory does not grow monotonically;
  Job resource usage remains inside its calibrated budget and all table pages eventually complete.
- Lease loss, slow DB, failed artifact publication and missing input data preserve old valid
  scores/models with explicit staleness instead of publishing partial results as current.

## 4. Finalization and zombie-run repair

### Do not erase the outage history

Persist the exact 20-run cohort as an incident membership set with full IDs and evidence query.
Do not label all old runs historical solely from their creation dates. Count remaining runnable,
deferred, executing, terminal and unresolved-external jobs per run. Keep their original reasons,
attempt timestamps and audit outcomes. The statement that grace behaved correctly is supported
by the sampled run, but still requires parent/child invariants across the cohort.

For every stale job, reconcile the external execution reference first. Never cancel or replay
an apparently stale job while an engine could still be mutating the table. Recover completed
receipts. Retry eligible unfinished intent with a linked attempt; mark genuinely expired scope
with a specific terminal reason. Retain original failures rather than rewriting them as success.

### Fix the lifecycle contract

The finalizer should only select explicit open run states in bounded pages. A terminal run's
`finished_at` is immutable; summary reconciliation should use a separate reconciliation timestamp.
Run terminalization and child admission must lock/check the same parent generation. A child
cannot become runnable under a terminal parent. Test competing finalizer, admission, retry,
and reconciliation transactions against PostgreSQL, not only SQLite.

Separate three concepts that are currently easily confused:

- **Attempt ended:** an engine failed or completed.
- **Intent still outstanding:** capacity/dependency deferral or a retry remains valid.
- **Run scope closed:** all required effects are terminal, explicitly transferred, expired,
  or cancelled under policy.

A six-hour timer must not silently convert outstanding recoverable work into an orphan failure.
Use a declared run deadline/intent expiry and reason-specific policy. When a run closes with
remaining recoverable work, transactionally attach it to a continuation with an explicit link,
or keep the run blocked; never leave unattached runnable children. Bound retry/defer lifetime so
this is not an instruction to keep every run alive forever.

Tests: finalization twice does not change timestamps; admission races cannot create orphaned
children; capacity deferral survives until policy expiry; expired work records one terminal
transition; old completed work is never redispatched; repeated reconciliation is idempotent.

## 5. Restore actual GB freed and useful work

Trace yesterday's zero through a single joined chain:

`eligible debt → planned effect → admitted job → engine execution → effect receipt →
audit outcome → daily aggregation → physical/billing confirmation`.

At each step report counts, oldest age, and reason for loss of coverage. Specifically compare:

1. Which tables had retention-eligible obsolete data or useful compaction opportunity?
2. Which plans included the operation, after preset/mode/capability exclusions?
3. Which jobs reached an engine versus waiting for capacity, dependency, approval or locks?
4. Which operations completed with measured bytes/files, which were valid no-ops, and which
   returned no trustworthy effect evidence?
5. Did successful receipts reach `AuditLog`, with the expected action/status, timestamp and unit?
6. Does the UTC daily window match “yesterday” in the report? Label UTC versus IST explicitly.

Keep unknown separate from measured zero. Add measured-outcome coverage and receipt lag to
the report. A compaction that reduces file count without shrinking live bytes can still improve
query cost. Conversely, metadata-only expiry does not establish physical deletion. Check native
expiry, durable Spark fallback and object-version retention as specified in the earlier cost plan.

Introduce explicit no-op categories: already satisfied, ineligible after fresh evidence,
unsupported, execution produced no evidence, and actual execution failure. Do not count every
zero-byte mutation as useful success, but do not count a legitimately already-satisfied operation
as an engine failure either. Preserve old records; add a versioned reason/classification and
report outcome counts transparently. Engine-specific golden tests must establish effect semantics.

Maintain a small representative canary cohort: mergeable files within partitions, retention-
eligible obsolete objects, delete-heavy data, and a healthy table expected to no-op. Verify row
correctness and protected snapshots, effect receipts, files removed and query behavior. An
arbitrary higher GB-freed target is not a reason to shorten retention or delete more data.

## 6. Metrics that demonstrate improvement

Do not change the existing health-score formula just to pass. Add context and companion metrics
without removing the unfiltered view.

| Metric | Definition / evidence | Proposed release gate |
|---|---|---|
| Scheduler reliability | Coordinator and analysis Job OOM/eviction, tick lag, task coverage | Zero OOM/eviction in a representative 48h soak and across all heavy cycles; no hidden disabled task. |
| New-work reliability | Fixed post-release creation cohort, outcome by operation/reason; pending age also shown | At least existing 90% target on sufficient representative completed executions; no passing on an empty/tiny sample. |
| Historical recovery | Exact incident cohort remaining jobs, unresolved engine refs, last terminal time | Zero unresolved/runnable incident work; immutable records preserved. |
| Aggregate reliability | Existing rolling finished-job view plus reconciliation/engine breakdown | Keep visible; document contamination rather than hiding it. |
| Queue health | Arrival rate, useful completions, oldest eligible age, blocked reason | Service rate catches arrival rate and oldest eligible work meets the agreed SLO. A mass failure is not successful draining. |
| Maintenance impact | Physically acknowledged bytes, file-count changes, delete burden and evidence coverage | Canary effects verified; missing data not zero; debt reduced relative to ingestion and policy. |
| Query value | Matched query CPU/input/requests/p95 latency | Improvement greater than maintenance overhead, without SLO regression. |
| Fleet health | Same table cohort, scan coverage/freshness, weighted debt and health grade | Debt declines without excluding unhealthy tables or changing denominators silently. |
| Cryo efficiency | Fully loaded cost/useful effect, unused share, resource-hours, node hours | Lower cost per useful work; provisional unused targets below 50%, then 30%, with headroom. |
| Net savings | Comparable baseline S3+Trino+maintenance cost minus observed same-scope total | Positive measured net savings over complete comparable billing windows; forecast separate. |

Report both **created-cohort** and **execution-time** metrics; each answers a different question.
New-work cohorts must include unresolved/pending counts to avoid survivorship bias. Distinguish
logical jobs, attempts, and audit actions rather than mixing them in a denominator. Version the
metric definitions and expose the cohort cutover, window, sample size, freshness and source.

Growing daily GB freed forever is not a sound steady-state goal. Once historical debt is gone,
reclamation may fall while the platform is healthy. Judge eligible debt age, retained physical
bytes, query efficiency and net cost together. A day with no eligible reclamation can be valid;
a day with eligible debt, no execution and no receipts cannot.

## 7. Ordered implementation tickets

| Ticket | Concrete change | Main files / components | Acceptance |
|---|---|---|---|
| R1 | Profile each background runner and add bounded task measurements | `cli.py`, scheduler runners, observability | Identify per-task memory/time and full-cycle coverage; no secrets in profiles/logs. |
| R2 | Durable analysis coordinator + bounded one-shot Job runner | `scheduler/`, CLI, DB models/migration, Helm/RBAC | Crash/lease/restart tests; one owner; coordinator survives child OOM. |
| R3 | Page table/partition scoring, checkpoint generations, incremental feedback | `planner/benefit_score.py`, `benefit_score_updater.py`, outcomes/model cache | More than 10k partitions covered; bounded peak memory; no lost feedback freshness. |
| R4 | Open-only/idempotent finalizer and atomic continuation contract | `run_finalizer.py`, admission/reconciliation, run/job repositories | Immutable finish time; no runnable child under a closed parent; race tests. |
| R5 | Incident reconciliation plus versioned cohort reporting | existing job/receipt reconciliation, `vitals.py`, reporting/API | Exact incident inventory drains safely; aggregate history retained. |
| R6 | Trace zero GB, fix receipt coverage and no-op classification | `impact_signals.py`, engine watchers, outcomes, daily report | Measured zero versus unknown; verified canary effects and correct daily window. |
| R7 | Capacity/dependency containment and right-sized execution | governor, native packing/routing, Spark profiles, Helm | No timeout reservation storm; unchanged data safety; lower useful-work cost. |
| R8 | Scorecard, query cohorts and billing reconciliation | cost tracking, reports, fleet trends, capacity operations | Auditable net result and readiness gates below. |

R1 instrumentation can land immediately. R2 and R3 are both required: neither a Job wrapper
around an unbounded sweep nor pagination inside an ever-growing coordinator is the full solution.
R4/R5 protect recovery; R6 cannot wait for historical failures to disappear because zero impact
is a separate signal. Reuse the earlier CR-01–CR-12 designs for routing, locking and savings proof.

## 8. Rollout, rollback and burn-in

1. Record image/config versions, baseline task memory, exact incident membership and metric
   definitions. Preserve historical values; do not reset charts.
2. Shadow-run bounded scoring on a small scope into a separate generation; compare values and
   partition coverage against the current implementation on bounded fixtures/canaries.
3. Disable the corresponding in-process runner before enabling its Job producer. Verify there
   are no duplicate entrypoints in `serve`, API startup, or scheduler. Keep last valid scores
   available during the cutover.
4. Expand only after memory/time and freshness gates pass. Pause new analysis dispatch on
   repeated failures; preserve the cursor. Do not automatically roll back into the known
   unbounded sweep. Use last-known-good scoring with explicit freshness while repairing it.
5. Reconcile incident runs in bounded batches, preserving locks and external execution evidence.
6. Verify real canary work and receipts, then observe a minimum 48h and at least three heavy
   analysis cycles without OOM/eviction or loss of scheduler progress. Test a rollout/restart.
7. Start the clean ten-day evaluation only when the readiness gate is recorded. For the legacy
   aggregate 24h rate, require 24h since the final incident terminal event. Start collecting the
   separate new-work cohort immediately rather than waiting to instrument it later.

Burn-in is event-gated, not “September 19 plus ten days.” If scheduler OOM or a new lifecycle
invariant violation occurs, the reliability clock resets after repair. Data correctness failure
halts affected mutation admission and triggers reconciliation. Billing confirmation may need a
longer comparable window than the operational ten-day evaluation; do not promise statistical
significance or a particular savings percentage by day ten.

## 9. Definition of completion

- [ ] Heavy work cannot take down the scheduling coordinator; bounded Job retry/cursor behavior is tested.
- [ ] Full partition/table coverage is measured and feedback remains fresh after restarts.
- [ ] Terminalization is idempotent and outstanding intent cannot become parentless.
- [ ] Exact legacy cohort is reconciled without deleting/relabeling failure history.
- [ ] Zero-reclamation signal is explained by traceable effects/coverage, not by success-rate arithmetic.
- [ ] Representative maintenance improves real debt/query behavior with correct row/snapshot results.
- [ ] Raw and cohort metrics reconcile; unknown, no-op, deferred and failed outcomes remain distinct.
- [ ] Clean operational soak and ten-day evaluation pass, followed by measured net cost evidence.

Completing this document is not completing these checks. The durable remedy is bounded,
recoverable work with verified effects—not a larger pod, a longer grace timer, or a greener dashboard.
