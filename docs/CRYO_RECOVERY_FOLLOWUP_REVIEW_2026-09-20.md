# Recovery follow-up review: fixes landed, remaining release gates

Reviewed 2026-09-20 against `9e015aff` plus the current uncommitted working tree.
This updates the status of [the original F01–F12 review](CRYO_R1_R6_REVIEW_AND_NEXT_STEPS_2026-09-20.md).
The original findings remain historical context; this document is the latest review status.
No application code, production state, or deployment was changed by this review.

## Decision

Keep analysis Jobs disabled. The fixes improve the implementation, but **F02/F06/F07 are
not the only remaining blockers**. F03/F04/F05/F08/F09/F10 remain partially addressed,
and the original F12 database-isolation requirement remains open.

Do not label this “9 of 11 P1 findings closed.” The supplied list contains nine touched
findings, including F11, which was P2; touching a finding is not satisfying its acceptance
criteria. In particular, the implementation itself explicitly documents F05 as incomplete.

The reported 146-test run is contributor-provided evidence, not independently reproduced
here. This review independently ran 91 targeted tests successfully. Passing tests establish
the tested behaviors; they do not establish crash safety, isolated test execution, or readiness
to enable the Job path.

## Updated finding status

| Finding | What is now supported by source inspection | Remaining scope / disposition |
|---|---|---|
| F01 | Production `run_scheduler` constructs `BenefitScoreUpdaterRunner`; old standalone loops removed. | Entrypoint bypass fixed. Separate coordinator polling from sweep cadence and persist due time under F06. Actual chart/Job integration still pending. |
| F02 | No runtime-contract repair found. | Open: explicit release image, database/config references, service account, pull configuration, placement and RBAC wiring. |
| F03 | `yield_task` returns clean bounded exits to pending. | Partial: crashes after the pre-batch heartbeat still strand running tasks; release does not invalidate the old token. |
| F04 | Heartbeat/progress/finish use conditional SQL; progress and heartbeat advance the token; sequential reset no longer starts at zero. | Partial: reset still uses an unfenced ORM write, yielded tokens remain usable, expiry is not checked by writes. |
| F05 | Pre-batch heartbeat rejects an already superseded token before starting more work. | Partial: no heartbeat during a long batch; scoring commits before the fence/checkpoint. |
| F06 | Coordinator still dispatches random names and persists the reference afterwards. | Open: durable attempt reservation, name reconciliation, retry/due scheduling and task-specific claim. |
| F07 | Ordered per-table pages remain in place. | Open: full coverage across scan changes, per-page score normalization and premature fleet completion. |
| F08 | Remaining budget persists in `task_state` across successful checkpoints and clean yields. | Partial: crash window can repeat paid work without debiting the durable budget; cross-fleet fairness remains unresolved. |
| F09 | Finalizers lock parent rows with `FOR UPDATE SKIP LOCKED`. | Finalizer-versus-finalizer repair present. Parent-finalization versus child requeue invariant remains open. |
| F10 | Four measurement states exist and some payloads/narrative expose them. | Partial: partial totals still drive trend percentages; numeric zero remains in unmeasured output cells/payloads. |
| F11 | Membership defaults to candidate; seed description says candidate; window includes explicit UTC offsets. | Candidate-label/timezone repair present. Actual member confirmation and confirmed-only reporting remain operational follow-up. |
| F12 | SQLite fixture restores shared metadata defaults on teardown. | Useful additional test-order fix, but original shared-database safety finding is still open. |

## Remaining defects with concrete failure scenarios

### 1. A crash now strands a task even before its first checkpoint

`cryo/analysis/runner.py::run_benefit_score_sweep_task` heartbeats before a batch, setting
status to running. `analysis_tasks.claim_due_task` still selects only pending/leased.
Kill the process during that first batch: after expiry the running row remains unclaimable.
The new clean-yield path cannot run on an OOM, SIGKILL, or node loss.

Repair: implement recovery of expired running attempts together with external Job
reconciliation and a new fence. Test kill after heartbeat, kill after a table output commit,
kill after checkpoint, and kill before yield. Assert continuation to complete coverage.
Correct comments claiming the old running state would become claimable merely by waiting
one lease window; running is excluded regardless of elapsed time.

### 2. Yield and reset still allow stale-owner interference

