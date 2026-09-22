# Top-500 daily maintenance overhaul (2026-09-22)

Cryo exists to run four Iceberg maintenance operations reliably. On 2026-09-21 it was running
them on roughly nothing while reporting tens of thousands of failures a day. This document
records what was actually wrong, what changed, and how to tell whether it worked.

The target after this overhaul is deliberately narrow: **`optimize`, `optimize_manifests`,
`expire_snapshots`, `remove_orphan_files` on the 500 most-queried Trino tables, every day, on
Spark-on-K8s only.** Everything else is switched off until that is boringly reliable.

---

## 1. What was actually broken

Evidence from the live fleet (12,605 active tables, `cdp` catalog) before any change:

| Signal | Value |
|---|---|
| `/api/v1/vitals` score | 12/100, CRITICAL |
| 24h job outcome | 2,062 succeeded / 20,159 failed (9.3%) |
| Sampled run `db567500` | 6,117 jobs: 161 succeeded, **5,952 failed `stuck_admitted_no_dispatch`** |
| `engine_ref` on those failures | null — no engine ever touched the table |
| Oldest stuck run | 113h |

The 9.3% success rate was not an engine problem. It was arithmetic.

**Root cause: admission was capacity-aware about engines and completely blind about workers.**

The governor admits jobs against live Trino/Spark capacity (`slot_cap`, `per_replica_cap`,
adaptive budget). Nothing in that path knows how fast workers drain `cryo:admitted`. A worker
processes one job at a time; at the KEDA ceiling of 10 replicas and a p50 job duration of 284s,
the real fleet throughput is about **3,000 jobs/day**. Five enabled presets were each fanning
out across the whole fleet inside a four-hour window, producing runs of 3,478–6,117 jobs.

Then the reaper finished the job. `reconcile_stuck_admitted_jobs` marked anything ADMITTED for
more than a hardcoded 15 minutes as permanently FAILED — including jobs that were sitting
correctly in the queue waiting their turn. Failing them released their in-flight slots, which
let the governor admit the next batch, which also aged out. A death spiral that manufactured
~20k failures/day out of a fleet that was merely oversubscribed.

Three further defects were hiding behind that noise:

- **The top-500 mechanism was dead code.** `TrinoHotPresetRunner`, `top_trino_query_tables`, and
  `repin_trino_hot_tables` all exist and work. The preset `daily-trino-hot-tables` was never
  seeded in production, so the runner logged a warning and returned, every 24h, forever.
- **One failed operation killed the other three.** The worker broke out of its statement loop on
  the first error, so a table whose `expire_snapshots` failed never got `remove_orphan_files`,
  and the job was recorded as a flat FAILED with no indication that `optimize` had succeeded.
- **`default-native-deep` omitted `optimize_manifests` entirely** — a preset can silently not
  request an operation, and nothing measured the difference.

---

## 2. What changed

### 2.1 Bound the work to the capacity that exists

`build_scheduled_run` has always accepted a `budget_hint`. The cron path never passed one.

- New setting `CRYO_MAX_JOBS_PER_SCHEDULED_RUN` (default **500**), applied in
  `SchedulerService._scheduled_run_budget`. A preset can raise or lower it via
  `guardrails.max_jobs_per_run`; `0` means uncapped, for deliberate one-off backfills.
- Candidates are ranked by `apply_priority_order` *before* the slice, so the cap keeps the
  highest-value tables rather than an arbitrary prefix.

### 2.2 Make admission throughput-aware

Workers now heartbeat into a Redis zset (`cryo:workers`, 600s retention) on every tick.
`Governor._worker_drain_headroom()` computes
`max(0, active_workers × CRYO_WORKER_DISPATCH_BACKLOG_PER_REPLICA − admitted_depth())` once per
wave and spends it across destinations.

Two deliberate choices:

- Headroom only applies to destinations that dispatch **via the worker queue**. Native K8s
  dispatch bypasses workers entirely, so throttling it on worker count would be wrong.
- Zero heartbeating workers returns `None` (unknown), not `0`. During a mixed-version rollout
  the old workers don't write heartbeats, and wedging admission fleet-wide on a signal that is
  merely absent is worse than the behavior being fixed.

### 2.3 Stop manufacturing failures

`reconcile_stuck_admitted_jobs` was rewritten:

