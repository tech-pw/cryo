# Async Spark dispatch (opt-in)

Issue [#435](https://github.com/tech-pw/cryo/issues/435). Setting:
`CRYO_WORKER_ASYNC_SPARK_DISPATCH` (default `false`).

## Problem

A worker runs one job at a time. On a Spark-on-Kubernetes destination it submits the Spark
Job and blocks in `wait_for_completion` until the Job ends. The fleet drains at about
`replicas / (submit + Spark duration)`. The governor admits against Spark slots, so
admitted jobs queue for a worker while the Spark slots sit idle. In the measured case,
8 workers and 30 slots gave about 160 jobs an hour, and admitted jobs waited a median
215 s for a worker.

## Design

With the setting on, a worker submits a Spark statement and moves on. The job is
**parked**, not finished:

| Held while parked | Why |
|---|---|
| Job status `running` | Reapers and the lock reconcile leave it alone. |
| In-flight slot | The governor keeps counting the Spark Job against `slot_cap`. |
| Table lock | Released only when the job ends, never at submit. |
| Step ledger row `started`, with the Spark Job name | The record of what is in flight. |

The parked state lives in Redis (`cryo/queue/async_spark.py`):

- `cryo:async-spark:state`, a hash of job id to step index, the Spark state (Job name,
  submit time, deadline, retry counters), the owning worker and the job's start time.
- `cryo:async-spark:due`, a sorted set of job id to next check time.
- `cryo:async-spark:owner:<worker>`, the jobs one worker has in flight, for its cap.

Flow:

1. **Submit.** The worker runs the statement loop as usual (lock check, ABAC, quiet-window
   preflight, `begin_step`), then calls `SparkK8sEngine.execute_async`. That submits the
   Spark Job, reports its name through the same `on_engine_ref` callback, and returns a
   `SparkAsyncState`. The worker parks the job and returns.
2. **Resolve.** Each governor-leader tick, `resolve_parked_spark_jobs`
   (`cryo/governor/async_spark_resolver.py`) reads up to `CRYO_ASYNC_SPARK_RESOLVE_BATCH`
   due entries, oldest first. It renews the table lock and asks Kubernetes for the Spark
   Job's state. A running Job is checked again after `CRYO_ASYNC_SPARK_POLL_INTERVAL_SECONDS`.
   When the Job has ended, or passed its deadline, or a retry backoff is over, the
   resolver hands the job back with `requeue_for_continuation`. It does not record
   outcomes.
3. **Resume.** A worker claims the job, takes the job-owner lease and calls
   `execute_async` with the stored state. That returns the same `ExecResult` `execute()`
   would have returned, and the worker records it through the same code: audit row, step
   ledger, run events. It then goes on to the next statement, which parks again, or
   finishes the job: final status, `release_job_claims`, supersede, job metrics.

`execute()` and `execute_async()` share one state machine (`SparkK8sEngine._run`). Retries
on catalog errors, driver OOM and executor OOM are the same. The async path persists
the retry counters and parks through a catalog-retry backoff instead of sleeping. A lost
commit is retried in place `CRYO_WORKER_COMMIT_CONFLICT_RETRIES` times as on the blocking
path, again parked for the backoff.

## Invariants

- **Order.** Statements run in plan order, and statement *n+1* is submitted only after
  statement *n* resolves. A resumed job uses the plan saved on the job row, so step
  indexes cannot shift under the ledger. A step that failed in an earlier pass is not
  re-run; it rebuilds the same `failed_actions` entry, so the job ends `partial` or
  `failed` exactly as the blocking loop would say.
- **One resumer.** The resuming worker holds the job-owner lease. A duplicate hand-back
  finds it taken and does nothing.
- **Restart.** A worker restart loses nothing: the state is in Redis and the Spark Job
  keeps running. A handed-back job that no worker finishes resuming is handed back again
  after `CRYO_ASYNC_SPARK_RESUME_GRACE_SECONDS`. Orphan recovery skips parked jobs, so it
  never closes a step without the audit row the resuming worker writes. A worker that
  dies between submit and park leaves a `started` step with no parked state; orphan
  recovery adopts it as it does for a blocking worker.
- **Bounded.** The resolver reads a capped, oldest-due-first slice of a sorted set. No
  job-table scan.
- **Cap.** `CRYO_WORKER_ASYNC_SPARK_MAX_IN_FLIGHT` (default 4) Spark Jobs per worker.
  Past it, the worker runs the statement blocking, so a full fleet still makes progress
  and resumes are never starved.
- **Turning it off.** Workers stop parking new statements but still resume parked jobs,
  and the resolver keeps running, so nothing is stranded.

## Scope

- Spark-on-Kubernetes (`SparkK8sEngine`) only, including a native destination redirected
  to its Spark fallback. Trino, EMR Serverless and native jobs are unchanged.
- Jobs with a durable intent always block. Their attempt and effect-receipt lifecycle is
  one worker pass.
- The governor's own native SQL-fallback dispatch (`fallback_sweep.py`) is a separate,
  already-async path and is unchanged.

## Sizing

Fleet drain becomes about `replicas × max_in_flight / Spark duration`. Keep
`replicas × max_in_flight` at or above the Spark `slot_cap`; the governor still bounds real
Spark concurrency.
