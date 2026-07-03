from __future__ import annotations

from datetime import UTC, datetime

from cryo.domain.enums import Mode
from cryo.planner.guardrails import validate_operation_order
from cryo.planner.planner import build_plan
from cryo.scanner.signals import detect_streaming_from_history, metadata_to_data_ratio
from cryo.scheduler.cron import cron_is_due
from cryo.scheduler.routing import resolve_destination
from cryo.scheduler.rules import PresetConfig, TableCandidate


def test_cron_is_due_on_hour_boundary() -> None:
    now = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
    assert cron_is_due("0 14 * * *", now=now, last_run_at=now.replace(hour=13))


def test_routing_large_table_to_spark() -> None:
    preset = PresetConfig(
        id="large",
        name="large",
        destination_id="trino-default",
        guardrails={
            "routing_rules": [
                {
                    "predicate": {"field": "total_size_gb", "op": ">", "value": 100},
                    "destination": "spark-large",
                }
            ]
        },
    )
    table = TableCandidate("t1", "raw", "events", {"total_size_gb": 250})
    destinations = {
        "trino-default": {"id": "trino-default", "name": "trino-default"},
        "spark-large": {"id": "spark-large", "name": "spark-large"},
    }
    route = resolve_destination(table, preset, destinations)
    assert route.destination_id == "spark-large"


def test_guardrails_warn_on_out_of_order_ops() -> None:
    warnings = validate_operation_order(["optimize", "expire_snapshots"])
    assert warnings


def test_manifest_only_mode() -> None:
    plan = build_plan({"schema_name": "s", "table_name": "t"}, [], Mode.MANIFEST_ONLY, {})
    assert len(plan.operations) == 1
    assert plan.operations[0].type.value == "optimize_manifests"


def test_delete_ratio_trigger_plans_optimize() -> None:
    plan = build_plan(
        {"schema_name": "s", "table_name": "t", "small_files_count": 10, "delete_file_pct": 25},
        [],
        Mode.STANDARD,
        {"min_small_files": 128, "delete_ratio_trigger": 20},
    )
    assert any(op.type.value == "optimize" for op in plan.operations)


def test_streaming_detection_from_history() -> None:
    history = [
        {"scan_at": "2026-07-02T00:00:00+00:00", "total_snapshots": 10},
        {"scan_at": "2026-07-03T00:00:00+00:00", "total_snapshots": 40},
    ]
    is_streaming, commits_per_day = detect_streaming_from_history(history)
    assert is_streaming
    assert commits_per_day > 20


def test_metadata_ratio() -> None:
    assert metadata_to_data_ratio(50, 1000) == 0.05
