# Worker Step Ledger & Orphan Recovery

Architecture contract for write-ahead step tracking, durable maintenance intent, and
worker-restart recovery.

## Roles

| Component | Responsibility |
|-----------|----------------|
| **Governor** | Sole admitter. May reap stale RUNNING jobs that have no adopt lease, no open started step, and no live engine activity. |
| **Worker** | Claims admitted jobs, executes statements, and maintains the write-ahead `job_steps` ledger. On each tick, runs orphan recovery *before* admitting new work via `process_next`. |
| **Native JobWatcher** | Owns live native dispatches. Recovery must not re-execute those jobs (`EngineOpState.NOT_APPLICABLE`). |

## Durable safety kernel

When `durable_intents_enabled=true`, an acknowledged job is bound to an immutable,
versioned `cryo.maintenance_intents` row before execution:

- the canonical plan and SHA-256 plan hash;
- actor, tenant, source, table, destination, operation scope, and observed snapshot;
- policy/approval bindings and expiry;
- cumulative input/output/file/partition/duration/deletion budgets;
- capability requirements for every destination that can execute an effect, including
  SQL fallbacks;
- a containment action and semantic idempotency key.

`cryo.maintenance_fences` provides a monotonic epoch per table. Every
`cryo.maintenance_attempts` row carries that epoch; a superseded worker is rejected
before it can submit another effect. The feature remains default-off until deployment
rollout and production-shaped recovery evidence pass.

### Effect receipts

Every external effect is write-ahead recorded in `cryo.effect_receipts`:

1. `prepared` before submission;
2. `submitted` with the destination reference when known;
3. `succeeded` or `failed` only after a definitive result;
4. `unknown` when the submission may have happened but the outcome is ambiguous.

Receipt identity includes the intent, step, operation, destination, and effect strategy.
An existing receipt may be reused only when that complete contract matches. Unknown
effects are never blindly replayed.

Native durable plans submit one operation per engine invocation. SQL fallback executes
one rendered statement at a time. This is required so persisted cumulative budgets can
contain follow-on effects after every observed result, including after a worker restart.

## Write-ahead ledger (`cryo.job_steps`)

1. **Before** engine execute: `begin_step` → status `started` (idempotent if already started/succeeded/skipped).
2. **On submit** (Spark/Trino query id): `set_step_engine_ref` (+ job `engine_ref`).
3. **After** execute: `complete_step` → `succeeded` / `failed` / `skipped`.

`jobs.current_step_index` tracks the active statement index.

The step ledger is the job-facing execution timeline. Durable intents and effect
receipts are the authoritative safety boundary for what was approved and what may have
happened externally; the ledger must never be used to infer that an ambiguous effect is
safe to replay.

## Quiet window (dangerous ops)

`expire_snapshots`, `remove_orphan_files`, and `optimize_manifests` require consecutive quiet probes before `begin_step`. Uncertain activity never clears the gate (especially orphan removal). Bare unit-test destinations with no Trino/Spark endpoints skip the probe.

## Orphan recovery

- Claims a short Redis job-owner lease (`cryo:job-owner:{job_id}`).
- Resolves open `started` steps via engine probes; does **not** admit new jobs.
- Dangerous + uncertain → wait (do not re-run).
- Succeeded engine → close step; failed → fail job and release locks.

## Intent reconciliation

`IntentReconciler.run_once()` performs a level-triggered sweep of submitted/unknown
receipts:

- probe-capable effects wait while running or uncertain, then resolve from the engine;
- manual-reconciliation effects remain blocked for operator attestation;
- stale/superseded intent results never requeue the job;
- a current resolved intent returns the job to the normal Governor queue, so pause,
  fencing, load, fairness, and capacity checks run again before execution.

Operators inspect and attest ambiguous outcomes through
`/api/v1/maintenance/safety/effects/{id}/resolve`. The resolution reason and optional
evidence reference are audited.

## Pause and fair admission

Generation-stamped pause state is durable in `cryo.maintenance_control_state` at global,
tenant, and destination scope. The Governor checks every effect destination (including a
fallback), and the Worker checks again immediately before submission. Global changes
require an admin; tenant and destination controls are tenant-scoped.

The Governor considers a bounded candidate window and round-robins it by tenant before
admission. This prevents a deep queue from one tenant from monopolizing every available
slot without making worker replica count the concurrency control.

See also: `cryo/worker/orphan_recovery.py`, `cryo/db/repositories/job_steps.py`.
