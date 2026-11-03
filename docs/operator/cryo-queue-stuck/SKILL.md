---
name: cryo-queue-stuck
description: Explain a Cryo queue that is not draining. Use when jobs stay queued, deferred, or admitted.
---

# Cryo queue stuck

Decide whether work was never admitted, or was admitted and is waiting on a worker. Do not add capacity to clear a refusal.

Read the Governor and queue code (`cryo/governor/`, [cryo/queue/redis_queue.py](../../cryo/queue/redis_queue.py)). Where a doc and the code disagree, the code wins.

## Admit is not drain

- Admitted work is the Redis stream `{namespace}:admitted:stream`. The default namespace makes that `cryo:admitted:stream`. Consumer group is `workers` (`_ADMITTED_GROUP` in [cryo/queue/redis_queue.py](../../cryo/queue/redis_queue.py)).
- The old `cryo:admitted` list is not read.
- `admitted_depth` is `XLEN` minus the group's pending count. `XLEN` counts entries not yet acknowledged. Pending entries are ones a worker already holds. Bare `XLEN` over-counts. What remains is work no worker has started.
- A worker runs one job at a time. Admitting against engine slots alone queues work no worker can reach. Worker presence comes from the consumer group, not from a replica count.
- A waiting healthy backlog is not a failed dispatch. `stuck_admitted_no_dispatch` is not raised anymore. It names a 2026-09-21 bug.
- An unacknowledged entry can be taken over after `_CLAIM_HANDOFF_IDLE_MS` (120 seconds). That window only covers claim to `mark_running`. Past `RUNNING`, recovery is `reconcile_stale_running_jobs`. Do not look for a log line that says the previous worker never finished. That sentence is not what the code logs.
- The chart's Redis keeps no persistence. A restart drops the stream, the `workers` group, job hashes, pending zsets, in-flight counters, and table locks. The next consumer-group command gets `NOGROUP`, so the queue recreates the group (`MKSTREAM`, from id `0`), logs `Consumer group workers on ... is gone`, and retries once. A worker does not need a restart. Postgres-`ADMITTED` jobs come back two ways. On its next tick, worker orphan recovery re-enqueues any job whose hash is gone, and the following governor wave admits it again. If no worker is running, `reconcile_stuck_admitted_jobs` returns it to `QUEUED` after `stuck_admitted_after_seconds` (900 by default). In-flight counters re-derive from Postgres every 5 minutes.

## Nine gates, in this order

`Governor._gate_job` in [cryo/governor/governor.py](../../cryo/governor/governor.py):

```text
paused → abac → intent → approval → pii → admission → roi → table lock → qpp
```

`decide_job_admission` is only the admission gate. The table lock is the only gate with a side effect a later refusal must undo. QPP runs after the lock.

The approval defer reason is the string `Destructive operation requires approval`. Approval is not a function of time. Requeueing does not grant it. Those jobs never reach the admitted stream, so more worker replicas cannot clear them. Name the gate in the defer reason. Do not tell the user to wait.

## Refuse

- Do not add a periodic full-table job list. Unbounded scans of queued, running, or admitted jobs can OOM the fleet.
- Do not raise worker replicas to clear an approval, ROI, paused, or guardrail refusal.
- Do not treat "needs approval" as a stuck worker.
- Do not run the refused maintenance, and do not set `force=true`.
