"""PII column classification precision (issue #168) and Spark time-column gating (issue #169)."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cryo.ai.pii import classify_column, name_tokens
from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.execution import build_execution_plan
from cryo.planner.schema_gates import (
    gate_spark_time_columns,
    is_time_type_label,
    schema_time_columns,
    time_columns_in_stats,
)

# --- #168 -------------------------------------------------------------------------------

PII_COLUMNS = [
    "email",
    "user_email",
    "emailAddress",
    "phone",
    "phone_number",
    "customer_phone",
    "mobile",
    "mobile_number",
    "first_name",
    "firstName",
    "last_name",
    "full_name",
    "surname",
    "user_name",
    "ssn",
    "date_of_birth",
    "dob",
    "password",
    "credit_card_number",
    "passport_no",
    "salary",
    "home_address",
    "ip_address",
]

NON_PII_COLUMNS = [
    "automobile_type",
    "mobile_os_version",
    "mobile_app_build",
    "phone_brand",
    "phone_model",
    "table_name",
    "schema_name",
    "brand_name",
    "os_name",
    "product_name",
    "course_name",
    "device_model",
    "event_type",
    "email_verified",
    "email_count",
    "phone_count",
    "is_mobile",
    "file_count",
    "created_at",
    "order_id",
    "amount",
    "status",
]


@pytest.mark.parametrize("column", PII_COLUMNS)
def test_pii_columns_are_flagged(column: str) -> None:
    assert classify_column(column, "string") is not None


@pytest.mark.parametrize("column", NON_PII_COLUMNS)
def test_non_pii_columns_are_not_flagged(column: str) -> None:
    assert classify_column(column, "string") is None


def test_name_tokens_split_snake_kebab_and_camel_case() -> None:
    assert name_tokens("mobile_os_version") == ("mobile", "os", "version")
    assert name_tokens("automobile-type") == ("automobile", "type")
    assert name_tokens("customerPhoneNumber") == ("customer", "phone", "number")
    assert name_tokens("HTTPStatus2") == ("http", "status")


def _classify_in_fresh_process(seed: str) -> str:
    code = (
        "from cryo.ai.pii import classify_column\n"
        "names = ['name', 'contact', 'customer_contact_info', 'holder', 'zip']\n"
        "print([(m.pii_category, m.similarity) if m else None "
        "for m in map(classify_column, names)])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    return subprocess.run(
        [sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True
    ).stdout


def test_classification_is_stable_across_processes() -> None:
    # Embeddings hashed n-grams with the salted builtin hash(), so the same column scored
    # differently on every pod and restart.
    assert _classify_in_fresh_process("1") == _classify_in_fresh_process("2")


# --- #169 -------------------------------------------------------------------------------


def _field(name: str, field_type: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, field_type=field_type)


class _Type(SimpleNamespace):
    def __str__(self) -> str:
        return str(self.label)


def test_schema_time_columns_finds_top_level_and_nested_fields() -> None:
    schema = SimpleNamespace(
        fields=[
            _field("id", _Type(label="long")),
            _field("opened_at", _Type(label="time")),
            _field("created", _Type(label="timestamptz")),
            _field(
                "shift",
                _Type(
                    label="struct<start: time>",
                    fields=[_field("start", _Type(label="time"))],
                ),
            ),
            _field(
                "breaks",
                _Type(
                    label="list<time>",
                    element_field=_field("element", _Type(label="time")),
                ),
            ),
        ]
    )
    assert schema_time_columns(schema) == ["opened_at", "shift.start", "breaks.element"]


def test_schema_time_columns_on_a_real_pyiceberg_schema() -> None:
    from pyiceberg.schema import Schema
    from pyiceberg.types import (
        LongType,
        NestedField,
        StructType,
        TimestampType,
        TimeType,
    )

    schema = Schema(
        NestedField(1, "id", LongType(), required=False),
        NestedField(2, "at", TimeType(), required=False),
        NestedField(3, "ts", TimestampType(), required=False),
        NestedField(
            4,
            "window",
            StructType(NestedField(5, "close", TimeType(), required=False)),
            required=False,
        ),
    )
    assert schema_time_columns(schema) == ["at", "window.close"]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("time", True),
        ("list<time>", True),
        ("struct<a: time>", True),
        ("timestamp", False),
        ("timestamptz", False),
        ("timestamp_ns", False),
        ("string", False),
        (None, False),
    ],
)
def test_is_time_type_label(label: object, expected: bool) -> None:
    assert is_time_type_label(label) is expected


def test_time_columns_in_stats_reads_schema_walk_and_column_quality() -> None:
    stats = {
        "raw": {
            "time_columns": ["opened_at"],
            "column_quality": [
                {"column_name": "opened_at", "column_type": "time"},
                {"column_name": "closed_at", "column_type": "time"},
                {"column_name": "ts", "column_type": "timestamptz"},
            ],
        }
    }
    assert time_columns_in_stats(stats) == ["opened_at", "closed_at"]
    assert time_columns_in_stats({}) == []


def _plan(*types: OperationType) -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="cat.db.t",
        mode=Mode.DEEP,
        operations=tuple(Operation(type=t) for t in types),
        notes=(),
    )


TIME_STATS = {"raw": {"column_quality": [{"column_name": "opened_at", "column_type": "time"}]}}


def test_spark_plan_skips_data_reading_ops_on_time_columns() -> None:
    plan = _plan(
        OperationType.OPTIMIZE,
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE_MANIFESTS,
    )
    gated = gate_spark_time_columns(plan, engine=EngineType.SPARK_EMR, stats=TIME_STATS)
    assert [op.type for op in gated.operations] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE_MANIFESTS,
    ]
    (note,) = gated.notes
    assert note.startswith("unsupported_schema: time columns not supported by Spark")
    assert "opened_at" in note and "optimize, convert_deletes" in note


@pytest.mark.parametrize("engine", [EngineType.TRINO, EngineType.NATIVE, "trino"])
def test_non_spark_engines_keep_the_plan(engine: object) -> None:
    plan = _plan(OperationType.OPTIMIZE)
    assert gate_spark_time_columns(plan, engine=engine, stats=TIME_STATS) is plan


def test_spark_plan_without_time_columns_is_untouched() -> None:
    plan = _plan(OperationType.OPTIMIZE)
    stats = {"raw": {"column_quality": [{"column_name": "ts", "column_type": "timestamp"}]}}
    assert gate_spark_time_columns(plan, engine=EngineType.SPARK_EMR, stats=stats) is plan


def test_build_execution_plan_gates_spark_optimize_on_time_columns() -> None:
    class _Destination:
        engine = EngineType.SPARK_EMR
        config = {"catalog": "bench"}

    stats = {
        "schema_name": "demo",
        "table_name": "shifts",
        "small_files_count": 200,
        "total_size_gb": 10,
        "snapshot_count": 40,
        "raw": {"time_columns": ["opened_at"]},
    }
    plan = build_execution_plan(
        stats=stats,
        partitions=[],
        mode=Mode.DEEP,
        params={},
        destination=_Destination(),
        operations=None,
    )
    op_types = {op.type for op in plan.operations}
    assert OperationType.OPTIMIZE not in op_types
    assert OperationType.EXPIRE_SNAPSHOTS in op_types
    assert any(note.startswith("unsupported_schema:") for note in plan.notes)
