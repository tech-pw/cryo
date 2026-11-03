"""Commit deltas read from Iceberg snapshot summaries.

Every Iceberg commit writes a snapshot whose summary carries file and byte counters
(``added-data-files``, ``added-delete-files``, ``added-files-size`` ...). Reading them off the
table metadata the catalog already returns costs no manifest or data-file reads, which is
what makes polling hot tables affordable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# Snapshot operations a maintenance job produces. Compaction and manifest rewrites commit a
# "replace" snapshot; expire_snapshots and remove_orphan_files commit none. Writers append,
# overwrite or delete, so a "replace" is not drift -- counting it would let compaction
# trigger more compaction.
MAINTENANCE_OPERATIONS = frozenset({"replace"})

DEFAULT_MAX_COMMITS_PER_OBSERVATION = 500


@dataclass(frozen=True)
class SnapshotCommit:
    snapshot_id: int
    parent_snapshot_id: int | None
    sequence_number: int | None
    timestamp_ms: int | None
    operation: str | None
    summary: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_maintenance(self) -> bool:
        return (self.operation or "").lower() in MAINTENANCE_OPERATIONS


@dataclass(frozen=True)
class SummaryDeltas:
    files_added: int = 0
    files_removed: int = 0
    bytes_added: int = 0
    bytes_removed: int = 0
    delete_files_added: int = 0
    equality_delete_files_added: int = 0
    position_delete_files_added: int = 0
    delete_files_removed: int = 0
    equality_delete_files_removed: int = 0
    position_delete_files_removed: int = 0
    snapshots_added: int = 0
    maintenance_commits: int = 0

    def __add__(self, other: SummaryDeltas) -> SummaryDeltas:
        return SummaryDeltas(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in self.__dataclass_fields__
            }
        )


@dataclass(frozen=True)
class SnapshotObservation:
    """What the catalog says now, and the commits since the last one we saw."""

    snapshot_id: int
    commits: tuple[SnapshotCommit, ...] = ()
    # False when the walk back to the previous snapshot stopped early (expired parents, the
    # commit cap, or no previous snapshot to walk to); the deltas then undercount.
    complete: bool = True

    @property
    def latest(self) -> SnapshotCommit | None:
        return self.commits[-1] if self.commits else None

    def deltas(self) -> SummaryDeltas:
        total = SummaryDeltas()
        for commit in self.commits:
            total = total + summary_deltas(commit)
        return total


def _int(summary: Mapping[str, Any], key: str) -> int:
    value = summary.get(key)
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def summary_deltas(commit: SnapshotCommit) -> SummaryDeltas:
    summary = commit.summary
    equality_added = _int(summary, "added-equality-delete-files")
    position_added = _int(summary, "added-position-delete-files")
    equality_removed = _int(summary, "removed-equality-delete-files")
    position_removed = _int(summary, "removed-position-delete-files")
    # Older writers only report the combined counter; newer ones report both.
    delete_added = max(_int(summary, "added-delete-files"), equality_added + position_added)
    delete_removed = max(_int(summary, "removed-delete-files"), equality_removed + position_removed)
    return SummaryDeltas(
        files_added=_int(summary, "added-data-files"),
        files_removed=_int(summary, "deleted-data-files"),
        bytes_added=_int(summary, "added-files-size"),
        bytes_removed=_int(summary, "removed-files-size"),
        delete_files_added=delete_added,
        equality_delete_files_added=equality_added,
        position_delete_files_added=position_added,
        delete_files_removed=delete_removed,
        equality_delete_files_removed=equality_removed,
        position_delete_files_removed=position_removed,
        snapshots_added=1,
        maintenance_commits=1 if commit.is_maintenance else 0,
    )


def _summary_mapping(summary: Any) -> dict[str, str]:
    if summary is None:
        return {}
    dump = getattr(summary, "model_dump", None)
    if callable(dump):
        payload = dump()
        if isinstance(payload, Mapping):
            return {str(k): str(v) for k, v in payload.items() if v is not None}
    if isinstance(summary, Mapping):
        return {str(k): str(v) for k, v in summary.items() if v is not None}
    return {}


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def snapshot_commit(snapshot: Any) -> SnapshotCommit | None:
    snapshot_id = _optional_int(_get(snapshot, "snapshot_id", "snapshot-id"))
    if snapshot_id is None:
        return None
    summary = _summary_mapping(_get(snapshot, "summary"))
    operation = summary.get("operation")
    if operation is not None and "." in operation:
        # str(Operation.APPEND) on some pyiceberg versions renders as "Operation.APPEND".
        operation = operation.rsplit(".", 1)[-1]
    return SnapshotCommit(
        snapshot_id=snapshot_id,
        parent_snapshot_id=_optional_int(
            _get(snapshot, "parent_snapshot_id", "parent-snapshot-id")
        ),
        sequence_number=_optional_int(_get(snapshot, "sequence_number", "sequence-number")),
        timestamp_ms=_optional_int(_get(snapshot, "timestamp_ms", "timestamp-ms")),
        operation=operation.lower() if operation else None,
        summary=summary,
    )


def observe_metadata(
    metadata: Any,
    *,
    last_seen_snapshot_id: int | None,
    last_seen_sequence_number: int | None = None,
    max_commits: int = DEFAULT_MAX_COMMITS_PER_OBSERVATION,
) -> SnapshotObservation | None:
    """Commits between ``last_seen_snapshot_id`` (exclusive) and the current snapshot.

    Walks the parent chain from the current snapshot rather than comparing ids: Iceberg
    snapshot ids are random 64-bit values, not a sequence, so "newer" can't be read off them.
    With no previous snapshot there is nothing to diff against, so the observation only
    carries the current id. When the walk never reaches the previous snapshot (it was
    expired, or the table was rolled back) it stops at the previous sequence number, so a
    rollback doesn't replay the table's whole history as new commits.
    """
    current_id = _optional_int(_get(metadata, "current_snapshot_id", "current-snapshot-id"))
    if current_id is None or current_id == -1:
        return None
    if last_seen_snapshot_id is None:
        return SnapshotObservation(snapshot_id=current_id, complete=False)
    if current_id == last_seen_snapshot_id:
        return SnapshotObservation(snapshot_id=current_id)
    by_id: dict[int, SnapshotCommit] = {}
    for snapshot in _iter_snapshots(_get(metadata, "snapshots")):
        commit = snapshot_commit(snapshot)
        if commit is not None:
            by_id[commit.snapshot_id] = commit
    chain: list[SnapshotCommit] = []
    cursor: int | None = current_id
    complete = False
    while cursor is not None:
        if cursor == last_seen_snapshot_id:
            complete = True
            break
        commit = by_id.get(cursor)
        if commit is None or len(chain) >= max_commits:
            break
        if (
            last_seen_sequence_number
            and commit.sequence_number is not None
            and commit.sequence_number <= last_seen_sequence_number
        ):
            break
        chain.append(commit)
        cursor = commit.parent_snapshot_id
    chain.reverse()
    return SnapshotObservation(snapshot_id=current_id, commits=tuple(chain), complete=complete)


def _iter_snapshots(value: Any) -> Iterable[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()
