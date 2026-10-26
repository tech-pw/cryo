"""Which maintenance operations an already-active job covers, and what a new request adds.

Pure: no I/O. The database side (loading active jobs, superseding) is
``cryo.db.repositories.job_dedupe``; the scheduler and the API both reduce a new request
against the same coverage rules so they cannot disagree about what counts as a duplicate.

A job's *effective operations* are the ones it can plan: an explicit subset recorded on the job
(dedupe trimmed it), else the run's explicit request, else everything its mode can emit. The
preset's own operations list is deliberately not consulted -- it does not bound the plan (see
``build_execution_plan``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from cryo.domain.enums import Mode, OperationType
from cryo.planner.guardrails import MODE_OPERATIONS, sort_operations_canonically

# Statuses in which a job still owns work for its table. A terminal job covers nothing.
ACTIVE_JOB_STATUSES: tuple[str, ...] = ("queued", "admitted", "running", "deferred")


@dataclass(frozen=True)
class ActiveJob:
    job_id: str
    operations: frozenset[str]


@dataclass(frozen=True)
class Coverage:
    """What is left of a request once active jobs are accounted for."""

    remaining: tuple[str, ...]
    skipped: tuple[str, ...]
    covering_job_ids: tuple[str, ...]

    @property
    def fully_covered(self) -> bool:
        return not self.remaining

    @property
    def partially_covered(self) -> bool:
        return bool(self.remaining) and bool(self.skipped)


def canonical_operations(operations: Iterable[str | OperationType]) -> tuple[str, ...]:
    """Valid operation names, de-duplicated, in CORRECT_ORDER. Unknown names are dropped."""
    seen: set[str] = set()
    valid: list[str] = []
    for operation in operations:
        try:
            name = OperationType(operation).value
        except ValueError:
            continue
        if name not in seen:
            seen.add(name)
            valid.append(name)
    return tuple(sort_operations_canonically(valid))


def dedupe_key(operations: Iterable[str | OperationType]) -> str | None:
    """Stable key for ux_jobs_active_table_ops; None when there is nothing to key on."""
    canonical = canonical_operations(operations)
    return ",".join(canonical) if canonical else None


def mode_operations(mode: Mode | str) -> frozenset[str]:
    try:
        return frozenset(op.value for op in MODE_OPERATIONS[Mode(mode)])
    except (ValueError, KeyError):
        return frozenset()


def effective_operations(
    mode: Mode | str,
    *,
    job_operations: Sequence[str] | None = None,
    run_operations: Sequence[str] | None = None,
) -> frozenset[str]:
    for explicit in (job_operations, run_operations):
        if explicit:
            names = canonical_operations(explicit)
            if names:
                return frozenset(names)
    return mode_operations(mode)


def uncovered(requested: Iterable[str | OperationType], active: Iterable[ActiveJob]) -> Coverage:
    """Split ``requested`` into what no active job covers and what one already does."""
    wanted = canonical_operations(requested)
    active_jobs = tuple(active)
    covered: set[str] = set()
    for job in active_jobs:
        covered |= job.operations
    remaining = tuple(op for op in wanted if op not in covered)
    skipped = tuple(op for op in wanted if op in covered)
    wanted_set = set(wanted)
    covering = tuple(job.job_id for job in active_jobs if job.operations & wanted_set)
    return Coverage(remaining=remaining, skipped=skipped, covering_job_ids=covering)
