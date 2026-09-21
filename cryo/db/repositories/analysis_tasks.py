"""Durable analysis task repository (recovery plan §3, 2026-09-20).

Real incident this fixes: the scheduler ran a multi-hour, ~13k-table benefit-score sweep as an
in-process thread with no persisted progress -- a crash/OOM/redeploy threw the entire sweep away
and restarted from table 1, and the crash-loop was actively costing scheduling throughput. This
module is the compare-and-set claim/heartbeat/commit/release contract that lets a short-lived
K8s Job resume exactly where a prior attempt left off, and lets the coordinator (a lightweight
scheduler thread, not the heavy analysis itself) detect and recover a dead/expired lease without
ever holding the heavy work's own memory.

See cryo/db/models.py::AnalysisTask for the full field-by-field design rationale, and
cryo/analysis/runner.py for the K8s Job entrypoint that actually calls these functions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cryo.db.models import AnalysisTask

# Terminal statuses -- a row in one of these does not block a new active row for the same
# (task_type, scope) under the partial unique index (see the migration/model docstrings).
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_ACTIVE_STATUSES = frozenset({"pending", "leased", "running"})


class LeaseConflict(Exception):
    """Raised when a caller's lease_generation no longer matches the row's current one --
    another owner has already claimed a newer generation of this task. The caller must stop
    writing immediately; continuing would risk two owners committing progress for the same
    logical task concurrently (recovery plan §3's "stale child" scenario)."""


@dataclass(frozen=True)
class ClaimedTask:
    """A snapshot of the task as of a successful claim -- the child process should treat this
    as its working state and never re-read task_type/scope/generation from a stale reference
    after this point; re-fetch via get_task if it needs the row again."""

    id: uuid.UUID
    task_type: str
    scope: str
    generation: int
    lease_generation: int
    cursor: str | None
    input_watermark: str | None
    attempt: int
    task_state: dict | None


def ensure_task_due(
    session: Session,
    *,
    task_type: str,
    scope: str,
    due_at: datetime | None = None,
    input_watermark: str | None = None,
) -> AnalysisTask:
    """Idempotently ensure a due task exists for (task_type, scope).

    If an active (pending/leased/running) row already exists for this (task_type, scope), it is
    returned unchanged -- this is what makes a scheduler restart reconcile the existing task
    instead of racing a duplicate (recovery plan §3: "on restart, reconcile the external Job
    before resubmitting"). Only creates a new row when no active one exists, e.g. the prior
    generation reached a terminal status and a new sweep is now due.
    """
    existing = session.scalars(
        select(AnalysisTask).where(
            AnalysisTask.task_type == task_type,
            AnalysisTask.scope == scope,
            AnalysisTask.status.in_(_ACTIVE_STATUSES),
        )
    ).first()
    if existing is not None:
        return existing

    task = AnalysisTask(
        task_type=task_type,
        scope=scope,
        status="pending",
        due_at=due_at or datetime.now(UTC),
        input_watermark=input_watermark,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        # Real race, not a bug: another coordinator replica (or this same one, re-entrant)
        # inserted a competing active row for this (task_type, scope) between our SELECT and
        # INSERT. The partial unique index is the actual source of truth here -- roll back our
        # attempt and return whichever row won.
        session.rollback()
        existing = session.scalars(
            select(AnalysisTask).where(
                AnalysisTask.task_type == task_type,
                AnalysisTask.scope == scope,
                AnalysisTask.status.in_(_ACTIVE_STATUSES),
            )
        ).first()
        if existing is None:
            raise
        return existing
    session.refresh(task)
    return task


def claim_due_task(
    session: Session,
    *,
    task_type: str,
    owner: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ClaimedTask | None:
    """Atomically claim the next due, unleased-or-lease-expired task of this type.

    Real bug fixed here (review finding F04, 2026-09-20): the original version selected a
    candidate row with `FOR UPDATE SKIP LOCKED` (correct and kept below), then wrote the new
    lease fields via plain ORM attribute assignment + commit. That row-lock only protects
    *this* statement's SELECT against a second concurrent SELECT -- it says nothing about a
    second UPDATE racing the first UPDATE's actual write, and more importantly every other
    write path in this module (heartbeat/commit_progress/finish_task) used a bare
    `session.get()` read, a Python-level `if ... != ...` comparison, and a separate ORM
    `.commit()` with NO row-locking or WHERE-clause fencing at all -- a textbook check-then-act
    race: two owners could both read the same `lease_generation`, both pass the Python check,
    and both commit, with the later commit silently winning over the earlier one's intended
    fence rejection. Every mutating function in this module now issues a single
    `UPDATE ... WHERE id = :id AND lease_generation = :expected ... RETURNING *` and inspects
    `rowcount` -- the database's MVCC/row-lock semantics guarantee at most one such UPDATE can
    match and commit for a given `(id, lease_generation)` pair, so the fence is enforced by the
    write itself, not by a read that happened to run first.

    `lease_generation` is also now a purely monotonic fencing token that is NEVER reset by
    `start_new_generation` (see that function's docstring for the fence-reuse bug this fixes) --
    it only ever increments, on every successful claim, for the lifetime of the row.
    """
    current = now or datetime.now(UTC)
    candidate = session.scalars(
        select(AnalysisTask)
        .where(
            AnalysisTask.task_type == task_type,
            AnalysisTask.status.in_(("pending", "leased")),
            AnalysisTask.due_at <= current,
        )
        .where(
            (AnalysisTask.lease_until.is_(None)) | (AnalysisTask.lease_until <= current)
        )
        .order_by(AnalysisTask.due_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if candidate is None:
        return None

    expected_generation = candidate.lease_generation
    new_lease_generation = expected_generation + 1
    result = session.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.id == candidate.id,
            AnalysisTask.lease_generation == expected_generation,
        )
        .values(
            status="leased",
            lease_owner=owner,
            lease_generation=new_lease_generation,
            lease_until=current + timedelta(seconds=lease_seconds),
            heartbeat_at=current,
            attempt=AnalysisTask.attempt + 1,
        )
    )
    if result.rowcount != 1:
        # The FOR UPDATE SKIP LOCKED select above should make this unreachable in practice (it
        # already excludes rows another transaction holds a lock on), but the fenced UPDATE is
        # kept as the actual correctness boundary rather than trusting the lock alone -- a
        # future change to the SELECT's locking behavior must not silently reintroduce the race.
        session.rollback()
        return None
    session.commit()
    session.refresh(candidate)
    return ClaimedTask(
        id=candidate.id,
        task_type=candidate.task_type,
        scope=candidate.scope,
        generation=candidate.generation,
        lease_generation=candidate.lease_generation,
        cursor=candidate.cursor,
        input_watermark=candidate.input_watermark,
        attempt=candidate.attempt,
        task_state=candidate.task_state,
    )


def heartbeat(
    session: Session,
    task_id: uuid.UUID,
    *,
    lease_generation: int,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> int | None:
    """Extend a held lease via a single fenced UPDATE. Returns the new lease_generation the
    caller must use for its next call, or None if the lease_generation no longer matches or the
    task has already reached a terminal status -- the caller has been superseded (or the task
    finished under it) and must stop working, but losing a heartbeat race is expected
    steady-state, not an error worth an exception.

    Real bug fixed here (F04): the old version read-then-compared-then-wrote in three separate
    steps with no fencing on the write itself, AND never checked terminal status at all -- a
    heartbeat from a lease-holder whose task had already been finish_task'd elsewhere (a
    legitimate race: the runner's own finish_task call races this same process's heartbeat
    thread) could silently move a terminal row back to 'running'. The WHERE clause below excludes
    terminal statuses explicitly so that can no longer happen. This call also now advances
    lease_generation on every success, same rationale as commit_progress: without that, two
    heartbeats presenting the same stale token would both match and both succeed.
    """
    current = now or datetime.now(UTC)
    new_lease_generation = lease_generation + 1
    result = session.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.lease_generation == lease_generation,
            AnalysisTask.status.notin_(_TERMINAL_STATUSES),
        )
        .values(
            lease_generation=new_lease_generation,
            lease_until=current + timedelta(seconds=lease_seconds),
            heartbeat_at=current,
            status="running",
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return new_lease_generation


def commit_progress(
    session: Session,
    task_id: uuid.UUID,
    *,
    lease_generation: int,
    cursor: str | None,
    processed_delta: int,
    task_state: dict | None = None,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> int:
    """Atomically advance cursor and processed_count, extending the lease in the same write.
    Returns the new lease_generation the caller must use for its NEXT commit_progress/
    heartbeat/finish_task call (see below for why this changed).

    Raises LeaseConflict if lease_generation no longer matches or the task is already terminal
    -- see that exception's own docstring. The caller (cryo/analysis/runner.py) must treat this
    as fatal and exit without committing any further output: another owner already holds a
    newer generation of this task, and continuing risks two owners racing progress for the same
    logical work.

    Real bug fixed here (F04): same check-then-write race as heartbeat/claim_due_task -- this is
    now a single fenced UPDATE ... WHERE lease_generation = :expected, and rowcount is the sole
    source of truth for whether the fence held, not a Python-level read-compare beforehand.

    This call ALSO now advances lease_generation on every successful write (not just on
    claim/start_new_generation) -- without that, two calls presenting the SAME held
    lease_generation (a genuine duplicate/retry, not just a true concurrent race) would both
    match the WHERE clause and both succeed, since nothing about a successful commit_progress
    used to change the fence value. A caller must carry forward the returned lease_generation
    into its next call; presenting a now-stale one correctly raises LeaseConflict.

    `task_state` (review finding F08) is opaque, task-type-defined extra checkpoint state that
    doesn't fit the single-string `cursor` field -- e.g. the benefit-score sweep's
    generation-wide realized-value refresh budget consumption. Passing None leaves the stored
    value UNCHANGED (not cleared) -- a caller that doesn't use task_state at all can omit it
    without accidentally wiping another caller's state on the same row within one generation.
    """
    current = now or datetime.now(UTC)
    new_lease_generation = lease_generation + 1
    values: dict = {
        "cursor": cursor,
        "processed_count": AnalysisTask.processed_count + processed_delta,
        "lease_generation": new_lease_generation,
        "lease_until": current + timedelta(seconds=lease_seconds),
        "heartbeat_at": current,
        "status": "running",
    }
    if task_state is not None:
        values["task_state"] = task_state
    result = session.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.lease_generation == lease_generation,
            AnalysisTask.status.notin_(_TERMINAL_STATUSES),
        )
        .values(**values)
    )
    if result.rowcount != 1:
        session.rollback()
        raise LeaseConflict(
            f"analysis_task {task_id} lease_generation mismatch, task missing, or already "
            f"terminal (expected lease_generation={lease_generation})"
        )
    session.commit()
    return new_lease_generation


def finish_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    lease_generation: int,
    status: str,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> None:
    """Mark a task terminal (status must be 'succeeded', 'failed', or 'cancelled').

    Raises LeaseConflict on a stale lease_generation or an already-terminal row, same contract
    as commit_progress -- a superseded attempt must never be allowed to mark the *current*
    generation's work finished, and a task cannot be finished twice.

    Real bug fixed here (F04): fenced single UPDATE, same pattern as the other write paths above.
    """
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"finish_task status must be terminal, got {status!r}")
    current = now or datetime.now(UTC)
    result = session.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.lease_generation == lease_generation,
            AnalysisTask.status.notin_(_TERMINAL_STATUSES),
        )
        .values(
            status=status,
            failure_reason=failure_reason,
            finished_at=current,
            started_at=func.coalesce(AnalysisTask.started_at, current),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise LeaseConflict(
            f"analysis_task {task_id} lease_generation mismatch, task missing, or already "
            f"terminal (expected lease_generation={lease_generation})"
        )
    session.commit()


def yield_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    lease_generation: int,
    now: datetime | None = None,
) -> bool:
    """Release a lease back to 'pending' without marking the task terminal -- the resumable-exit
    path for a runner that hit its own bounded-batch-count limit (F03).

    Real bug fixed here (F03): the runner previously just... exited. commit_progress's last call
    left the row at status='running' with a lease that (correctly) eventually expires, but until
    that lease_until passes, claim_due_task's WHERE clause (status IN ('pending','leased') AND
    (lease_until IS NULL OR lease_until <= now)) cannot see the row at all -- 'running' is not in
    that claimable-status list. So a *normal*, successful bounded exit (not a crash) left the
    task unclaimable for a full lease_seconds window it did not need to wait out, and
    ensure_task_due could not create a replacement task either (an active row still exists).
    Calling this immediately before a clean exit returns the row to 'pending' (still claimable
    the moment the coordinator's next tick runs) while preserving cursor/processed_count/
    generation exactly as commit_progress last left them -- only status/lease ownership changes.
    """
    result = session.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.lease_generation == lease_generation,
            AnalysisTask.status.notin_(_TERMINAL_STATUSES),
        )
        .values(
            status="pending",
            lease_owner=None,
            lease_until=None,
            heartbeat_at=None,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def start_new_generation(
    session: Session,
    task_id: uuid.UUID,
    *,
    input_watermark: str | None,
    due_at: datetime | None = None,
    now: datetime | None = None,
) -> AnalysisTask:
    """Reset a task to a fresh generation (new watermark, cursor cleared, lease released).

    Used when new scan evidence invalidates the current generation's watermark mid-sweep
    (recovery plan §3: "never mix generations silently") -- rather than let an in-flight
    generation keep paging against stale evidence, the coordinator calls this to start a clean
    generation once the current one either finishes or is explicitly abandoned.

    Real bug fixed here (F04, "generation reuse"): the original version reset lease_generation
    to 0 on every new generation. Since claim_due_task always increments from the row's current
    lease_generation, the very next claim after a reset produces lease_generation=1 -- the SAME
    token an owner from a PRIOR generation could still be holding (e.g. generation 1's first-ever
    claim was also lease_generation=1). That old owner's next commit_progress/finish_task call
    would then compare its stale token against the row's current token, find them equal, and be
    incorrectly treated as still valid -- writing into the wrong generation's cursor/output.
    lease_generation is now a purely monotonic fencing token for the row's entire lifetime: it is
    never reset to a smaller/reused value, only ever incremented -- by claim_due_task on every
    claim, and by this function too (rather than resetting to 0) so that an old generation's
    still-in-flight holder is immediately fenced out (its held token can never again equal the
    row's current one) without reintroducing the reset-to-a-reused-value bug. Bumping
    `generation` still marks a fresh logical sweep; the safety property (an old owner can never
    match a live fence) comes entirely from lease_generation's monotonicity, independent of
    `generation`.
    """
    current = now or datetime.now(UTC)
    task = session.get(AnalysisTask, task_id)
    if task is None:
        raise ValueError(f"analysis_task {task_id} not found")
    task.generation = task.generation + 1
    task.status = "pending"
    task.cursor = None
    task.input_watermark = input_watermark
    task.due_at = due_at or current
    task.lease_owner = None
    # Advance, never reset -- see docstring above (F04's generation-reuse bug).
    task.lease_generation = task.lease_generation + 1
    task.lease_until = None
    task.heartbeat_at = None
    task.external_job_ref = None
    task.started_at = None
    task.finished_at = None
    task.processed_count = 0
    task.failure_reason = None
    task.next_retry_at = None
    task.task_state = None
    session.commit()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: uuid.UUID) -> AnalysisTask | None:
    return session.get(AnalysisTask, task_id)


def get_active_task(session: Session, *, task_type: str, scope: str) -> AnalysisTask | None:
    return session.scalars(
        select(AnalysisTask).where(
            AnalysisTask.task_type == task_type,
            AnalysisTask.scope == scope,
            AnalysisTask.status.in_(_ACTIVE_STATUSES),
        )
    ).first()
