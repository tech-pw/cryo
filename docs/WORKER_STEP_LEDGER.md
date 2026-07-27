# Worker Step Ledger & Orphan Recovery

Architecture contract for write-ahead step tracking and worker-restart recovery.

## Roles

| Component | Responsibility |
|-----------|----------------|
| **Governor** | Sole admitter. May reap stale RUNNING jobs that have no adopt lease, no open started step, and no live engine activity. |
| **Worker** | Claims admitted jobs, executes statements, and maintains the write-ahead `job_steps` ledger. On each tick, runs orphan recovery *before* admitting new work via `process_next`. |
| **Native JobWatcher** | Owns live native dispatches. Recovery must not re-execute those jobs (`EngineOpState.NOT_APPLICABLE`). |

## Write-ahead ledger (`cryo.job_steps`)

1. **Before** engine execute: `begin_step` → status `started` (idempotent if already started/succeeded/skipped).
2. **On submit** (Spark/Trino query id): `set_step_engine_ref` (+ job `engine_ref`).
3. **After** execute: `complete_step` → `succeeded` / `failed` / `skipped`.

`jobs.current_step_index` tracks the active statement index.

## Quiet window (dangerous ops)

`expire_snapshots`, `remove_orphan_files`, and `optimize_manifests` require consecutive quiet probes before `begin_step`. Uncertain activity never clears the gate (especially orphan removal). Bare unit-test destinations with no Trino/Spark endpoints skip the probe.

## Orphan recovery

- Claims a short Redis job-owner lease (`cryo:job-owner:{job_id}`).
- Resolves open `started` steps via engine probes; does **not** admit new jobs.
- Dangerous + uncertain → wait (do not re-run).
- Succeeded engine → close step; failed → fail job and release locks.

See also: `cryo/worker/orphan_recovery.py`, `cryo/db/repositories/job_steps.py`.
