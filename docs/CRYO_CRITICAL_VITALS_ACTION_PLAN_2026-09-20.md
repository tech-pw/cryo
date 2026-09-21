# Critical vitals: incident diagnosis and recovery plan

Prepared 2026-09-20 from the user's latest snapshot and local source inspection.
**Status: planned, not deployed.** Snapshot observation time and billing day were not supplied;
the numbers below are reported evidence, not a fresh production measurement.
This incident plan supplements the [implementation follow-up review](CRYO_RECOVERY_FOLLOWUP_REVIEW_2026-09-20.md).

## What the snapshot establishes—and what it does not

| Signal | Reported observation | Interpretation / next evidence |
|---|---|---|
| Vitals | Critical, 12/100 | Several operational problems coexist. Raising the score alone is not the goal. |
| Success | 1,908 succeeded / 59,673 failed in 24h | 3.10% of succeeded+failed jobs succeeded. Attribute failures by reason, run age and execution stage before choosing a remedy. |
| Idle allocation | $1.07 unused / $0.23 used, reported 4.7x | Using rounded values, total is $1.30 and unused share is about 82.3%. Establish billed day, completeness and workload breakdown before extrapolating. |
| Stuck work | Two runs >12h, oldest 20h; 32 stale jobs | Determine whether these are the prior incident cohort or new runs. Inspect ownership, actual engine work and progress. |
| Repeated failure | 89 `orphaned_catalog_entry` / `NoSuchKey` | Verify whether 89 means events or distinct tables and confirm the window. Current health check aggregates failures over three days. |
| Scan coverage | 2,187 / 12,601 stale beyond 168h | About 17.36% stale; about 82.64% fresh under that seven-day check. Seven-day freshness is not necessarily sufficient for high-ingestion tables. |
| Scan failure | 83 tables | Establish overlap with stale tables and missing-metadata tables; do not add the populations together. |
| Small files | Accumulation exceeds maintenance removal | Separate files created by ingestion from useful compaction, and prioritize measured query benefit. |
| Impact | 0.0 GB yesterday, -100% WoW, null DoD | Could be missing evidence rather than physical zero. Compare measurement coverage and engine mix before accepting the direction. |

**59,673 failures do not mathematically explain zero reclaimed bytes.**
`vitals.py::_success_rate_pulse` counts terminal jobs by `finished_at`.
`impact_signals.py::_daily_impact` sums successful reclaim audit rows' byte fields separately.
Failures could reduce useful maintenance, but that causal link needs evidence. The previous
scorecard traced many failures to old outage runs; that does not prove the latest failures
have the same origin. Missing Trino byte receipts were independently identified earlier.

The historical scorecard statement that legacy failures drag GB-freed down should not be
used as a diagnosis. Preserve that history, with this correction linked to it.

## P0: establish one reproducible incident ledger

Owner: orchestration/backend for job evidence; reporting for metric windows; platform for
deployment and cost provenance. These are proposed responsibilities, not assignments to named people.

Capture one UTC observation time `T`, running image digest, chart revision, effective flags,
and the exact query windows. Query a read-only transaction with statement timeout and bounded
detail retrieval. Use an existing read replica if available; do not export full job payloads.
Record the aggregate counts and a small redacted set of representative IDs in the scorecard.

1. Reproduce the reported succeeded/failed counts on `[T-24h,T)`. Note that the current vitals
   function has a lower bound only; the investigation should fix an upper bound for repeatability.
2. Group all failures by normalized error, run creation day, job creation day, destination,
   and hour of finalization. Count distinct runs and tables as well as jobs. Include an
   unclassified bucket so totals reconcile exactly; never classify every old run as incident-related.
3. For the largest groups, join existing job-step receipts/dispatch identity and record
   whether execution started, was rejected before admission, or was closed by reconciliation.
   A job row is not necessarily one engine attempt; count retries and attempts separately.
4. Join confirmed incident-cohort membership. Keep candidate, confirmed, excluded and
   unclassified members distinct. Compare all jobs, confirmed historical incident jobs, and
   fresh runs created after the recorded recovery cutover.
5. Trace the two stuck runs and all 32 stale jobs to their last progress, child status,
   lease, dispatch reference and actual engine state. Establish overlap rather than double-counting.

Illustrative aggregate, using the current schema; bind `:as_of` to the captured UTC time and
validate the query plan before use on the production job table:

```sql
SELECT date_trunc('hour', j.finished_at) AS finished_hour,
       date_trunc('day', r.created_at) AS run_created_day,
       CASE
         WHEN j.error LIKE 'orphaned_deferred:%' THEN 'orphaned_deferred'
         WHEN j.error LIKE '%stuck_admitted_no_dispatch%' THEN 'stuck_admitted_no_dispatch'
         WHEN j.error LIKE '%NoSuchKey%' THEN 'NoSuchKey'
         ELSE 'other_or_unclassified'
       END AS reason,
       count(*) AS failed_jobs,
       count(DISTINCT j.run_id) AS distinct_runs,
       count(DISTINCT j.table_id) AS distinct_tables
FROM cryo.jobs j
LEFT JOIN cryo.runs r ON r.id = j.run_id
WHERE j.status = 'failed'
  AND j.finished_at >= CAST(:as_of AS timestamptz) - interval '24 hours'
  AND j.finished_at < CAST(:as_of AS timestamptz)
GROUP BY 1, 2, 3
ORDER BY failed_jobs DESC;
```

