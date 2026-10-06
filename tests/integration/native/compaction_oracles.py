"""Reusable compaction correctness oracles (§8.3) for native REST e2e tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pyiceberg.manifest import DataFileContent
from pyiceberg.table import Table

from tests.integration.native.mor_inspect import count_live_rows, live_int_column_values as mor_live_ints
from tests.integration.native.rest_env import list_object_sizes, normalize_uri


@dataclass(frozen=True)
class TableSnapshot:
    snapshot_id: int | None
    schema_id: int
    default_spec_id: int
    sort_order_id: int | None
    row_count: int
    data_file_count: int
    data_file_paths: frozenset[str]
    delete_file_count: int


def capture_table_snapshot(table: Table) -> TableSnapshot:
    metadata = table.metadata
    sort_order_id = metadata.sort_orders[0].order_id if metadata.sort_orders else None
    paths = frozenset(_iter_data_file_paths(table))
    return TableSnapshot(
        snapshot_id=metadata.current_snapshot_id,
        schema_id=metadata.current_schema_id,
        default_spec_id=metadata.default_spec_id,
        sort_order_id=sort_order_id,
        row_count=count_live_rows(table),
        data_file_count=len(paths),
        data_file_paths=paths,
        delete_file_count=count_delete_files(table),
    )


def count_rows(table: Table) -> int:
    return count_live_rows(table)


def count_delete_files(table: Table) -> int:
    total = 0
    for entry in _iter_manifest_entries(table):
        if entry.data_file.content != DataFileContent.DATA:
            total += 1
    return total


def live_int_column_values(table: Table, column: str) -> list[int]:
    return mor_live_ints(table, column)


def assert_deleted_ids_absent(table: Table, deleted_ids: set[int]) -> None:
    live_ids = set(live_int_column_values(table, "id"))
    overlap = live_ids & deleted_ids
    assert not overlap, f"O7 deleted rows reappeared: ids={sorted(overlap)}"


def assert_delete_files_reduced(before: TableSnapshot, after: TableSnapshot) -> None:
    assert after.delete_file_count < before.delete_file_count, (
        f"O7 expected fewer delete files: {before.delete_file_count} -> {after.delete_file_count}"
    )


def paths_for_path_token(table: Table, token: str) -> set[str]:
    return {path for path in _iter_data_file_paths(table) if token in path}


def _iter_manifest_entries(table: Table):
    metadata = table.metadata
    snapshot_id = metadata.current_snapshot_id
    if snapshot_id is None:
        return
    snapshot = metadata.snapshot_by_id(snapshot_id)
    if snapshot is None:
        return
    for manifest in snapshot.manifests(table.io):
        for entry in manifest.fetch_manifest_entry(table.io):
            yield entry


def _iter_data_file_paths(table: Table) -> list[str]:
    paths: list[str] = []
    for entry in _iter_manifest_entries(table):
        if entry.data_file.content == DataFileContent.DATA:
            paths.append(normalize_uri(entry.data_file.file_path))
    return paths


def _file_path_from_task(task: object) -> str | None:
    file_obj = getattr(task, "file", None)
    if file_obj is None:
        return None
    path = getattr(file_obj, "file_path", None)
    if path is None:
        return None
    return normalize_uri(str(path))


def assert_row_count_unchanged(before: TableSnapshot, after: TableSnapshot) -> None:
    assert before.row_count == after.row_count, (
        f"O1 row count changed: {before.row_count} -> {after.row_count}"
    )


def assert_failure_no_op(before: TableSnapshot, after: TableSnapshot) -> None:
    """O9: injected failure left the table unchanged."""
    assert before.snapshot_id == after.snapshot_id, (
        f"O9 snapshot id changed: {before.snapshot_id} -> {after.snapshot_id}"
    )
    assert_row_count_unchanged(before, after)
    assert before.data_file_paths == after.data_file_paths, (
        "O9 data file set changed after failed compaction "
        f"(removed={sorted(before.data_file_paths - after.data_file_paths)[:3]}, "
        f"added={sorted(after.data_file_paths - before.data_file_paths)[:3]})"
    )
    assert before.delete_file_count == after.delete_file_count, (
        f"O9 delete file count changed: {before.delete_file_count} -> {after.delete_file_count}"
    )


def assert_metadata_stable(before: TableSnapshot, after: TableSnapshot) -> None:
    assert before.schema_id == after.schema_id, (
        f"O6 schema id changed: {before.schema_id} -> {after.schema_id}"
    )
    assert before.default_spec_id == after.default_spec_id, (
        f"O6 partition spec id changed: {before.default_spec_id} -> {after.default_spec_id}"
    )
    assert before.sort_order_id == after.sort_order_id, (
        f"O6 sort order id changed: {before.sort_order_id} -> {after.sort_order_id}"
    )


def assert_file_count_reduced(before: TableSnapshot, after: TableSnapshot) -> None:
    assert after.data_file_count < before.data_file_count, (
        f"O8 file count did not decrease: {before.data_file_count} -> {after.data_file_count}"
    )


def assert_files_exist_in_object_store(table: Table, location: str) -> None:
    sizes = list_object_sizes(location)
    missing = [path for path in _iter_data_file_paths(table) if path not in sizes]
    assert not missing, f"O3 missing data files in object store: {missing[:3]}"


def assert_partition_scope_unchanged(
    before: TableSnapshot,
    after: TableSnapshot,
    *,
    outside_path_token: str,
) -> None:
    """Verify files outside the scoped partition token are unchanged (O4)."""
    untouched_before = {path for path in before.data_file_paths if outside_path_token in path}
    untouched_after = {path for path in after.data_file_paths if outside_path_token in path}
    assert untouched_before == untouched_after, (
        "O4 files outside predicate scope changed "
        f"(removed={sorted(untouched_before - untouched_after)[:3]}, "
        f"added={sorted(untouched_after - untouched_before)[:3]})"
    )


def assert_snapshot_lineage(table: Table, parent_snapshot_id: int | None) -> None:
    metadata = table.metadata
    current = metadata.current_snapshot_id
    assert current is not None, "expected a current snapshot after compaction"
    snapshot = next(
        snap for snap in metadata.snapshots if snap.snapshot_id == current
    )
    assert snapshot.parent_snapshot_id == parent_snapshot_id, (
        f"O5 parent snapshot mismatch: expected {parent_snapshot_id}, "
        f"got {snapshot.parent_snapshot_id}"
    )
    summary = snapshot.summary
    operation = None
    if isinstance(summary, dict):
        operation = summary.get("operation")
    elif summary is not None:
        operation = getattr(summary, "operation", None)
        if operation is not None and not isinstance(operation, str):
            operation = getattr(operation, "value", str(operation))
    if operation is not None:
        assert str(operation).lower() == "replace", (
            f"O5 expected replace operation, got {operation}"
        )


def epoch_day(value: date) -> int:
    return (value - date(1970, 1, 1)).days


def data_files_for_partition(table: Table, *, epoch_day_value: int) -> set[str]:
    """Return data file paths whose partition matches a day transform value."""
    from datetime import timedelta

    paths: set[str] = set()
    partition_date = date(1970, 1, 1) + timedelta(days=epoch_day_value)
    path_needle = f"event_date_day={partition_date.isoformat()}"

    for path in _iter_data_file_paths(table):
        if path_needle in path:
            paths.add(path)
            continue
        for task in table.scan().plan_files():
            task_path = _file_path_from_task(task)
            if task_path != path:
                continue
            partition = getattr(getattr(task, "file", None), "partition", None)
            if partition is not None and _partition_matches_day(partition, epoch_day_value):
                paths.add(path)
            break
    return paths


def _partition_matches_day(partition: Any, epoch_day_value: int) -> bool:
    if isinstance(partition, dict):
        for value in partition.values():
            if _value_as_epoch_day(value) == epoch_day_value:
                return True
        return False
    if isinstance(partition, (list, tuple)):
        return any(_value_as_epoch_day(value) == epoch_day_value for value in partition)
    return _value_as_epoch_day(partition) == epoch_day_value


def _value_as_epoch_day(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, date):
        return epoch_day(value)
    if hasattr(value, "as_py"):
        converted = value.as_py()
        if isinstance(converted, date):
            return epoch_day(converted)
        if isinstance(converted, int):
            return converted
    return None
