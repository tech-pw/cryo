# Queue, Scanner and Governor overhaul — 2026-09-22

Three defects, one theme: each subsystem was making a decision on a signal that could not
support it. The queue guessed whether a job had been lost, the scanner picked tables by
alphabet, and the governor sized admission against a worker count that read zero whenever the
fleet was busy. This is what changed, and what deliberately did not.

---

## 1. Scanner — 75% of every run was discarded

### What was wrong

`deterministic_chunk(items, chunk_id, num_chunks)` keeps `index % num_chunks == chunk_id`.
The CLI defaults to `--chunk-id 0`, and `Settings.scan_num_chunks` defaults to `4`. The
CronJob ran **one** pod with **no arguments and no chunk environment variables**, so every run
took chunk 0 and dropped the rest. The last run's summary showed `assigned` at about a
quarter of `discovered`.

Second, subtler problem: candidates came back in catalog order
(`source.name, schema_name, table_name`). Since a run cannot finish the fleet, catalog
order meant *which* tables got scanned was decided by the alphabet. Some tables were scanned
every day while others were scanned once a week, and a slice of the fleet drifted past 30
days without a scan.

### What changed

| File | Change |
|---|---|
| `deploy/helm/cryo/templates/scanner-cronjob.yaml` | `completionMode: Indexed`, `completions`/`parallelism` = `scanner.chunks`, and `--chunk-id $(JOB_COMPLETION_INDEX) --num-chunks <chunks>` passed explicitly |
| `deploy/helm/cryo/values.yaml` | New `scanner.chunks: 4` drives completions, parallelism **and** `--num-chunks` from one value so they cannot drift apart |
| `cryo/scanner/run.py` | New `order_by_staleness()`, applied before chunking |

Ordering is stalest-first with never-scanned tables ahead of everything, falling through to
catalog order so it stays total and chunk assignment stays reproducible. An unfinished run is
now self-correcting: whatever it misses is nearer the front of the next one. Same idea as
Amoro's `balanced` scheduling policy.

Round-robin chunking over a stalest-first ordering also spreads the backlog evenly across the
four pods rather than piling it into one — pinned by
`test_stale_tables_spread_across_chunks_rather_than_piling_into_one`.

### Still open

The per-run failure rate is unexplained and untouched here, as
is the unexplained drop in daily coverage after Sep 7.

---

## 2. Redis queue — at-most-once delivery

### What was wrong

`admit()` did `RPUSH cryo:admitted`; `claim_admitted()` did a bare `LPOP`. Once popped, the
only record of the job was the claiming worker's memory. A pod dying before it wrote RUNNING
to Postgres dropped the job permanently: the row stayed ADMITTED with no `engine_ref` and no
owner, invisible to every reconciliation pass.

The repair for that was `reconcile_stuck_admitted_jobs`, which had to *infer* loss from age
plus a missing `engine_ref`. That inference is wrong, because the same description fits a job
merely queued behind more work than the fleet can drain. One run failed most of its jobs as
`stuck_admitted_no_dispatch` while workers were healthy and still working through the same list.

### What changed

Admitted jobs now travel over a Redis Streams consumer group (`cryo:admitted:stream`, group
`workers`). Prod Redis is 7.4.10; Streams are already used elsewhere in the codebase
(`cryo/events/redis_stream.py`, `cryo/telemetry/query_buffer.py`, `cryo/queue/run_events.py`).

**Acknowledgement happens at `mark_running`, not at completion.** That is the design decision
everything else follows from. It keeps the group's pending list covering exactly the
claim → RUNNING handoff, which gives the recovery paths non-overlapping ownership:

| Failure | Owner |
|---|---|
| Worker dies between claiming and starting | Consumer group — `XAUTOCLAIM` redelivers after 2 minutes |
| Worker dies after starting | `reconcile_stale_running_jobs` + orphan recovery (unchanged) |
| The entry is gone outright (Redis restart, no persistence) | `reconcile_stuck_admitted_jobs` (rewritten, below) |

Because the window is only the handoff, the reclaim threshold can be 2 minutes instead of
having to exceed the longest possible maintenance operation.

Two signals fall out of the same structure for free:

- **`admitted_depth()`** = `XLEN − group pending`. Entries are deleted at ack, so this is
  exactly "admitted work no worker has started yet" — the backpressure signal the governor
  needs, rather than a list length that also counted work in progress.
- **`active_worker_count()`** = consumers that polled recently **or hold an unacknowledged
  entry**. This replaced an explicit heartbeat zset, which could not work: a worker runs one
  job at a time and does not poll again until it finishes, so presence measured by polling
  alone read zero exactly when every worker was busy — making the governor's drain-rate cap
  silently inert under the load it exists to handle.

Consumer names derive from the pod name, so a restarted container rejoins as itself and picks
its own unacknowledged entry back up instead of waiting to be reclaimed.

### Deleted as a consequence

| Thing | Why it is gone |
|---|---|
| `cryo:workers` heartbeat zset, `record_worker_heartbeat`, `_WORKER_HEARTBEAT_RETENTION_SEC`, the `cryo/cli.py` heartbeat call | The consumer group already tracks this, and tracks it correctly |
| `jobs.dispatch_attempts` column, `record_dispatch_attempt`, `Settings.max_dispatch_attempts` | Bounded a retry loop that existed only because the old signal was ambiguous. Migration `20260922_2100` |
| Most of `reconcile_stuck_admitted_jobs` | ~120 lines of inference became ~40 lines around one exact question |

