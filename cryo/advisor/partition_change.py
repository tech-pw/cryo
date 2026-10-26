"""Table layout changes from Path to A: partition spec and sort order, through PyIceberg.

Both are metadata-only commits. New writes use the new layout; existing files keep theirs
until a rewrite (compaction) moves them.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session


class LayoutChangeError(ValueError):
    pass


def _load_iceberg_table(session: Session, table_id: uuid.UUID) -> Any:
    from cryo.db.models import Source, Table
    from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog

    table = session.get(Table, table_id)
    if table is None:
        raise LayoutChangeError("Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        raise LayoutChangeError("Source not found")
    catalog = PyIcebergSourceCatalog(name=str(source.id), config=source.config)
    return catalog.load_table(table.schema_name, table.table_name)


def _transform(name: str) -> Any:
    from pyiceberg.transforms import DayTransform, MonthTransform, YearTransform

    transforms: dict[str, Any] = {
        "day": DayTransform(),
        "month": MonthTransform(),
        "year": YearTransform(),
    }
    if name not in transforms:
        raise LayoutChangeError(f"Unsupported partition transform: {name}")
    return transforms[name]


def change_partition_spec(
    session: Session,
    table_id: uuid.UUID,
    *,
    remove: Sequence[str],
    add: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Remove partition fields by name and add day/month/year fields, in one commit."""
    if not remove and not add:
        raise LayoutChangeError("No partition change given")
    iceberg_table = _load_iceberg_table(session, table_id)
    spec = iceberg_table.spec()
    schema = iceberg_table.schema()
    current = {spec_field.name: spec_field for spec_field in spec.fields}
    missing = [name for name in remove if name not in current]
    if missing:
        raise LayoutChangeError(
            f"The table's current spec has no field {', '.join(missing)}; rescan the table "
            "and try again."
        )
    if len(remove) >= len(current) and not add:
        raise LayoutChangeError("This change would leave the table unpartitioned")
    removed = [
        f"{current[name].transform}({schema.find_column_name(current[name].source_id)})"
        for name in remove
    ]
    added = [f"{item['transform']}({item['source_column']})" for item in add]
    with iceberg_table.update_spec() as update:
        for name in remove:
            update.remove_field(name)
        for item in add:
            column = item["source_column"]
            update.add_field(column, _transform(item["transform"]), f"{column}_{item['transform']}")
    refreshed = iceberg_table.refresh()
    return {"removed": removed, "added": added, "spec_id": int(refreshed.spec().spec_id)}


def set_sort_order(session: Session, table_id: uuid.UUID, columns: Sequence[str]) -> dict[str, Any]:
    from pyiceberg.transforms import IdentityTransform

    if not columns:
        raise LayoutChangeError("No sort columns given")
    iceberg_table = _load_iceberg_table(session, table_id)
    schema = iceberg_table.schema()
    for column in columns:
        try:
            schema.find_field(column, case_sensitive=False)
        except ValueError as exc:
            raise LayoutChangeError(f"Column {column} is not in the table schema") from exc
    with iceberg_table.update_sort_order() as update:
        for column in columns:
            update.asc(column, IdentityTransform())
    refreshed = iceberg_table.refresh()
    return {"sort_order": list(columns), "sort_order_id": int(refreshed.sort_order().order_id)}
