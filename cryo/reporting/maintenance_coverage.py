"""Did the tables we promised to maintain today actually get each operation?

Every existing signal answers a different question. vitals.py asks whether the machine is
healthy, impact_signals.py asks how many bytes were reclaimed fleet-wide, and
trino_hot_cost_signal.py asks whether Trino's scan cost for the hot cohort went down. None
of them can answer the one an operator asks first: "we said the top 500 tables get all four
operations every day -- did they?"

That gap is not cosmetic. Live 2026-09-21, the fleet reported a 9.3% job success rate while
the actual per-table maintenance coverage was unknown, because the failures were dominated
by jobs that never reached an engine at all. A job-level success rate cannot distinguish
"optimize ran and expire_snapshots didn't" from "nothing ran", and a preset silently
configured without optimize_manifests looks identical to one where every manifest rewrite
succeeded. Coverage is measured per (table, operation) from the audit trail, which is the
only place that distinction is recorded.

Counted as covered: a terminal OK/SKIPPED audit row for that operation. SKIPPED is
deliberately a success -- "this table had 7 small files, below the threshold" means the
operation was correctly evaluated and found unnecessary, which is the intended outcome, not
a miss. A miss is an operation that failed or was never attempted at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Preset, TableAssignment
from cryo.domain.enums import OperationType

# The four operations that constitute "maintained" for a table. Ordered as the planner runs
# them (cryo/planner/guardrails.py), not alphabetically, so a report reads in execution order.
CORE_OPERATIONS: tuple[OperationType, ...] = (
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
)

# Audit statuses that mean the operation reached a decision. See the module docstring for why
# SKIPPED counts: a correctly-evaluated no-op is coverage, not a gap.
_COVERED_STATUSES = frozenset({"OK", "SKIPPED"})


@dataclass(frozen=True)
class OperationCoverage:
    operation: OperationType
    covered_tables: int
    failed_tables: int

    def coverage_pct(self, cohort_size: int) -> float | None:
        if cohort_size <= 0:
            return None
        return round(100.0 * self.covered_tables / cohort_size, 1)


@dataclass(frozen=True)
class CohortCoverage:
    preset_name: str
    cohort_size: int
    window_hours: int
    measured_at: datetime
    operations: tuple[OperationCoverage, ...]
    # Tables that got no terminal audit row for ANY of the four operations in the window --
    # the cohort members maintenance did not reach at all, which is the number that actually
    # moved during the 2026-09 incident.
    untouched_tables: int

    @property
    def fully_covered_pct(self) -> float | None:
        """Share of the cohort where every one of the four operations was covered.

        Deliberately the strictest reading. A per-operation average can sit comfortably high
        while no individual table ever receives the full set.
        """
        if self.cohort_size <= 0:
            return None
        weakest = min((op.covered_tables for op in self.operations), default=0)
        return round(100.0 * weakest / self.cohort_size, 1)


def cohort_table_ids(session: Session, *, preset_name: str) -> set[uuid.UUID]:
    """Tables currently pinned to `preset_name`."""
    return set(
        session.scalars(
            select(TableAssignment.table_id)
            .join(Preset, Preset.id == TableAssignment.preset_id)
            .where(Preset.name == preset_name, TableAssignment.pinned.is_(True))
        ).all()
    )


def measure_cohort_coverage(
    session: Session,
    *,
    preset_name: str,
    window_hours: int = 24,
    now: datetime | None = None,
) -> CohortCoverage:
    """Per-operation coverage of `preset_name`'s pinned cohort over the trailing window."""
    current = now or datetime.now(UTC)
    window_start = current - timedelta(hours=window_hours)
    table_ids = cohort_table_ids(session, preset_name=preset_name)

    if not table_ids:
        return CohortCoverage(
            preset_name=preset_name,
            cohort_size=0,
            window_hours=window_hours,
            measured_at=current,
            operations=tuple(
                OperationCoverage(operation=operation, covered_tables=0, failed_tables=0)
                for operation in CORE_OPERATIONS
            ),
            untouched_tables=0,
        )

    rows = session.execute(
        select(
            AuditLog.action,
            AuditLog.status,
            func.count(func.distinct(AuditLog.table_id)),
        )
        .where(
            AuditLog.table_id.in_(table_ids),
            AuditLog.ts >= window_start,
            AuditLog.ts <= current,
            AuditLog.action.in_([operation.value for operation in CORE_OPERATIONS]),
        )
        .group_by(AuditLog.action, AuditLog.status)
    ).all()

    covered: dict[str, int] = {}
    failed: dict[str, int] = {}
    for action, status, table_count in rows:
        bucket = covered if status in _COVERED_STATUSES else failed
        bucket[action] = bucket.get(action, 0) + int(table_count)

    touched = set(
        session.scalars(
            select(func.distinct(AuditLog.table_id)).where(
                AuditLog.table_id.in_(table_ids),
                AuditLog.ts >= window_start,
                AuditLog.ts <= current,
                AuditLog.action.in_([operation.value for operation in CORE_OPERATIONS]),
                AuditLog.status.in_(_COVERED_STATUSES),
            )
        ).all()
    )

    return CohortCoverage(
        preset_name=preset_name,
        cohort_size=len(table_ids),
        window_hours=window_hours,
        measured_at=current,
        operations=tuple(
            OperationCoverage(
                operation=operation,
                covered_tables=covered.get(operation.value, 0),
                failed_tables=failed.get(operation.value, 0),
            )
            for operation in CORE_OPERATIONS
        ),
        untouched_tables=len(table_ids) - len(touched),
    )


def coverage_to_payload(coverage: CohortCoverage) -> dict[str, object]:
    return {
        "preset_name": coverage.preset_name,
        "cohort_size": coverage.cohort_size,
        "window_hours": coverage.window_hours,
        "measured_at": coverage.measured_at.isoformat(),
        "fully_covered_pct": coverage.fully_covered_pct,
        "untouched_tables": coverage.untouched_tables,
        "operations": [
            {
                "operation": item.operation.value,
                "covered_tables": item.covered_tables,
                "failed_tables": item.failed_tables,
                "coverage_pct": item.coverage_pct(coverage.cohort_size),
            }
            for item in coverage.operations
        ],
    }