This is an investigation query, not executed evidence. Expand `other_or_unclassified` until
the material failure sources are understood. Compare current error normalization with
`health_check.py::_check_repeat_failures` to avoid treating different error families as one.

**Exit:** every headline total has a timestamp, window and denominator; the largest failure
groups have traceable causes. Do not defer recovery of verified stuck work while waiting for
every minor group to be classified.

## P1: stop new lifecycle failures and drain verified old work

| Observed condition | Repair | Required proof |
|---|---|---|
| Parent finalized before child requeue | Finish F09: parent eligibility and child transition share a transaction/locking protocol; reconcile queue publication with durable state. | Forced finalizer-versus-requeue interleaving cannot leave runnable work under a terminal parent. |
| Admitted job has no dispatch | Reconcile dispatch intent, external identity and receipts; make dispatch retry idempotent. Requeue only after absence of execution is established. | Lost API response and worker crash do not create duplicate engine writes. |
| Engine is still executing | Restore watcher/ownership reconciliation and preserve the operation; do not declare it dead from row age alone. | Engine completion reaches the correct durable attempt and parent. |
| Lease expired, no active execution | Use existing orphan recovery after receipt/engine checks; record the recovery reason once. | Repeated reconciliation is idempotent and cannot move the original terminal timestamp. |
| Only capacity-deferred children remain | Check whether capacity can recover within the run's deadline; retain correct defer semantics and explicitly resolve expired intent. | No repeated mass failure caused by parent/child state disagreement. |
| All children genuinely terminal | Finalize once, preserving first `finished_at`; maintain accurate summary counts. | Two finalizers and concurrent child writers satisfy lifecycle invariants. |

Do not delete failures, bulk-mark jobs succeeded, extend grace indefinitely, or restart all
work without reconciling running engines. These actions can improve apparent vitals while
increasing cost or duplicating maintenance.

**Exit:** two reported stuck runs have evidence-backed dispositions, each stale job has an
owner/next action, and fresh-run orphan failures stop. The aggregate rolling rate may remain
bad until old terminal events age out; preserve it and show fresh-work reliability alongside it.

## P1: contain repeated NoSuchKey work safely

Inspect the exact missing object class: catalog metadata pointer, manifest, or data file.
Distinguish an obsolete registration from a valid table whose required object is missing,
and distinguish missing-object errors from authorization/catalog outages. Read the current
catalog pointer before making a classification. Missing metadata is not permission to delete
the table, drop the catalog entry, or remove more S3 objects.

Extend existing table failure/admission mechanisms with a persisted retry-after and reason:
confirmed unchanged missing metadata should not trigger every scheduled maintenance cycle.
Permit a bounded verification probe; resume after verified repair or metadata-location change.
Scope transient dependency backoff to the affected catalog and preserve healthy catalog work.
Surface held tables explicitly so suppressing retries cannot conceal lost coverage.

Relevant code: `cryo/engines/base.py` missing-metadata classification,
`cryo/governor/native_admit.py`, `cryo/db/repositories/sources.py`, and scanner failure paths.
Existing `_evolving` cleanup is a narrow historical mechanism, not a generic fix for these
89 events. If reusing its sibling check, require matching source/catalog as well as schema and
name: the current helper's inspected predicate does not include source equality. Keep unrelated
scoped-out cleanup out of this incident change.

**Tests:** unchanged missing metadata is not repeatedly dispatched; changed pointer gets a
bounded recovery probe; transient authorization errors are not mislabeled as deletion; a
same-named table in another source cannot justify deactivation. **Exit:** repeated attempts
on unchanged confirmed-broken metadata cease, with an auditable repair queue.

## P1: make the north-star signal truthful, then measure useful maintenance

For yesterday and the WoW/DoD baseline days, count successful eligible actions, non-null byte
receipts, measured zeros, unmeasured actions, engine mix and receipt freshness. Do not assume
the week-old value used the same engine or measurement method.

Finish F10 from the follow-up review: total reclaim is null when unmeasured, partial results
are labeled measured subtotals, and incompatible coverage cannot produce -100% or “worsening.”
Update every Slack table cell and serialized consumer. Preserve a genuinely measured zero.
`no_actions` is meaningful only when telemetry itself is fresh and complete.

Restore receipts where the executing engine supports verifiable evidence. For Trino paths
without byte evidence, use bounded asynchronous verification and explicit estimates, not
invented successful-action bytes or full-bucket scans. Keep logical obsolete bytes, physical
deletion and billed storage reduction separate; retain snapshots/branches and configured
retention protections. Compaction may improve queries before old files become deletable.

