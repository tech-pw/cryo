"""Path to A: planner fixes it relies on, the advisor's projections, and its API."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from cryo.advisor.path_to_a import (
    GIB,
    MIB,
    TableSnapshot,
    build_path_to_a,
    points_for,
)
from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.guardrails import validate_params
from cryo.planner.partitions import PartitionFieldSpec, is_open_time_partition, partition_date
from cryo.planner.planner import build_plan
from cryo.scanner.analyze import HEALTH_SMALL_FILE_BYTES
from cryo.scanner.health import compute_health_score
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
DAY_FIELD = PartitionFieldSpec(
    base_column="event_ts",
    transform=PartitionTransform.DAY,
    col_type="timestamp",
    partition_field="event_ts_day",
)
_EPOCH = date(1970, 1, 1)


def _day_stats() -> dict:
    return {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 1000,
        "total_size_gb": 250,
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
    }


def _optimize(plan: MaintenancePlan) -> list[Operation]:
    return [op for op in plan.operations if op.type is OperationType.OPTIMIZE]


# --- planner ---------------------------------------------------------------------------


def test_planner_compacts_to_the_tables_own_target_size() -> None:
    stats = {**_day_stats(), "target_file_size_bytes": 512 * MIB}
    partitions = [
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 300,
            "partition_size_gb": 5,
        }
    ]
    plan = build_plan(stats, partitions, Mode.STANDARD, {"min_small_files": 128})
    op = _optimize(plan)[0]
    assert op.params["target_file_size_bytes"] == 512 * MIB
    assert op.params["file_size_mb"] == 512
    assert any("table's own target file size (512 MB)" in note for note in plan.notes)


def test_planner_keeps_explicit_target_and_honours_opt_out() -> None:
    stats = {**_day_stats(), "target_file_size_bytes": 512 * MIB}
    partitions = [
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 300,
            "partition_size_gb": 5,
        }
    ]
    explicit = build_plan(stats, partitions, Mode.STANDARD, {"target_file_size_bytes": 128 * MIB})
    assert _optimize(explicit)[0].params["target_file_size_bytes"] == 128 * MIB
    opted_out = build_plan(
        stats, partitions, Mode.STANDARD, {"use_table_target_size": False, "file_size_mb": 256}
    )
    assert "target_file_size_bytes" not in _optimize(opted_out)[0].params


def test_planner_skips_the_current_day_partition() -> None:
    today = datetime.now(UTC).date()
    partitions = [
        {
            "partition_value": today.isoformat(),
            "small_files_in_partition_count": 300,
            "partition_size_gb": 5,
        },
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 300,
            "partition_size_gb": 5,
        },
    ]
    plan = build_plan(_day_stats(), partitions, Mode.STANDARD, {"min_small_files": 128})
    rendered = " ".join(str(op.predicates) for op in _optimize(plan))
    assert today.isoformat() not in rendered
    assert any(f"Skipped open partition {today.isoformat()}" in note for note in plan.notes)

    opted_out = build_plan(
        _day_stats(),
        partitions,
        Mode.STANDARD,
        {"min_small_files": 128, "skip_open_time_partition": False},
    )
    assert not any("Skipped open partition" in note for note in opted_out.notes)


def test_retention_hours_tightens_the_expire_window() -> None:
    plan = build_plan(_day_stats(), [], Mode.LIGHT, {"retention_days": 7, "retention_hours": 6})
    expire = next(op for op in plan.operations if op.type is OperationType.EXPIRE_SNAPSHOTS)
    assert expire.params["retention_hours"] == 6
    looser = build_plan(_day_stats(), [], Mode.LIGHT, {"retention_days": 1, "retention_hours": 48})
    expire = next(op for op in looser.operations if op.type is OperationType.EXPIRE_SNAPSHOTS)
    assert "retention_hours" not in expire.params


def test_new_params_are_validated() -> None:
    assert validate_params({"retention_hours": 6, "skip_open_time_partition": False}) == []
    assert validate_params({"retention_hours": 0}) == ["params.retention_hours must be at least 1"]
    assert validate_params({"use_table_target_size": "no"}) == [
        "params.use_table_target_size must be a boolean"
    ]


def test_engines_render_retention_hours() -> None:
    from cryo.engines.flink.config import FlinkMaintenanceConfig
    from cryo.engines.spark.sql import render as render_spark
    from cryo.engines.trino.sql import render as render_trino

    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.LIGHT,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {"retention_days": 1, "retention_hours": 6, "retain_last": 1},
            ),
        ),
    )
    spark_sql = render_spark(plan)[0].sql
    match = re.search(r"older_than => TIMESTAMP '([\d\- :]+)'", spark_sql)
    assert match is not None, spark_sql
    cutoff = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert abs((cutoff - (datetime.now(UTC) - timedelta(hours=6))).total_seconds()) < 30

    assert "retention_threshold => '6h'" in render_trino(plan)[0].sql

    config = FlinkMaintenanceConfig.from_cryo_params(
        "demo.events", "iceberg", "rest", None, {"retention_days": 1, "retention_hours": 6}
    )
    assert config.expire_max_snapshot_age_ms == 6 * 3600 * 1000


# --- partition values ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transform", "value", "expected"),
    [
        (PartitionTransform.DAY, "2026-10-20", date(2026, 10, 20)),
        (PartitionTransform.DAY, (date(2026, 10, 20) - _EPOCH).days, date(2026, 10, 20)),
        (PartitionTransform.MONTH, "2026-10", date(2026, 10, 1)),
        (PartitionTransform.MONTH, (2026 - 1970) * 12 + 9, date(2026, 10, 1)),
        (PartitionTransform.YEAR, "2026", date(2026, 1, 1)),
        (PartitionTransform.YEAR, 56, date(2026, 1, 1)),
        (PartitionTransform.DAY, "not-a-date", None),
    ],
)
def test_partition_date_reads_strings_and_transform_integers(transform, value, expected) -> None:
    spec = PartitionFieldSpec("event_ts", transform, "timestamp", f"event_ts_{transform.value}")
    assert partition_date(spec, {"partition_values": {spec.partition_field: value}}) == expected


def test_open_partition_matches_only_the_current_period() -> None:
    today = date(2026, 10, 20)
    assert is_open_time_partition(
        [DAY_FIELD], {"partition_values": {"event_ts_day": "2026-10-20"}}, today
    )
    assert not is_open_time_partition(
        [DAY_FIELD], {"partition_values": {"event_ts_day": "2026-10-19"}}, today
    )


def test_health_small_file_size_matches_the_scanner() -> None:
    from cryo.advisor.path_to_a import HEALTH_SMALL_FILE_BYTES as advisor_value

    assert advisor_value == HEALTH_SMALL_FILE_BYTES


# --- advisor ---------------------------------------------------------------------------


def _deductions(stats: dict) -> tuple[int, str, list]:
    return compute_health_score(stats, 0, now=NOW)


def _over_partitioned(days: int = 1000, per_day_mb: int = 17) -> TableSnapshot:
    """A day-partitioned table whose partitions are each far below one target file."""
    rows = []
    start = date(2023, 1, 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        rows.append(
            {
                "partition_values": {"event_ts_day": day.isoformat()},
                "files_per_partition": 3 if offset % 50 else 900,
                "small_files_in_partition_count": 3 if offset % 50 else 900,
                "partition_size_gb": per_day_mb / 1024,
            }
        )
    total_partitions = days * 70
    total_files = sum(r["files_per_partition"] for r in rows) * 70
    stats = {
        "total_files": total_files,
        "total_snapshots": 400,
        "total_partitions": total_partitions,
        "partition_skew_ratio": 300.0,
        "avg_files_per_partition": total_files / total_partitions,
        "last_snapshot_committed_at": NOW,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 512 * MIB,
    }
    score, grade, deductions = _deductions(stats)
    return TableSnapshot(
        table_name="events",
        score=score,
        grade=grade,
        deductions=deductions,
        total_files=total_files,
        total_partitions=total_partitions,
        total_size_bytes=int(days * 70 * per_day_mb * MIB),
        total_snapshots=400,
        manifest_count=10,
        commits_per_day=180.0,
        is_streaming=False,
        last_commit_at=NOW,
        table_target_bytes=512 * MIB,
        has_sort_order=True,
        write_distribution_mode="hash",
        params={"retention_days": 7, "min_small_files": 128},
        scheduled_operations=frozenset({"optimize", "expire_snapshots"}),
        partition_fields=(DAY_FIELD,),
        partitions=rows,
        partition_skew_ratio=300.0,
        query_filters={"event_ts": 40},
        now=NOW,
    )


def test_projection_without_steps_matches_the_scorer() -> None:
    snapshot = _over_partitioned()
    path = build_path_to_a(snapshot)
    assert path.score == snapshot.score
    assert snapshot.grade == "F"
    labels = {d["label"] for d in snapshot.deductions}
    assert {"Snapshot retention buildup", "Partition volume skew", "Excessive file count"} <= labels


def test_points_follow_the_scorer_thresholds() -> None:
    assert points_for("total_files", 9_999) == 0
    assert points_for("total_files", 10_000) == 8
    assert points_for("total_files", 100_000) == 20


def test_over_partitioned_table_needs_a_partition_change() -> None:
    path = build_path_to_a(_over_partitioned())
    by_id = {step.id: step for step in path.steps}

    expire = by_id["expire_snapshots"]
    assert expire.kind == "automatic" and expire.in_schedule
    assert expire.action == {"type": "run_operations", "operations": ["expire_snapshots"]}

    retention = by_id["retention_hours"]
    assert retention.kind == "approval"
    assert retention.action == {"type": "set_overrides", "overrides": {"retention_hours": 6}}
    assert "Snapshot retention buildup" in retention.fixes

    design = by_id["partition_spec"]
    assert design.kind == "design"
    assert design.required_role == "admin"
    assert design.confirm_text == "events"
    assert design.action == {
        "type": "partition_spec",
        "remove": ["event_ts_day"],
        "add": [{"source_column": "event_ts", "transform": "month"}],
        "dropped": [],
    }
    assert design.title == "Partition by month of event_ts instead of day"
    assert "40 recorded queries filter on event_ts." in design.detail
    assert "median partition holds 17 MB" in design.detail
    assert design.projected_score > by_id["retention_hours"].projected_score
    assert path.cryo_score >= design.projected_score
    assert path.cryo_score > path.score


def test_compaction_alone_is_reported_honestly() -> None:
    path = build_path_to_a(_over_partitioned())
    routine = next(step for step in path.steps if step.id == "optimize")
    assert routine.kind == "automatic"
    assert "Excessive file count" not in routine.fixes
    assert "Partition volume skew" not in routine.fixes or routine.projected_score > path.score


def test_steps_are_ordered_by_kind() -> None:
    order = {"automatic": 0, "approval": 1, "design": 2, "external": 3}
    kinds = [order[step.kind] for step in build_path_to_a(_over_partitioned()).steps]
    assert kinds == sorted(kinds)


def test_healthy_table_has_no_steps() -> None:
    stats = {
        "total_files": 100,
        "last_snapshot_committed_at": NOW,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 512 * MIB,
    }
    score, grade, deductions = _deductions(stats)
    snapshot = TableSnapshot(
        table_name="events",
        score=score,
        grade=grade,
        deductions=deductions,
        total_files=100,
        total_partitions=1,
        total_size_bytes=10 * GIB,
        total_snapshots=3,
        manifest_count=2,
        commits_per_day=1.0,
        is_streaming=False,
        last_commit_at=NOW,
        table_target_bytes=512 * MIB,
        has_sort_order=True,
        write_distribution_mode="hash",
        params={},
        scheduled_operations=frozenset(),
        partition_fields=(),
        partitions=[],
        now=NOW,
    )
    path = build_path_to_a(snapshot)
    assert (path.grade, path.steps, path.reachable_grade) == ("A", [], "A")


def test_unfixable_deductions_become_external_steps() -> None:
    snapshot = _over_partitioned()
    deductions = [
        *snapshot.deductions,
        {"label": "High velocity streaming", "deduction": 8, "severity": "warning", "reason": ""},
    ]
    path = build_path_to_a(
        TableSnapshot(
            **{**snapshot.__dict__, "deductions": deductions, "score": snapshot.score - 8}
        )
    )
    external = [step for step in path.steps if step.kind == "external"]
    assert [step.id for step in external] == ["streaming_writer"]
    assert external[0].action is None
    assert path.reachable_score == path.cryo_score + 8


# --- API -------------------------------------------------------------------------------


def _seed(session, *, with_stats: bool = True) -> uuid.UUID:
    from cryo.db.models import (
        Destination,
        Preset,
        Source,
        Table,
        TableAssignment,
        TablePartition,
        TableStats,
    )

    snapshot = _over_partitioned(days=200)
    source_id, table_id = uuid.uuid4(), uuid.uuid4()
    session.add(
        Source(
            id=source_id,
            name="lake",
            type="rest",
            config={"type": "rest", "uri": "http://localhost"},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="demo",
            table_name="events",
            first_seen_at=NOW,
            is_active=True,
        )
    )
    destination = Destination(name="spark", engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    preset = Preset(
        name=f"daily-{table_id}",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize", "expire_snapshots"],
        params={"retention_days": 7},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    session.add(
        TableAssignment(table_id=table_id, preset_id=preset.id, pinned=True, param_overrides={})
    )
    if with_stats:
        session.add(
            TableStats(
                table_id=table_id,
                scan_at=NOW,
                needs_compaction=True,
                compaction_priority="HIGH",
                grade=snapshot.grade,
                overall_score=snapshot.score,
                total_files=snapshot.total_files,
                small_files_count=snapshot.total_files,
                total_partitions=snapshot.total_partitions,
                partition_skew_ratio=300.0,
                total_size_gb=snapshot.total_size_bytes / GIB,
                total_snapshots=snapshot.total_snapshots,
                manifest_count=10,
                write_distribution_mode="hash",
                has_sort_order=True,
                target_file_size_bytes=512 * MIB,
                query_count_30d=0,
                raw={
                    "health_deductions": list(snapshot.deductions),
                    "commits_per_day": 180.0,
                    "partition_fields": [
                        {
                            "base_column": "event_ts",
                            "partition_field": "event_ts_day",
                            "transform": "day",
                            "col_type": "timestamp",
                        }
                    ],
                },
            )
        )
        for index, row in enumerate(snapshot.partitions, start=1):
            session.add(
                TablePartition(
                    id=index,
                    table_id=table_id,
                    scan_at=NOW,
                    partition_value=row["partition_values"]["event_ts_day"],
                    partition_values=row["partition_values"],
                    partition_fields=[],
                    files_per_partition=row["files_per_partition"],
                    small_files_in_partition_count=row["small_files_in_partition_count"],
                    partition_size_gb=row["partition_size_gb"],
                )
            )
    session.commit()
    return table_id


def test_get_path_to_a(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    response = api_db.client.get(f"/api/v1/tables/{table_id}/path-to-a", headers=VIEWER_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grade"] == "F"
    ids = [step["id"] for step in body["steps"]]
    assert "retention_hours" in ids and "partition_spec" in ids


def test_get_path_to_a_needs_a_scan(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, with_stats=False)
    session.close()
    response = api_db.client.get(f"/api/v1/tables/{table_id}/path-to-a", headers=VIEWER_HEADERS)
    assert response.status_code == 409


def test_apply_override_step_needs_admin_and_merges(api_db: ApiDb) -> None:
    from cryo.db.models import AuditLog, TableAssignment

    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    url = f"/api/v1/tables/{table_id}/path-to-a/apply"
    denied = api_db.client.post(url, headers=OPERATOR_HEADERS, json={"step_id": "retention_hours"})
    assert denied.status_code == 403

    response = api_db.client.post(url, headers=ADMIN_HEADERS, json={"step_id": "retention_hours"})
    assert response.status_code == 200, response.text
    session = api_db.session_factory()
    assert session.get(TableAssignment, table_id).param_overrides == {"retention_hours": 6}
    audit = session.get(AuditLog, response.json()["audit_log_id"])
    assert audit.action == "path_to_a_override"
    session.close()


def test_apply_rejects_unknown_step(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/path-to-a/apply",
        headers=ADMIN_HEADERS,
        json={"step_id": "nope"},
    )
    assert response.status_code == 409


def test_partition_change_needs_typed_confirmation(api_db: ApiDb) -> None:
    from cryo.db.models import AuditLog

    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    url = f"/api/v1/tables/{table_id}/path-to-a/apply"
    unconfirmed = api_db.client.post(url, headers=ADMIN_HEADERS, json={"step_id": "partition_spec"})
    assert unconfirmed.status_code == 400

    with patch(
        "cryo.api.routes_path_to_a.change_partition_spec",
        return_value={"removed": ["day(event_ts)"], "added": ["month(event_ts)"], "spec_id": 1},
    ) as change:
        response = api_db.client.post(
            url, headers=ADMIN_HEADERS, json={"step_id": "partition_spec", "confirm": "events"}
        )
    assert response.status_code == 200, response.text
    assert change.call_args.kwargs == {
        "remove": ["event_ts_day"],
        "add": [{"source_column": "event_ts", "transform": "month"}],
    }
    session = api_db.session_factory()
    audit = session.get(AuditLog, response.json()["audit_log_id"])
    assert audit.action == "partition_spec_change"
    session.close()


def _iceberg_table(spec, schema) -> MagicMock:
    table = MagicMock()
    table.spec.return_value = spec
    table.schema.return_value = schema
    table.refresh.return_value.spec.return_value.spec_id = 1
    return table


def test_change_partition_spec_swaps_and_drops_fields() -> None:
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import DayTransform, IdentityTransform, MonthTransform
    from pyiceberg.types import NestedField, StringType, TimestampType

    from cryo.advisor import partition_change

    schema = Schema(
        NestedField(1, "event_ts", TimestampType()), NestedField(2, "kind", StringType())
    )
    spec = PartitionSpec(
        PartitionField(2, 1000, IdentityTransform(), "kind"),
        PartitionField(1, 1001, DayTransform(), "event_ts_day"),
    )
    table = _iceberg_table(spec, schema)
    update = table.update_spec.return_value.__enter__.return_value
    with patch.object(partition_change, "_load_iceberg_table", return_value=table):
        result = partition_change.change_partition_spec(
            MagicMock(),
            uuid.uuid4(),
            remove=["kind", "event_ts_day"],
            add=[{"source_column": "event_ts", "transform": "month"}],
        )
    assert [c.args for c in update.remove_field.call_args_list] == [("kind",), ("event_ts_day",)]
    column, transform, name = update.add_field.call_args.args
    assert (column, name) == ("event_ts", "event_ts_month")
    assert isinstance(transform, MonthTransform)
    assert result["removed"] == ["identity(kind)", "day(event_ts)"]
    assert result["added"] == ["month(event_ts)"]


def test_change_partition_spec_refuses_stale_or_unpartitioning_changes() -> None:
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import DayTransform
    from pyiceberg.types import NestedField, TimestampType

    from cryo.advisor import partition_change

    schema = Schema(NestedField(1, "event_ts", TimestampType()))
    spec = PartitionSpec(PartitionField(1, 1000, DayTransform(), "event_ts_day"))
    table = _iceberg_table(spec, schema)
    with patch.object(partition_change, "_load_iceberg_table", return_value=table):
        with pytest.raises(partition_change.LayoutChangeError, match="no field event_ts_hour"):
            partition_change.change_partition_spec(
                MagicMock(), uuid.uuid4(), remove=["event_ts_hour"], add=[]
            )
        with pytest.raises(partition_change.LayoutChangeError, match="unpartitioned"):
            partition_change.change_partition_spec(
                MagicMock(), uuid.uuid4(), remove=["event_ts_day"], add=[]
            )
    table.update_spec.assert_not_called()


def test_commit_rate_from_history_skips_close_and_shrinking_pairs() -> None:
    from cryo.advisor.path_to_a import commit_rate_from_history

    history = [
        {"scan_at": "2026-10-21T00:00:00+00:00", "total_snapshots": 200},
        {"scan_at": "2026-10-22T00:00:00+00:00", "total_snapshots": 270},
        {"scan_at": "2026-10-22T00:00:01+00:00", "total_snapshots": 270},
        {"scan_at": "2026-10-23T00:00:00+00:00", "total_snapshots": 120},
        {"scan_at": "2026-10-24T00:00:00+00:00", "total_snapshots": 196},
    ]
    assert commit_rate_from_history(history) == pytest.approx(73.0, abs=0.1)
    assert commit_rate_from_history(history[:1]) is None


def test_unknown_commit_rate_leaves_snapshots_unprojected() -> None:
    snapshot = TableSnapshot(**{**_over_partitioned().__dict__, "commits_per_day": None})
    path = build_path_to_a(snapshot)
    expire = next(step for step in path.steps if step.id == "expire_snapshots")
    assert expire.confidence == "assumed"
    assert expire.fixes == []
    assert "retention_hours" not in {step.id for step in path.steps}
    assert any("couldn't measure this table's commit rate" in note for note in path.notes)


def test_skewed_identity_column_is_dropped_and_sorted() -> None:
    kind = PartitionFieldSpec("kind", PartitionTransform.IDENTITY, "string", "kind")
    rows = []
    for offset in range(300):
        day = (date(2025, 1, 1) + timedelta(days=offset)).isoformat()
        for name, mb in (("big", 4096), ("small", 1), ("tiny", 1)):
            rows.append(
                {
                    "partition_values": {"kind": name, "event_ts_day": day},
                    "files_per_partition": 8 if name == "big" else 2,
                    "small_files_in_partition_count": 0 if name == "big" else 2,
                    "partition_size_gb": mb / 1024,
                }
            )
    base = _over_partitioned()
    deductions = [
        {"label": "Partition volume skew", "deduction": 20, "severity": "critical", "reason": ""}
    ]
    snapshot = TableSnapshot(
        **{
            **base.__dict__,
            "deductions": deductions,
            "score": 80,
            "grade": "B",
            "total_partitions": len(rows),
            "total_files": sum(r["files_per_partition"] for r in rows),
            "total_size_bytes": int(sum(r["partition_size_gb"] for r in rows) * GIB),
            "partition_skew_ratio": 100.0,
            "partition_fields": (kind, DAY_FIELD),
            "partitions": rows,
            "query_filters": {"kind": 12},
        }
    )
    path = build_path_to_a(snapshot)
    by_id = {step.id: step for step in path.steps}
    design = by_id["partition_spec"]
    assert design.action is not None
    assert design.action["remove"] == ["kind"]
    assert design.action["dropped"] == ["kind"]
    assert "Partition volume skew" in design.fixes
    assert "12 recorded queries filter on kind." in design.detail
    sort = by_id["sort_order"]
    assert sort.action == {"type": "sort_order", "columns": ["kind"]}
    assert sort.confirm_text == "events"
