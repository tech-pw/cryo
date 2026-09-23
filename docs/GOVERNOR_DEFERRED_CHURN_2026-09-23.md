# The governor was re-queueing 24 jobs it could never run — 2026-09-23

Every governor wave, for hours, produced the identical line:

```
Wave complete: admitted=1, deferred=24, blocked=1, orphaned=0, re_enqueued=24
```

`admitted` moved around (0–7, which is just real work draining against worker capacity).
`deferred=24, re_enqueued=24` never moved at all — same 24 job ids, wave after wave, each one
deferred with *"Destructive operation requires approval"*.

## Why it looped

The wave applies nine gates in order:

```
paused → abac → intent → approval → pii → admission → roi → table lock → qpp
```

`reconcile_deferred_jobs` is what returns a deferred job to the queue, and it made that
decision by re-running exactly one of those nine:

```python
decision = decide_job_admission(admission_job, config, now=current)
if not decision.admissible and decision.defer:
    continue
...
queue.enqueue(job.id, job.destination_id)
store.mark_status(job.id, JobStatus.QUEUED)
```

For a job refused by `_gate_approval` — which sits *before* admission — admission has no
opinion, so it says yes, the job goes back to QUEUED, and the next wave's approval gate
refuses it again. Nothing in that cycle can terminate, because approval is not a function of
time.

Postgres shows the split cleanly. Of 181 deferred jobs:

| Reason | Count | Can waiting change it? |
|---|---|---|
| `deadline_near` | 153 | yes |
| `Destructive operation requires approval` | 24 | **no** |
| `cooldown` | 3 | yes |
| `table_locked` | 1 | yes |

At a measured ~39 s wave that is ~2,250 waves/day, so roughly **108,000 job-status writes a
day**, plus the matching Redis operations and two log lines per job per wave, all of it
achieving nothing.

## Why the obvious fix is wrong

The tempting change is "don't re-queue approval-deferred jobs". That would be a worse bug
than the one it fixes.

A DEFERRED job is **not in the pending zset**. The gate chain only ever runs over jobs the
wave iterates, and the wave iterates the pending zset. So `reconcile_deferred_jobs` is the
only thing that puts a deferred job back where `_gate_approval` can look at it again — which
means the churn *is* the mechanism by which approving a job eventually lets it run. Remove it
and every approved job is stranded permanently.

The loop is not "it re-queues too much". It is "it re-queues on a signal unrelated to why the
job stopped".

## The fix

`reconcile_deferred_jobs` takes an optional `still_blocked` predicate. When it answers yes,
the job is left alone, with no status write and no queue traffic. The governor supplies one
that asks the approval question the same way `_gate_approval` does — via
`prefetch_approved_operation_keys` and `batch_job_requires_approval`, memoised per table over
a single session for the whole pass.

Three properties worth stating, because each one is a way this could have gone wrong:

- **Ordering.** The predicate is consulted *after* the admission arithmetic, so a job
  admission would defer anyway never reaches a database lookup. Pinned by
  `test_admission_still_filters_first_so_the_check_stays_cheap`, which asserts both that the
  approval job reached the predicate and that the cooldown job did not — otherwise the test
  would pass vacuously if the loop never ran at all.
- **Approval still releases the job.** The moment the approval exists the predicate answers
  no and the job is re-queued on the next wave, exactly as before. This is the half that
  matters most, and it has its own test.
- **Failure direction.** A lookup that raises is treated as "not blocked". Wrongly answering
  yes would park a job until a human noticed; wrongly answering no costs one wasted round
  trip. The cheap mistake is the one it makes.

Optional, so the in-memory and test paths keep the previous behaviour unchanged.

## Scope

Only the approval gate is wired up, because approval is the only gate observed churning in
production — `paused`, ABAC and PII deferrals were all zero. The hook is general, so the
others can be answered the same way if they ever show up; the reasons they produce are
dynamic strings, so enumerating them speculatively would have been guesswork.

## Related, found in the same session

Deploying this revealed a separate problem. The `helm upgrade` pre-upgrade migration hook
(`ADD COLUMN cryo.tables.last_scan_error_kind`) hung for 13 minutes and failed the release at
revision 199. It was not slow — it was queued behind two transactions `cryo-scheduler` had
left idle-in-transaction for ~60 minutes, and because a blocked `ACCESS EXCLUSIVE` request
also blocks every reader that arrives after it, **17 sessions piled up behind the ALTER**.

Terminating the two leaked backends let the migration finish instantly. `migrations/env.py`
now sets `lock_timeout = '10s'`, so a blocked migration aborts instead of taking the table
down with it — verified against a real `postgres:16` by holding a transaction and watching
the ALTER cancel at exactly 10s.

The underlying leak is still open: `run_benefit_score_update` holds a single transaction
across a per-table loop over a 12k-table fleet, so Postgres sees `Client/ClientRead` on that
session for minutes at a time. `idle_in_transaction_session_timeout` on the database is `1d`,
which is no protection at all. Worth fixing at the source — committing between units of work —
rather than only defending the migration path.