- **Skips jobs still on the claim list.** `queue.awaiting_claim(job_id)` distinguishes "queued,
  waiting for a worker" (healthy, leave it alone) from "dispatch was genuinely dropped". This
  alone removes the bulk of the 20k daily failures.
- **Requeues instead of failing.** A genuinely dropped dispatch goes back to pending with its
  original FIFO priority, up to `CRYO_MAX_DISPATCH_ATTEMPTS` (default 3), tracked in the new
  `cryo.jobs.dispatch_attempts` column (migration `20260922_0055`). Only after exhausting
  attempts is it FAILED.
- **The timeout is configurable** — `CRYO_STUCK_ADMITTED_AFTER_SECONDS`, default 900s, was a
  hardcoded constant.

### 2.4 Per-operation isolation in the worker

The statement loop no longer aborts the job on the first failure. Failures are collected per
operation in `failed_actions`; remaining operations still run. The job resolves to PARTIAL if
anything succeeded, FAILED only if everything failed, with an error string naming each failed
operation (`expire_snapshots: <detail>`).

The exception to this is deliberate: when a receipt was already issued, or the engine reports
`outcome_unknown`, the job goes PARTIAL and **breaks**. An operation whose effect on the table
is unknown must not be followed by another operation on the same table.

### 2.5 Make the top-500 selection actually produce 500 tables

- `top_trino_query_tables` falls back to aggregating `TableQueryActivity.query_count` when
  `cryo.query_logs` yields nothing. This matters on this deployment specifically: the Iceberg
  query-log ingest path (`aggregate_from_iceberg_query_log`) writes no `query_logs` rows at all,
  so the primary ranking source may legitimately be empty. The result carries a `source` field
  so the fallback is visible rather than silent.
- `repin_trino_hot_tables` raises `EmptyHotRankingError` instead of reconciling to an empty set.
  An empty ranking previously unpinned the entire cohort — the worst possible response to a
  transient ingest gap. The runner logs and keeps yesterday's pins.
- Repin now **takes over tables pinned to a different preset** by this same job.
  `TableAssignment.table_id` is the primary key, so one assignment exists per table fleet-wide;
  without this, a table pinned to a legacy preset could never join the hot cohort. Operator pins
  (`assigned_by` not the repin job) are left alone and counted in `skipped_operator_pins`.

### 2.6 Hard-gate orphan removal on shared S3 prefixes

`remove_orphan_files` deletes files under a table's location that the table's metadata doesn't
reference. If two tables share an S3 prefix, one table's live data is the other table's orphan.
Cryo already detects this (`cryo/anomalies/paths.py`, persisted daily as
`SharedS3AnomalyReport`) but nothing consumed the finding on the execution path.

`gate_orphan_removal_on_shared_location` now drops `remove_orphan_files` from the plan for any
table in the latest report, with a note explaining the remedy (give the table a unique location,
then re-enable). The lookup is cached in `JobStore` for 300s. This is a hard gate, not a warning:
the failure mode is silent, irreversible data loss.

### 2.7 Measure what was promised

New `cryo/reporting/maintenance_coverage.py` + `GET /api/v1/maintenance-coverage`.

Job success rate cannot answer the operator's actual question. It cannot distinguish "optimize
ran and expire_snapshots didn't" from "nothing ran", and a preset configured without
`optimize_manifests` looks identical to one where every manifest rewrite succeeded. Coverage is
measured per `(table, operation)` from the audit trail:

- `fully_covered_pct` — share of the cohort where **all four** operations were covered. The
  strictest reading, deliberately: per-operation averages can look healthy while no individual
  table ever receives the full set.
- `untouched_tables` — cohort members maintenance never reached. This is the number that moved
  during the incident.
- `SKIPPED` counts as covered. "Below the small-file threshold" means the operation was
  correctly evaluated and found unnecessary — that is the intended outcome, not a gap.

---

## 3. The target configuration

`scripts/seed_trino_hot_preset.py` now seeds:

| Field | Value | Why |
|---|---|---|
| name | `daily-trino-hot-tables` | matches `CRYO_TRINO_HOT_PRESET_NAME` |
| destination | Spark-on-K8s (`spark-k8s-fallback`) | zero added load on production Trino |
| mode | `deep` | `remove_orphan_files` and `optimize_manifests` are deep-only in `build_plan` |
| operations | all four | |
| params | `min_small_files: 10` | planner default of 128 is too coarse for a curated cohort |
| guardrails | `cooldown_hours: 24`, `max_jobs_per_run: 500` | |
| schedule | `0 19 * * *` (00:30 IST) | off-peak; was `0 2 * * *`, which is 07:30 IST |

