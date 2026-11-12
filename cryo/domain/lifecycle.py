"""Table lifecycle (#519): ``active`` (default) or ``archived``.

An archived table is read-only on purpose: a backup, a snapshot copy, a retired table. It
still costs storage and still collects snapshots and orphan files, so the housekeeping that
only touches metadata and unreferenced files keeps running. Operations that rewrite data
files stop, because there are no new writes to compact or re-layout.
"""

from __future__ import annotations

from enum import StrEnum

from cryo.domain.enums import OperationType


class TableLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


# Every operation that commits rewritten or deleted data files. expire_data is here too: an
# archive is frozen, so a TTL that deletes rows from it is a data change someone must opt into
# by returning the table to active.
ARCHIVED_EXCLUDED_OPERATIONS: frozenset[OperationType] = frozenset(
    {
        OperationType.CONVERT_EQUALITY_DELETES,
        OperationType.CONVERT_DELETES,
        OperationType.REWRITE_POSITION_DELETES,
        OperationType.OPTIMIZE,
        OperationType.COMPUTE_STATISTICS,
        OperationType.EXPIRE_DATA,
        OperationType.MIGRATE_PARTITION_SPEC,
    }
)

# Archive-candidate suggestion thresholds. 90 days matches the window after which BigQuery
# treats unmodified table data as long-term storage; 30 days of no reads is the query window
# Cryo already records (query_count_30d).
ARCHIVE_CANDIDATE_NO_COMMIT_DAYS = 90
ARCHIVE_CANDIDATE_NO_READ_DAYS = 30


def is_archived(lifecycle: str | None) -> bool:
    return lifecycle == TableLifecycle.ARCHIVED


def lifecycle_excluded_operations(lifecycle: str | None) -> tuple[str, ...]:
    if not is_archived(lifecycle):
        return ()
    return tuple(sorted(op.value for op in ARCHIVED_EXCLUDED_OPERATIONS))


def is_archive_candidate(
    *, lifecycle: str | None, days_since_commit: int | None, query_count_30d: int | None
) -> bool:
    """A suggestion only: no commit for 90+ days and, when reads are recorded, none in 30."""
    if is_archived(lifecycle) or days_since_commit is None:
        return False
    if days_since_commit < ARCHIVE_CANDIDATE_NO_COMMIT_DAYS:
        return False
    return not query_count_30d