`yield_task` clears ownership and changes status to pending without advancing the token.
`heartbeat`/`commit_progress`/`finish_task` accept any nonterminal status with that token.
An old callback after a successful yield can therefore turn pending back into running or
finish it before another owner claims it. There is no lease-expiry predicate on these writes.

`start_new_generation` still reads a model, computes `lease_generation + 1` in Python, and
commits without a conditional predicate or row lock. If it reads token 5, another session
heartbeats to 6, and the reset then stores 6, the old holder of 6 is valid in the reset
generation. Thus the token is not strictly monotonic under that interleaving.

Repair: make reset atomic and conditional; invalidate the token on release/reset. Restrict
worker writes to owned leased/running attempts with the intended expiry contract. Reject
expired owners, or document and prove a deliberately different ownership protocol. Include
task generation/owner checks where appropriate. Avoid mixing an ownership fence with a
per-write revision accidentally: a future independent heartbeat thread must coordinate token
updates with checkpoint writes, otherwise the same worker can invalidate its own checkpoint.

The claim docstring also incorrectly says the old `FOR UPDATE` lock protected only the
SELECT statement. PostgreSQL row locks persist through the transaction. Preserve the valid
claim locking while fixing the actual unfenced reset and output paths.

### 3. Pre-batch heartbeat is not protection during a batch

A batch lasting more than 300 seconds outlives its renewed lease. Scoring still commits
per-table outputs before `commit_progress` verifies the task token. Fixing reclaim alone
would then allow overlapping stale and current output writes.

Repair: compute bounded pages and commit output plus progress under one ownership fence;
renew ownership during long computation through a coordinated heartbeat mechanism. Add SQL
timeouts and a wall-time checkpoint margin, rather than relying only on ten batches of 200
tables. Do not close F05 based on a test that loses ownership only between batches.

### 4. Budget persistence does not yet cap cost across interrupted attempts

The runner subtracts `realized_value_refreshes_spent` locally and persists it only after
the batch returns. A crash after refresh work commits but before `commit_progress` leaves
the durable budget unchanged. With crash recovery implemented, repeated attempts can exceed
the generation budget. Refresh selection is also limited to the current table page, allowing
early IDs to spend the budget before later, potentially older candidates are considered.

Repair: durably reserve refresh allowance before expensive work, keyed to a generation and
work item/attempt, and account for uncertain or failed work conservatively. Select eligible
stale work across the fleet, or persist a fair rotation. Output transaction atomicity alone
does not refund compute already spent by an aborted attempt. Test interruption between spend
and checkpoint plus repeated generations with stale candidates on later pages.

### 5. Parent locking must be shared by child lifecycle writers

The new finalizer lock serializes finalizers. However, `cryo/queue/reconcile.py` checks
`job.run_status` from a previously read DTO, then enqueues and calls `store.mark_status`.
`SQLJobStore.mark_status` in `cryo/db/repositories/job_store.py` loads and updates the child
without rechecking/locking the parent in that transition. The reviewed path can therefore
read running, race with finalization, and subsequently requeue under a terminal parent.

Repair: make parent eligibility and the durable child transition participate in one locking
or conditional-transition protocol, with consistent lock order. Reconcile Redis publication
with the durable transition; do not assume the queue and database commit atomically. Test a
barrier after the old parent read, finalize concurrently, then resume child requeue. Assert
no admissible queued child survives under a terminal parent.

### 6. Measurement states do not yet prevent misleading totals

`DailyImpact.bytes_freed_is_known` remains true for partial coverage.
`compute_impact_signal` computes percentages for a partial day or partial baseline, allowing
a change in evidence coverage to look like a change in maintenance performance.
`daily_report.py::_build_slack_blocks` still formats the reclaimed and comparison cells as
numeric GB for an unmeasured day. `cryo_vitals_to_payload` still emits numeric zero alongside
the new state. The normal signal-return path also returns numeric baseline GB even when its
comparison was suppressed because that baseline was unmeasured.

Repair: serialize an unknown total as null, expose measured subtotal and counts separately,
and suppress total-impact directions/percentages for incomparable coverage. Update every
Slack cell and API consumer, not just the narrative. Add freshness evidence before treating
no actions as a trustworthy zero. Tests must include complete-to-partial and partial-to-complete
transitions with unchanged real impact, unmeasured baseline, and the complete Slack payload.

### 7. F12's original safety issue was not the metadata-default leak