**Capacity check.** 500 tables × 4 operations at p50 284s ≈ 39 worker-hours ≈ 4h on 10 workers.
At p90 (1,156s) it is ~16h, which still fits a daily cycle but leaves no slack — worth watching
once real numbers land. The previous configuration was roughly 20× over capacity.

---

## 4. Live findings that fix the rollout order (verified 2026-09-22)

Checked against production before writing the rollout, because two of these change it:

| Check | Result |
|---|---|
| `cryo.query_logs` rows, `engine='trino'`, 7d | **0** (536 rows total, all older/other engines) |
| `cryo.table_query_activity` rows | 54,380 |
| Top-500 ranking from the fallback | 500 tables; #1 = 3,375 queries/7d, #500 = 75 |
| Latest `SharedS3AnomalyReport` | 1,294 groups, **2,594 tables** fleet-wide |
| Top-500 tables that hit the orphan gate | **22** (4.4%) |
| Currently enabled presets | 5, all fleet-wide, all in a 16:00–20:45 UTC window |

Two consequences:

**The `TableQueryActivity` fallback is not a safety net here, it is the only working path.**
`query_logs` has zero Trino rows, so the primary ranking returns nothing. Without §2.5 the
top-500 preset cannot select a single table — and the old repin behavior would have responded
to that empty ranking by unpinning the entire cohort.

**Nothing can be seeded before the code ships.** Running `seed_trino_hot_preset.py` against the
currently-deployed code would create a preset whose first repin sees an empty ranking, and 22
of its tables would run `remove_orphan_files` with no shared-prefix gate. The seed script now
takes `--disabled` so it can be staged, but the ordering below is not optional.

---

## 5. Rollout

1. ~~**Disable the five legacy presets**~~ — **done 2026-09-22**, via
   `PATCH /api/v1/presets/{id}` as admin so each change is audited.
   `claude-cold-table-fleet-optimization`, `claude-spark-run-unified-bq-gold-dbt-pw-bq`,
   `default-native-deep`, `spark-large`, `spark-medium` are all `enabled=false`; **0 enabled
   presets remain**. Done ahead of the deploy because it needs no code: it stops the fleet-wide
   fan-out that the reaper converts into ~20k failures/day. Post-change queue state was
   **0 queued, 0 admitted, 51 running** — the backlog drained rather than re-firing, confirming
   nothing else was scheduling work. Cost of this step is the ~2,000 successful jobs/day those
   presets landed, against a fleet whose small-file ratio was rising anyway.
2. **Apply migration `20260922_0055`**, then deploy. Order matters — `dispatch_attempts` is read
   on the reconcile path, so the column must exist before the new governor/worker start.
3. **Seed with `--disabled`** and let one repin cycle run. Confirm `ranking_source` in the repin
   log reads `table_query_activity` (expected here, not an error) and eyeball the cohort.
4. **Enable** `daily-trino-hot-tables`.
5. **Watch for three days**: `GET /api/v1/maintenance-coverage` (expect `fully_covered_pct`
   climbing, `untouched_tables` near zero, and `remove_orphan_files` coverage sitting ~4% below
   the others — that is the gate, not a failure), `/api/v1/vitals` success rate, and the count
   of `stuck_admitted_no_dispatch` failures (expect ~0).

---

## 6. What this does not fix

- **The 2,250 tables unscanned in 168h.** Scan freshness is a separate path (`cryo-scanner-worker`,
  HPA 3–8) and is untouched here. Stale stats degrade planner decisions for the hot cohort too.
- **p90 job duration of 1,156s.** Four operations on a large table taking ~19 minutes is the
  binding constraint on how far the cohort can grow beyond 500. Not investigated.
- **Spark fallback resource sizing is still one static value for every table**, unlike the native
  engine's per-table sizing. Known cost gap, tracked in `AGENTS.md`.
- **Tables gated out of orphan removal get no remediation path.** They are logged and skipped;
  nothing yet reports them for the location fix that would re-enable the operation.
