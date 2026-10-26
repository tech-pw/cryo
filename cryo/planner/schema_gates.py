"""Plan-time gates for table schemas an engine cannot read.

Spark's Iceberg integration refuses Iceberg ``time`` columns outright: converting the table
schema raises ``UnsupportedOperationException: Spark does not support time fields`` before any
work starts. Every Spark operation that reads data files therefore fails on such a table, and
the planner used to submit them again every cycle (issue #169). Trino and iceberg-go read
``time`` fine; metadata-only operations (expire_snapshots, remove_orphan_files,
optimize_manifests) never touch the table schema in Spark, so they still run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import MaintenancePlan

UNSUPPORTED_SCHEMA_PREFIX = "unsupported_schema"

# Operations whose Spark procedure reads the table's data files, and so the table schema.
SPARK_SCHEMA_READING_OPERATIONS = frozenset(
    {
        OperationType.OPTIMIZE,
        OperationType.CONVERT_DELETES,
        OperationType.CONVERT_EQUALITY_DELETES,
        OperationType.REWRITE_POSITION_DELETES,
        OperationType.COMPUTE_STATISTICS,
        OperationType.EXPIRE_DATA,
    }
)

# "time" as a whole word: matches "time", "list<time>", "struct<at: time>", but never
# "timestamp" or "timestamptz" (Spark supports those).
_TIME_TYPE = re.compile(r"(?<![a-z_])time(?![a-z_0-9])")
_SPARK_ENGINES = frozenset({EngineType.SPARK_EMR.value, "spark"})


def is_time_type_label(label: object) -> bool:
    return isinstance(label, str) and bool(_TIME_TYPE.search(label.strip().lower()))


def schema_time_columns(schema: object) -> list[str]:
    """Dotted paths of ``time`` fields in a PyIceberg schema, nested fields included."""
    found: list[str] = []

    def visit(field: object, prefix: str) -> None:
        name = str(getattr(field, "name", "") or "")
        path = f"{prefix}.{name}" if prefix else name
        field_type = getattr(field, "field_type", None)
        if field_type is None:
            return
        if str(field_type).strip().lower() == "time":
            found.append(path)
            return
        for child in _child_fields(field_type):
            visit(child, path)

    for top in getattr(schema, "fields", None) or ():
        visit(top, "")
    return found


def _child_fields(field_type: object) -> Iterable[object]:
    nested = getattr(field_type, "fields", None)
    if nested:
        return tuple(nested)
    children = []
    for attr in ("element_field", "key_field", "value_field"):
        child = getattr(field_type, attr, None)
        if child is not None:
            children.append(child)
    return children


def time_columns_in_stats(stats: Mapping[str, Any]) -> list[str]:
    """Time columns recorded by the last scan.

    The Python scanner stores the schema walk as ``raw.time_columns``. Scans that do not
    (the Go scanner, older rows) still carry per-column types in ``raw.column_quality``,
    which covers every column with Iceberg metrics.
    """
    raw = stats.get("raw")
    if not isinstance(raw, Mapping):
        return []
    names: list[str] = []
    recorded = raw.get("time_columns")
    if isinstance(recorded, list):
        names.extend(str(name) for name in recorded if name)
    for entry in raw.get("column_quality") or ():
        if isinstance(entry, Mapping) and is_time_type_label(entry.get("column_type")):
            name = entry.get("column_name") or entry.get("name") or entry.get("field_id")
            if name is not None:
                names.append(str(name))
    return list(dict.fromkeys(names))


def _engine_value(engine: object) -> str:
    return engine.value if isinstance(engine, EngineType) else str(engine).lower()


def spark_time_columns_note(columns: Iterable[str], skipped: Iterable[str]) -> str:
    column_list = ", ".join(sorted(columns))
    skipped_list = ", ".join(skipped)
    return (
        f"{UNSUPPORTED_SCHEMA_PREFIX}: time columns not supported by Spark. Skipped "
        f"{skipped_list}: the table has Iceberg time column(s) ({column_list}), which Spark "
        "cannot read. Route the table to a Trino or native destination to compact it."
    )


def gate_spark_time_columns(
    plan: MaintenancePlan,
    *,
    engine: object,
    stats: Mapping[str, Any],
) -> MaintenancePlan:
    """Drop data-reading operations from a Spark plan when the table has ``time`` columns."""
    if _engine_value(engine) not in _SPARK_ENGINES:
        return plan
    dropped = [op for op in plan.operations if op.type in SPARK_SCHEMA_READING_OPERATIONS]
    if not dropped:
        return plan
    columns = time_columns_in_stats(stats)
    if not columns:
        return plan
    skipped = list(dict.fromkeys(op.type.value for op in dropped))
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=tuple(
            op for op in plan.operations if op.type not in SPARK_SCHEMA_READING_OPERATIONS
        ),
        notes=(*plan.notes, spark_time_columns_note(columns, skipped)),
    )
