# Governor fixes and operation-aware job dedupe — 2026-10-22

Outcome of a review of how the governor decides how many and which queued jobs run in parallel,
and whether it notices several queued jobs maintaining the same table. Part A fixes defects in the
existing wave and reconcile path. Part B stops the queue from filling with redundant jobs.

## Part A — governor and reconcile fixes

| # | Problem | Fix |
|---|---------|-----|
| 1 | Re-enqueue paths (`reconcile_queued_jobs`, `reconcile_deferred_jobs`, orphan recovery, intent reconciler, force-admit) enqueued at priority 0. `fifo_priority` scores are negative epoch seconds, so 0 outranked every fresh job and a re-enqueued job jumped the queue each wave. | Every enqueue uses `fifo_priority(job.run_created_at)`, so a job keeps its run's age. |
| 2 | The Redis in-flight counter only moved at admit/complete/return, so any other way out of ADMITTED/RUNNING leaked a slot until capacity drifted to zero. `defer()` after admit never returned its slot. | `defer()` decrements when the prior status was admitted/running. The governor rebuilds each destination's counter from Postgres (`SQLJobStore.in_flight_counts`) on a 5 minute timer (`_maybe_rebuild_in_flight`). |
| 3 | `cli._reconcile_orphaned_table_locks` released the lock of an ADMITTED job. | Releases only when the job is not RUNNING or ADMITTED. |
| 4 | `reconcile_deferred_jobs` judged re-queue with a default `AdmissionConfig` and a partial job, and re-queued jobs that the gate chain would refuse again. | It uses the destination's real config and the real admission job (`admission_config_from_destination`, `admission_job_from_record`). A job of unknown cost under a fleet budget stays deferred unless forced. The governor passes `_still_blocked_predicate`: table lock, pause, intent not ready or expired, pending approval. |
| 5 | Wave, purge and reaper scans grew with backlog. | `pending_by_destination(limit=2000)`, `pending_depth` for the metric, one batched `job_statuses` lookup per destination, `list_running(limit=500)`, `list_admitted(limit=500)`. |
| 6 | `_gate_abac` returned the deferred sentinel without deferring, so the job stayed at the head of the queue and was refused every wave. | It calls `_defer(job, reason)`. |

## Part B — operation-aware dedupe

### Rules

- A job's **effective operations** are, in order: `jobs.operations` (set when dedupe trimmed it),
  the run summary's explicit `operations`, else everything its mode can plan
  (`MODE_OPERATIONS[mode]` in `cryo/planner/guardrails.py`).
- `Preset.operations` is **not** consulted. It does not bound the plan, so using it would claim
  coverage the job never delivers. For the same reason there is no `PresetConfig.operations`
  field on the scheduler side.
- A new request is reduced with `uncovered(requested, active_jobs)` (`cryo/planner/job_coverage.py`):
  operations an active job (queued, admitted, running, deferred) already covers are skipped.
  Nothing left → the table is skipped. Something left → a **trimmed** job whose `operations` is
  the remainder.
- `jobs.dedupe_key` is the canonical (CORRECT_ORDER) comma-joined effective operations.
  `ux_jobs_active_table_ops` is a partial unique index on `(table_id, dedupe_key)` for active
  statuses with a non-null key. It is the race backstop; the application check is the primary
  filter. A NULL key exempts a job.

### Where it applies

| Entry point | Behaviour |
|-------------|-----------|
| `POST /maintenance/run` and `POST /tables/{id}/maintenance/operations` (`start_maintenance_run`) | Skips covered tables and steps. `skipped` in the response and `run.summary["dedupe"]` (`tables_skipped`, `steps_skipped`, `details[:200]`) report them. 409 `already_queued` when every table is covered. `force: true` creates the jobs anyway with a NULL key. Scheduled-for-later runs are exempt. |
| Cron presets (`build_scheduled_run`) | Filters against fleet-wide active coverage after the cooldown filter and **before** the `budget_hint` cut, so a covered table does not spend a slot. Coverage is read once per tick and updated as each preset's run is persisted. A failed coverage read disables dedupe for that tick. |
| `SQLSchedulerStore.persist_run` | Writes `operations` and `dedupe_key`; a job that loses the unique-index race is dropped (savepoint) and not enqueued. |
| Worker finalize and native watcher success | `supersede_covered_jobs` marks queued/deferred duplicates `skipped` with `error = superseded_by:<id>` and an `audit_log` row (`job_superseded`), then drops their Redis entries. Only jobs created no later than the winner started, with a non-null key and operations that are a subset of what succeeded, are superseded. |

### Migration `20261022_1200`

Adds `jobs.operations` and `jobs.dedupe_key`, backfills keys for active jobs, retires existing
duplicates, then builds the unique index `CONCURRENTLY`. Of each `(table, key)` group the job
furthest along survives (running > admitted > queued > deferred, then oldest run). Extra queued or
deferred jobs become `skipped` / `superseded_by:<id>`. Extra admitted or running jobs cannot be
stopped safely, so they only lose their key. The effective-operations rules are inlined in the
migration; keep them in step with `job_coverage.py` if the mode table changes.

### Known trade-off

A trimmed job can run before the job covering the table's other operations, so the canonical
order (compact first, clean up after) is guaranteed **within** a job but not **across** jobs. A
trimmed `expire_snapshots` may run before another job's `optimize` finishes, and that cleanup then
waits for the next cycle. Accepted: the alternative is blocking a request behind a job that may
sit queued for hours.

### Maintenance notes

- `MODE_OPERATIONS` is an upper bound, pinned by `test_build_plan_never_emits_operations_outside_mode_operations`.
  Add any new operation type to the right mode there.
- Other job creators (batch coordinator, retries) leave `dedupe_key` NULL: they are never blocked
  by the index, but their jobs still count as coverage for everyone else.
- Web: the Run dialog and the batch page expose "Run even if already queued" (`force`), and the
  success toast lists skipped steps.