`awaiting_claim()` is now an exact test (does the stream still hold this job's entry) rather
than a heuristic, so the remaining reconcile has no guessing left in it.

### Rollout note

The old `cryo:admitted` **list** key is not read any more. Jobs sitting in it at deploy time
will be ADMITTED in Postgres with no stream entry, which is precisely the case
`reconcile_stuck_admitted_jobs` now handles — the next governor wave returns them to QUEUED
and they get re-admitted onto the stream. No manual intervention needed; delete the stale
`cryo:admitted` key afterwards to reclaim the memory.

---

## 3. Governor — a 622-line function, and non-deterministic admission

### 3a. `run_wave` decomposition

`run_wave` was 622 lines nested ten levels deep. The two things a reader actually needs —
the order the gates apply in, and which counter each branch bumps — were impossible to see at
once.

| | Before | After |
|---|---|---|
| `run_wave` | 622 lines, depth 10 | 44 lines, depth 2 |
| Largest function in the file | 622 lines | 107 lines (`_admit_for_destination`), depth 3 |

The gate chain is now a flat, ordered sequence in `_gate_job`: `paused → abac → intent →
approval → pii → admission → roi → table lock → qpp`. Order is the point. Cheap local checks
run first, then the ones needing the database, then admission arithmetic, and the table lock
last — because it is the only gate with a side effect a later refusal has to undo. QPP is the
one gate after the lock and therefore owns releasing it. A reordering that moved the lock
earlier would silently start leaking locks on refusal, which
`test_a_job_deferred_by_a_gate_does_not_hold_a_table_lock` now catches.

Supporting types introduced:

- **`_WaveTally`** — wave totals and their per-destination breakdown incremented together.
  Previously two hand-maintained counters at each of ~20 branches, where updating one and
  forgetting the other made the metric and the returned `WaveResult` disagree about the same
  wave.
- **`_WorkerDrainHeadroom`** — fleet-wide claim-queue headroom, shared across destinations so
  one destination cannot spend it twice. Distinguishes *exhausted headroom* (block the whole
  destination) from *no engine capacity* (counted per candidate), which the inline version
  conflated.
- **`_DestinationWave`** — everything the gates need for one destination, resolved once.

Behaviour is unchanged. 12 new tests in `tests/test_governor_wave_structure.py` pin the parts
that had no coverage, on top of the 17 existing `run_wave` tests.

### 3b. Admission was non-deterministic

A Thompson-sampling bandit was resampling `per_replica_cap`, `high_load_ratio`,
`high_load_fraction`, `default_cooldown_hours` and `min_benefit_score` **on every wave**, with
no flag to turn it off. Two real problems:

1. The same fleet state could admit a different number of jobs each wave — the worst possible
   property for a system being debugged, and it fights the new drain-rate cap directly.
2. Recording the signals it learned from ran **per job**: a load-modify-save of a single
   Postgres row (`GovernorBanditState`), so a wave deferring 800 jobs meant ~1,600 round trips
   and 800 commits contending on that one row — plus a read-modify-write of a node-local JSON
   file per admission that multiple governor replicas cannot keep consistent.

Both halves are now behind `Settings.governor_bandit_tuning_enabled`, **default false**.
`_admission_config` returns what the operator configured and opens no database session; the
three `bandit_feedback` entry points return immediately. The knobs it tuned are ones the
operator already sets explicitly per destination.

Not deleted — the opt-in still works and the Intelligence UI still reads the state. Pinned by
`tests/test_governor_deterministic_admission.py`.

---

## What was deliberately NOT deleted

Worth being explicit, because these looked like dead code and are not:

| Module | Lines | Why it stays |
|---|---|---|
| `governor/roi_gate.py` | 191 | `roi_gate_enabled=true` in prod with `roi_gate_shadow_mode=true`. It is deliberately collecting shadow data toward an enforcement decision; deleting it discards an experiment in progress |
| `governor/native_admit.py` | 1,257 | `native_engine_enabled=false` in prod, but this is the dispatch path for the 57k-line Go engine and has live API surface. Removing it is a product decision, not a refactor |
| `governor/qpp.py` | 391 | `qpp_enabled=false` and never enabled in prod, but it has API routes, metrics, a migration and frontend surface (`web/src/pages/Intelligence.tsx`, generated OpenAPI types). A cross-stack removal, not a governor cleanup |

Each is a decision to take deliberately rather than fold into this change.

---

## Verification

- `ruff check cryo tests`: 192 errors, against 193 pre-existing (one fixed, none added).
- `mypy cryo`: 34 errors, exactly the pre-existing baseline, none in touched files.
- `pytest`: full suite green in both fixed and random order, except the two
  `tests/test_fleet_trends_api.py` failures that also fail on a clean checkout.
- Migration `20260922_2100` applies and round-trips (`alembic downgrade -1 && upgrade head`).
- `helm template` renders the scanner CronJob with `completions: 4`, `parallelism: 4` and
  `--num-chunks 4`.

### What to watch after deploy

1. Scanner: `assigned` across the four pods should approach `discovered`, not one chunk of it.
   Scan freshness past 30 days should fall.
2. Queue: `stuck_admitted_no_dispatch` should disappear entirely as an error string — it no
   longer exists. Reclaim events log as "Reclaimed admitted job … its previous worker never
   started it" and should be rare.
3. Governor: admitted-per-wave should stop varying for an unchanged fleet state, and the
   governor's Postgres connection count per wave should drop by roughly one session per
   destination plus one per deferred job.
