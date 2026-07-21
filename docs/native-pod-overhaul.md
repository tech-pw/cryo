# Native maintenance pod overhaul (2026-07-20)

## What changed

Native destinations with `k8s_dispatch=true` now create **Kubernetes Jobs on Governor admit**
(not inside the Worker subprocess path):

| Trigger | Pods |
|---|---|
| Single-table manual/scheduled | 1 Job sized from that table's stats + planned ops |
| Grouped run (N tables) | 1..N Jobs: hot/large tables dedicated; small/cold pack into shared pods (serial) |

Ops in scope: `expire_snapshots`, `remove_orphan_files`, `optimize` (rewrite).

## Resource sizing

- Module: `cryo/engines/native/resources.py`
- Inputs: scanner stats (`total_size_gb`, files, deletes, snapshots, …) + planned op set
- Peak mem across ops (not sum); CPU scales with table size / files (fill toward 16Gi)
- **Hard max memory request: 16Gi** (`MAX_POD_MEM_MIB`). Config may lower only.
- Go `compaction_memory_budget_bytes` stamped to ~75% of the pod request
- **Chunk size is not a preset knob.** After the pod is sized (and packed for groups),
  `chunk_size_gb_for_pod_mem(mem_mib, stats)` derives the planner `chunk_size_gb` so rewrite
  utilizes the Job's allocated memory. Shared multi-table pods use the dispatch mem for all members.
- **File-group size scales with the same pod budget.** `max_bytes_per_group_for_pod_mem`
  grows with chunk/pod. Hard floor = `target_file_size_bytes` (never shred below one
  output file). Packing floor = 2× target so the rolling writer amortizes remainder
  tails; rewrite mem/budget is bumped so `budget / planning_expansion` can hold that
  floor. Soft cap = `budget / 8` (hard cap 8 GiB). Go clamps stamped caps the same way
  (preserves explicit dense under-floor overrides). Run `3483a157` previously coalesced
  to 2 GiB under a 4× budget slack and peaked ~20 GiB RSS — packing floor is 512 MiB
  for a 256 MiB target, not 2 GiB.

## Packing

- Module: `cryo/engines/native/packing.py`
- Hot = rewrite + (mem near ceiling | size ≥ 500GB | hot partition files)
- Shared-pod request = `max(cpu)`, `max(mem)` of members (serial)

## Lifecycle

```
API/cron → queue → Governor gates → estimate pod → pack
  → derive chunk + max_bytes_per_group from pod mem → rebuild plan → create K8s Job
  → NativeJobWatcher finalize
```

Fallback when not in-cluster: Worker derives chunk / file-group caps from a stats-based
virtual pod, then subprocess.

Runtime Jobs use the Governor pod's namespace by default, inherit the Helm-configured
native image/service account through Governor environment, and read their payload from
an owner-referenced Kubernetes Secret. The watcher reconciles Jobs before the next
admission wave, preserves per-table outcomes for partial batches, and keeps table locks
held until a cancelled Job has been deleted.

## Schema

- `cryo.native_dispatches` — one row per K8s Job
- `cryo.jobs.dispatch_id`
- `cryo.chunk_checkpoints.table_id` (multi-table run isolation)

## Enable

1. Destination config: `k8s_dispatch: true` (and native engine)
2. Helm: `native.enabled: true` (RBAC for Governor)
3. Governor image includes `kubernetes` client; Job image includes `cryo-engine-go` + Python batch entrypoint

## Follow-up hardening

See `docs/native-dispatch-hardening-plan.md` for the durable outbox/state-machine,
result persistence, idempotent audit, and asynchronous cancellation work still required.
