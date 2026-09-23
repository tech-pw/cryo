# Redis was on a ~31-day fuse — 2026-09-23

Cryo's in-namespace Redis had no mechanism, anywhere, for removing a key. Not a slow leak
under some rare condition: the ordinary success path left a key behind every time a job
finished. Left alone it fills its memory limit and the kernel kills it, taking the queue with
it, on a loop measured in weeks.

This is the root cause and the fix. It is separate from the delivery-semantics work in
[`QUEUE_SCANNER_GOVERNOR_OVERHAUL_2026-09-22.md`](./QUEUE_SCANNER_GOVERNOR_OVERHAUL_2026-09-22.md),
which changed *how* jobs are handed to workers; this changes how long the evidence of them
sticks around.

---

## The measurement

Taken from the live `cryo-redis` pod on 2026-09-23:

| | |
|---|---|
| `dbsize` | 22,552 keys |
| `used_memory` | 33,308,624 B (31.8 MB) |
| `uptime_in_seconds` | 160,303 (44.5 h) |
| container limit | 512Mi (536.9 MB) |
| `maxmemory` | **0** — no ceiling |
| `maxmemory-policy` | `noeviction` |
| `appendonly` | `no` |
| `dir` | `/data`, on `/dev/nvme1n1p1` |

Redis had been up 44.5 hours and held 31.8 MB, having started empty — there is no volume, so
every restart begins from nothing. That is **17.1 MB/day**, which reaches a 512Mi limit in
about **31 days**.

Three details turn "grows" into "loses the queue":

1. **`maxmemory 0`** means Redis never pushes back. It does not reject writes or evict; it
   keeps allocating until the cgroup limit is hit and the kernel OOMKills the process.
2. **`/data` is the container's ephemeral layer.** `df` reports the node disk, and there are
   no PVCs in the namespace (`kubectl get pvc -n cryo` → none). The 10 MB `dump.rdb` sitting
   there could never be read back after a restart. Redis looked persistent and was not.
3. **`appendonly no`**, so even within one container's life the fallback was a snapshot up to
   an hour stale.

## Where the growth came from

Two key families, neither bounded.

**`cryo:job:<id>` — 22,522 of the 22,552 keys, all with `TTL -1`.** `complete()` is the
terminal transition, and it did this:

```python
pipe.hset(self._job_key(job_id), "status", JobStatus(status).value)
```

It writes the final status *into* the hash and moves on. Nothing deletes it, then or ever —
the only `hdel` in the module removes the `stream_id` field, not the key. Postgres had 631,067
jobs at the time of writing, created at 10k–115k/day; every one of them that ran while a given
Redis process was alive left a 192-byte hash behind permanently.

**`cryo:run:<id>:events` — one per run, also `TTL -1`.** `MAX_STREAM_LEN = 500` caps how large
a single run's buffer gets (observed 4.7 KB–251 KB), but nothing capped how many buffers
accumulate. One key per run, forever.

## The fix

### Expiry, in two horizons

`cryo/queue/redis_queue.py` now sets a TTL on the job hash at every write, with two values:

| | Value | Applied at |
|---|---|---|
| `_ACTIVE_JOB_TTL_SEC` | 7 days | `enqueue`, `admit`, `mark_running`, `defer`, `return_to_pending`, `requeue_for_continuation` |
| `_TERMINAL_JOB_TTL_SEC` | 1 hour | `complete` |

The active horizon is a safety net that every transition refreshes, so a job stays addressable
for as long as it is genuinely in play. It is sized against **queue** time, not run time:
`reconcile_stale_running_jobs` reaps a RUNNING job after 6h, but a QUEUED one can wait far
longer behind a backlog — 3,046 jobs once sat queued for 21+ hours. A week clears that with
room to spare while still bounding the leak.

The terminal horizon is what actually shrinks the steady state, because completions are the
overwhelming majority of keys. Once `complete()` runs, no further transition is possible, so
the key only has to outlive readers already holding the id.

**Expiring too early is safe, by design.** Postgres is the source of truth and both recovery
paths rebuild from it: `reconcile_queued_jobs` re-enqueues a DB-QUEUED job missing from Redis,
and orphan recovery re-admits a DB-ADMITTED job whose hash is gone (`worker/orphan_recovery.py`,
`if queue.status(job.id) is None`). Neither inspects a job already terminal in Postgres, which
is the only kind the one-hour horizon can reach. `defer` deliberately keeps the *active*
horizon — deferred means "come back on a later wave", not finished.

`cryo/queue/run_events.py` gives each run's stream a 24-hour sliding expiry, refreshed on
publish. Safe because that stream is only the live-tail/replay buffer for the UI — every event
is also written to the observation lakehouse, which is the archive.

### A ceiling that fails loudly

`maxmemory 320mb` with `maxmemory-policy noeviction`. With TTLs in place the steady state is a
few MB, so reaching this means something started leaking again — and it should surface as
bounded write errors rather than a kernel kill that loses everything. Held well under 512Mi
because RSS runs above `used_memory` (36.8 MiB vs 31.8 MiB measured) and the allocator does not
return freed pages promptly.

`noeviction` rather than an LRU policy is deliberate: these keys are jobs and locks. Silently
evicting one loses work with no record. Refusing a write is recoverable; dropping it is not.

### Stop pretending to persist

`save ""` disables RDB snapshotting. It bought no durability — the file was written to a disk
that cannot survive the pod — while costing real I/O and a fork that can transiently double RSS,
which is the worst thing to do next to a memory limit.

Verified against a real `redis:7-alpine` rather than trusting the flag syntax, since a
mis-parsed `--save` would silently keep snapshotting:

```
maxmemory          335544320
maxmemory-policy   noeviction
save
appendonly         no
```

## Tests

`tests/test_queue_key_expiry.py`, using fakeredis because TTL bookkeeping is the behaviour
under test (the hand-rolled double in `test_redis_maintenance_queue.py` models no expiry at
all). The load-bearing one is
`test_no_job_hash_survives_a_full_lifecycle_without_an_expiry`: it drives jobs through every
transition the queue supports and asserts no `cryo:job:*` key is left immortal, so a new write
path that forgets to refresh the TTL fails there rather than in a month of production growth.

## What was deliberately not done

**No PVC.** Giving Redis real persistence is a genuine option, but it is a different decision
with its own cost: an EBS RWO volume pins the pod to one availability zone, where today it can
reschedule anywhere instantly. The architecture already tolerates Redis loss on purpose — the
reconcile paths above exist precisely for it — so durability here buys a faster recovery, not
correctness. Worth taking deliberately rather than folding into a leak fix.

## What to watch

- `dbsize` and `used_memory` should now plateau within a day or two instead of climbing
  linearly. A steady state of a few MB and a few thousand keys is expected.
- `cryo:job:*` keys with `TTL -1` should be zero for jobs created after the deploy. Keys
  written before it keep no TTL — they are not rewritten — so either let the next restart clear
  them or delete them explicitly.
- The stale `cryo:workers` zset is a leftover from the heartbeat the Streams work removed
  (2026-09-22). Nothing reads it; safe to delete.