**Exit:** report fixtures cover complete, partial, unmeasured, true zero and stale telemetry;
impact comparisons use compatible evidence. A positive number is not required to pass honesty
checks, but measured useful outcomes are required to claim the cost-saving objective is working.

## P2: recover scanner coverage and useful compaction throughput

Build one table inventory crossing stale/fresh, scan failures, catalog health, scan queue age,
recent successful maintenance, query demand and ingestion rate. Prioritize the 83 failing
tables by shared cause, then healthy but unscheduled/unclaimed/stalled scan work. Inspect
scanner queue claims/leases, CronJob or dispatcher activity, completion persistence and
autoscaler behavior against the actual deployed revision.

Run a bounded catch-up queue with per-catalog concurrency and retry caps. Do not submit
2,187 simultaneous scans or raise all replicas while dependencies are unhealthy. Preserve
oldest-first fairness while giving high-ingestion/query-critical tables a tighter freshness
budget. Explicitly label quarantined tables; do not silently remove them from the denominator.

Use precomputed scan data to rank maintenance by small-file burden, query frequency and
expected benefit after execution cost. Preserve partition filters, GB-budgeted chunks,
governor admission and off-peak/cooldown policies. For comparable table cohorts, measure
files introduced by ingestion, files removed by successful maintenance, bytes/CPU per user
query and maintenance spend. Raw fleet file count alone cannot identify ineffective compaction.

The newly present `trino_hot_*` modules are separate working-tree work and were not audited
by this plan; their existence is not evidence that production prioritization has changed.

**Exit:** recoverable stale backlog declines each day, healthy tables meet their policy's
scan deadline, repeated scan failures are contained, and targeted hot-table query efficiency
improves without increasing foreground query latency or violating retention.

## P2: lower Cryo's own cost after containing wasted work

Use the latest complete billed day, not an empty current day. Break used/unused cost down by
api, scheduler, governor, workers, scanners, analysis, native/Spark and the empty workload-name
bucket. Capture requests/limits, replicas, active working-set/CPU percentiles, pending time,
OOM/evictions and completed useful work. Append newly verified daily figures to
`docs/cost-tracking/daily-cost-log.csv` before reporting them.

The supplied $1.07/$0.23 values have no billing date in this request, so they are preserved
here without fabricating a dated daily-log row. They are not enough to choose new requests.

First stop retry storms and dependency-blocked Pod reservations. Then tune scanner idle
floor/scale-down and worker floors against queue age and admitted work, and size dispatch
Pods against successful workloads. Governor concurrency remains the real engine cap.
Do not reduce scheduler memory limits while its growth is unresolved. Close F02–F08 before
enabling analysis Jobs; the flag remains off. Additive isolation code is not an OOM fix until
the actual bounded execution path is verified and deployed.

Canary one resource or replica lever at a time. Compare absolute cost and unused share,
useful work per dollar, fresh-run reliability and latency/freshness against a recorded baseline.
Check whether freed requests permit node consolidation: allocation improvement alone does
not guarantee a lower AWS invoice. Restore prior sizing if OOMs, queue age or user latency
regress. Do not claim success merely because the unused/used ratio falls as active spend rises.

## Delivery sequence and acceptance record

| Order | Deliverable | Gate to proceed |
|---|---|---|
| 1 | Timestamped failure/receipt/cost provenance and exact stuck-work inventory | Headline totals reproduced; historical versus fresh failures distinguished |
| 2 | Safe lifecycle recovery, missing-metadata retry containment, truthful impact output | No duplicate execution; known broken objects stop consuming repeated work; unknown is not zero |
| 3 | Finish outstanding ownership/coverage/dispatch fixes from follow-up review | Isolated PostgreSQL fault tests and offline manifest/reconciliation tests pass; staging runtime verified |
| 4 | Controlled scanner catch-up and query-aware maintenance canary | Coverage and useful throughput improve within engine-load constraints |
| 5 | Capacity canaries and matched economic evaluation | Lower cost per useful work unit, stable reliability, comparable-period net benefit |

Record each change's source revision, deployed digest, exact configuration, canary scope,
before/after window, observation completeness and rollback result. Do not pick a target vitals
score by changing penalty weights or filtering out unresolved failures.

Proposed readiness gates: zero fresh lifecycle-invariant violations, zero scheduler OOMs
through at least a full scheduling cycle and the subsequent clean evaluation, no unexplained
stuck work, improving recoverable scan backlog, and credible impact/cost evidence. Confirm
numeric latency/freshness budgets against existing service policy before rollout. Retain the
recovery plan's ten-day evaluation and 24-hour legacy-event washout for the aggregate view.

The economic outcome must include S3 storage/request changes and workload-adjusted Trino
compute savings minus Cryo and maintenance overhead over the same period. A 12→90 score
improvement without useful maintenance and net savings does not meet Cryo's objective.

## Review scope

Source inspection only: vitals, impact, health checks, existing source cleanup and previous
recovery reviews. No live failure queries, cluster changes, new cost measurements or tests
were executed for this documentation change. The investigation SQL is a proposed query,
not a claim that the 59,673 failures have already been traced.
