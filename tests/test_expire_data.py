from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.guardrails import CORRECT_ORDER
from cryo.planner.planner import build_plan
from cryo.planner.ttl import parse_retention, plan_expire_data
from cryo.workflows.approvals import _is_parameter_safe  # noqa: PLC2701

NOW = datetime(2026, 10, 18, tzinfo=UTC)
LAST_COMMIT = datetime(2026, 1, 1, tzinfo=UTC)


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "expire_data_enabled": True,
        "expire_data_field": "event_date",
        "expire_data_retention": "7d",
        "expire_data_field_type": "date",
        "expire_data_now": NOW.isoformat(),
        "expire_data_last_commit_at": LAST_COMMIT.isoformat(),
    }
    params.update(overrides)
    return params


def test_parse_retention_accepts_days_and_hours() -> None:
    assert parse_retention("7d").days == 7
    assert parse_retention("12h").total_seconds() == 12 * 3600
    with pytest.raises(ValueError):
        parse_retention("0d")
    with pytest.raises(ValueError):
        parse_retention("7w")


def test_stalled_pipeline_keeps_the_tail_relative_to_last_commit() -> None:
    operation, notes = plan_expire_data(
        {"partition_col": "event_date"},
        (
            {"partition_value": "2025-12-20", "total_bytes": 100},
            {"partition_value": "2025-12-30", "total_bytes": 50},
        ),
        _params(),
        now=NOW,
    )
    assert operation is not None
    assert operation.params["cutoff"] == datetime(2025, 12, 25, tzinfo=UTC).isoformat()
    assert operation.params["estimated_partitions"] == 1
    assert operation.params["estimated_bytes"] == 100
    assert "2025-12-30" not in operation.params["cutoff"]
    assert any("Protected snapshots" not in note for note in notes)


def test_missing_last_commit_does_not_fall_back_to_now() -> None:
    operation, notes = plan_expire_data(
        {"partition_col": "event_date"},
        (),
        _params(expire_data_last_commit_at=None),
        now=NOW,
    )
    assert operation is None
    assert "wall clock" in notes[0]


def test_partition_level_refuses_a_non_partition_field() -> None:
    operation, notes = plan_expire_data(
        {"partition_col": "event_date"},
        (),
        _params(expire_data_field="updated_at"),
        now=NOW,
    )
    assert operation is None
    assert "expire_data_level=file" in notes[0]


def test_file_level_is_flagged_destructive() -> None:
    operation, notes = plan_expire_data(
        {"partition_col": "event_date"},
        (),
        _params(expire_data_field="updated_at", expire_data_level="file"),
        now=NOW,
        protected_snapshot_ids=[9, 4],
    )
    assert operation is not None
    assert operation.params["row_level"] is True
    assert operation.params["protected_snapshot_ids"] == [4, 9]
    assert any("requires approval" in note for note in notes)


def test_planner_orders_expire_data_before_snapshot_expiry() -> None:
    plan = build_plan(
        {"table_ref": "cat.db.events", "partition_col": "event_date"},
        (),
        Mode.LIGHT,
        _params(),
        protected_snapshot_ids=[3],
    )
    types = [op.type for op in plan.operations]
    assert types.index(OperationType.EXPIRE_DATA) < types.index(OperationType.EXPIRE_SNAPSHOTS)
    assert CORRECT_ORDER.index(OperationType.EXPIRE_DATA) < CORRECT_ORDER.index(
        OperationType.EXPIRE_SNAPSHOTS
    )


def test_disabled_expire_data_is_not_planned() -> None:
    plan = build_plan(
        {"table_ref": "cat.db.events", "partition_col": "event_date"},
        (),
        Mode.STANDARD,
        {"expire_data_enabled": False},
    )
    assert OperationType.EXPIRE_DATA not in {op.type for op in plan.operations}


def _plan(field_type: str = "date") -> MaintenancePlan:
    operation, _notes = plan_expire_data(
        {"partition_col": "event_date"},
        (),
        _params(expire_data_field_type=field_type),
        now=NOW,
    )
    assert operation is not None
    return MaintenancePlan("catalog.db.events", Mode.LIGHT, (operation,))


def test_trino_renders_partition_expire_data() -> None:
    rendered = render_trino(_plan())
    assert rendered[0].sql == (
        "DELETE FROM catalog.db.events WHERE \"event_date\" < DATE '2025-12-25'"
    )
    assert rendered[0].metadata["cutoff"].startswith("2025-12-25")
    assert rendered[0].metadata["estimated_bytes"] == 0


def test_spark_renders_partition_expire_data() -> None:
    rendered = render_spark(_plan())
    assert rendered[0].sql == (
        "DELETE FROM catalog.db.events WHERE `event_date` < DATE '2025-12-25'"
    )


def test_renderer_rejects_unsafe_field_names() -> None:
    plan = MaintenancePlan(
        "catalog.db.events",
        Mode.LIGHT,
        (
            Operation(
                OperationType.EXPIRE_DATA,
                {
                    "field": "event_date; drop",
                    "cutoff": LAST_COMMIT.isoformat(),
                    "field_type": "date",
                },
            ),
        ),
    )
    with pytest.raises(ValueError, match="simple identifier"):
        render_trino(plan)


def test_later_cutoff_is_not_covered_by_an_older_approval() -> None:
    approved = {"cutoff": "2025-12-01T00:00:00+00:00", "field": "event_date", "level": "partition"}
    requested = {"cutoff": "2025-12-25T00:00:00+00:00", "field": "event_date", "level": "partition"}
    assert _is_parameter_safe(requested, approved) is False
    assert _is_parameter_safe(approved, requested) is True