`tests/test_analysis_runner_postgres.py` still checks availability with configured
`SessionLocal` + `SELECT 1`, invokes the real fleet task type, traverses all active tables,
and deletes rows by that task type. Other cleanup uses broad `test_%` matching. A database
with no active analysis task may still contain real tables and historical tasks.

Repair: require a disposable, explicitly identified PostgreSQL test database; inject its
session factory; apply migrations there; and clean only fixture-owned IDs. Do not infer
isolation from successful tests or a local-looking hostname. Make CI fail if the required
PostgreSQL tests skip. Separately correct `_patch_sqlite_incompatible_defaults`'s `-> None`
annotation: it now returns a list consumed by its caller.

## F02/F06/F07 can be implemented and convincingly tested offline

Live integration is a final gate, not a prerequisite for testing their deterministic behavior.

| Work | Local evidence to build now | Staging evidence required before enablement |
|---|---|---|
| F02 | Pure Job manifest builder; fail-fast configuration tests; Helm matrix for analysis/Spark on/off, custom release, image digest, Secret/ConfigMap refs, SA, placement and resource bounds. Assert no fallback to an unrelated/native or latest image. | Real image pull, service-account authorization, network/DNS and database connectivity using deployed references. |
| F06 | PostgreSQL attempt reservation plus scripted Kubernetes-client responses: create success, success with lost response, conflict on deterministic name, Pending/Running/terminal state, deletion and retry. Two coordinators must resolve one durable attempt identity. | Actual API/RBAC behavior, controller lifecycle and scheduler restart reconciliation. |
| F07 | Generated large-table data with small injected page sizes; pinned scans, mid-page scan replacement, deletion/retention, new table IDs, empty final page and page-size-invariant scores. Assert covered IDs equal the expected snapshot set. | Production-shaped volume, query plans, memory peaks, duration and acceptable freshness. |

Do not invent a general framework. Extend the existing task repository, dispatcher and
scoring functions with these narrow contracts. Keep source reads separate from engine writes
and preserve the existing governor/partition/retention safeguards.

## Next implementation order and acceptance

1. **Safe verification:** close the original F12, retain the fixture-default repair, and run
   migrations 0049–0053 on an isolated populated test database. Record exact test command,
   revision, database provenance and skipped count.
2. **One coherent ownership protocol:** finish F03/F04/F05 and crash-aware F08 together.
   Prove stale-owner rejection, expired-running recovery, budget bounds and output/checkpoint
   consistency. Add deterministic interleavings, not only threads starting at roughly the
   same time.
3. **Coverage and integration:** implement F07, then F02/F06 manifest/dispatch contracts and
   the local test matrix. Separate frequent continuation/reconciliation polling from persisted
   six-hour sweep due time. Exercise both real scheduler and serve entrypoints.
4. **Default-path release blockers:** finish F09 and F10 independently of the Job flag. They
   affect current lifecycle/reporting behavior even when Jobs remain disabled. Complete F11
   member verification without blanket run/job mutations.
5. **Staging canary:** verify image/config/RBAC and restart/deadline behavior, full coverage,
   bounded memory, no duplicated committed outputs, and no scheduler heavy-sweep fallback.
6. **R7/R8:** use the existing review's concrete capacity and economic deliverables. Baseline
   collection and metric definitions can proceed now; production right-sizing follows healthy
   useful-work measurements. Keep billing allocation waste separate from actual bill savings.

R1 memory profiling remains required; no new evidence here proves all scheduler memory growth
sources are resolved. Burn-in and cost-saving claims remain gated on useful maintenance,
measurement completeness, lifecycle correctness, and comparable-period economics. Preserve
the previous recovery plan's rollback and event-gated ten-day evaluation requirements.

## Validation in this review

Independent run, exit 0, **91 tests passed**:

```text
.venv/bin/pytest -q tests/test_benefit_score_integration.py tests/test_impact_signals.py tests/test_run_finalizer.py tests/test_vitals.py tests/test_cli_scheduler_benefit_score_flag.py
```

The reported 146/146 PostgreSQL-inclusive run was not repeated against the configured
database because the original F12 isolation defect remains. No fail-without-fix mutation
experiment was performed here. Existing tests named concurrent claim/progress use two
sessions sequentially; the finalizer test starts threads with a barrier but does not force
both through the vulnerable read/write interleaving. These tests are useful, but are not
proof of every concurrency claim in the supplied summary.

No live cluster/billing query or deployment was performed. No full lint/typecheck/full-suite
pass is claimed. Documentation-only changes preserve the implementation for the next fixes.
