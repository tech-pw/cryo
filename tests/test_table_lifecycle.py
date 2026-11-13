"""Table lifecycle (#519): archived tables skip the stale-commit deduction, drop data-rewrite
operations from every plan, and are set by an admin with an audited, typed confirmation."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from cryo.advisor.path_to_a import TableSnapshot, build_path_to_a
from cryo.db.models import AuditLog, Table, TableStats
from cryo.db.repositories.operation_exclusions import (
    excluded_operations,
    excluded_operations_by_table,
)
from cryo.domain.enums import Mode
from cryo.domain.lifecycle import (
    ARCHIVED_EXCLUDED_OPERATIONS,
    is_archive_candidate,
    lifecycle_excluded_operations,
)
from cryo.planner.planner import build_plan
from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    STALE_COMMITS_LABEL,
    apply_lifecycle,
    compute_health_score,
)
from cryo.scanner.persistence import analysis_for_lifecycle
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb
from tests.test_table_config import _seed as _seed_config

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
OLD_COMMIT = NOW - timedelta(days=120)

_STATS: dict[str, Any] = {
    "catalog": "iceberg",
    "schema_name": "backups",
    "table_name": "orders_backup",
    "small_files_count": 500,
    "total_files": 600,
    "total_size_gb": 10,
    "total_delete_files": 40,
    "delete_file_pct": 30.0,
    "total_snapshots": 400,
    "last_snapshot_committed_at": OLD_COMMIT.isoformat(),
}


def _labels(deductions: list[Any]) -> set[str]:
    return {d["label"] for d in deductions}


# --- health -----------------------------------------------------------------------------


def test_active_table_with_old_commit_gets_the_staleness_deduction() -> None:
    _score, _grade, deductions = compute_health_score(
        {"last_snapshot_committed_at": OLD_COMMIT}, 0, now=NOW
    )
    stale = [d for d in deductions if d["label"] == STALE_COMMITS_LABEL]
    assert stale and stale[0]["deduction"] == 20


def test_archived_table_skips_only_the_staleness_deduction() -> None:
    stats = {"last_snapshot_committed_at": OLD_COMMIT, "total_snapshots": 400}
    active_score, _, active = compute_health_score(stats, 0, now=NOW)
    archived_score, _, archived = compute_health_score(stats, 0, now=NOW, lifecycle="archived")
    assert _labels(active) - _labels(archived) == {STALE_COMMITS_LABEL}
    # Every other check still counts, at its unchanged weight.
    assert "Snapshot retention buildup" in _labels(archived)
    assert archived_score == active_score + 20


def test_weights_are_unchanged() -> None:
    assert HEALTH_THRESHOLDS["snapshot_age_days_warn"] == 7
    assert HEALTH_THRESHOLDS["snapshot_age_days_crit"] == 30


def test_apply_lifecycle_round_trips() -> None:
    stats = {"last_snapshot_committed_at": OLD_COMMIT, "total_snapshots": 400}
    score, grade, deductions = compute_health_score(stats, 0, now=NOW)
    archived = apply_lifecycle(deductions, lifecycle="archived", stats=stats, now=NOW)
    assert STALE_COMMITS_LABEL not in _labels(archived[2])
    assert archived[0] == score + 20
    restored = apply_lifecycle(archived[2], lifecycle="active", stats=stats, now=NOW)
    assert restored[0] == score and restored[1] == grade


def test_scanner_persistence_regrades_archived_tables() -> None:
    from cryo.scanner.analyze import TableAnalysis

    _score, _grade, deductions = compute_health_score(
        {"last_snapshot_committed_at": OLD_COMMIT}, 0, now=NOW
    )
    fields = {f.name: None for f in dataclasses.fields(TableAnalysis)}
    fields.update(
        schema_name="s",
        table_name="t",
        scan_at=NOW,
        grade="B",
        overall_score=80,
        raw={"health_deductions": deductions},
    )
    analysis = TableAnalysis(**fields)
    assert analysis_for_lifecycle(analysis, "active") is analysis
    archived = analysis_for_lifecycle(analysis, "archived")
    remaining = archived.raw["health_deductions"]
    assert STALE_COMMITS_LABEL not in _labels(remaining)
    assert len(remaining) == len(deductions) - 1
    assert archived.overall_score == 100 - sum(d["deduction"] for d in remaining)


# --- planner ----------------------------------------------------------------------------


def _types(plan: Any) -> set[str]:
    return {op.type.value for op in plan.operations}


def test_archived_plan_keeps_housekeeping_and_drops_rewrites() -> None:
    params = {"compute_statistics": True, "stats_compute_ndv": True}
    active = build_plan(_STATS, [], Mode.DEEP, params)
    assert "optimize" in _types(active)

    archived = build_plan(
        _STATS,
        [],
        Mode.DEEP,
        params,
        excluded_operations=lifecycle_excluded_operations("archived"),
    )
    assert not {op.value for op in ARCHIVED_EXCLUDED_OPERATIONS} & _types(archived)
    assert {"expire_snapshots", "remove_orphan_files", "optimize_manifests"} <= _types(archived)


def test_active_lifecycle_excludes_nothing() -> None:
    assert lifecycle_excluded_operations("active") == ()
    assert lifecycle_excluded_operations(None) == ()


def test_exclusion_repository_adds_lifecycle_exclusions(session) -> None:
    from cryo.db.models import Source

    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:6]}", type="rest", config={})
    archived = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="a", table_name="b", lifecycle="archived"
    )
    active = Table(id=uuid.uuid4(), source_id=source.id, schema_name="a", table_name="c")
    session.add_all([source, archived, active])
    session.commit()

    assert "optimize" in excluded_operations(session, archived.id)
    assert excluded_operations(session, active.id) == ()
    by_table = excluded_operations_by_table(session, [archived.id, active.id])
    assert "optimize" in by_table[archived.id]
    assert active.id not in by_table


# --- archive candidate ------------------------------------------------------------------


def test_archive_candidate_is_a_suggestion_with_both_signals() -> None:
    assert is_archive_candidate(lifecycle="active", days_since_commit=120, query_count_30d=0)
    assert not is_archive_candidate(lifecycle="active", days_since_commit=120, query_count_30d=5)
    assert not is_archive_candidate(lifecycle="active", days_since_commit=60, query_count_30d=0)
    assert not is_archive_candidate(lifecycle="archived", days_since_commit=120, query_count_30d=0)


# --- Path to A --------------------------------------------------------------------------


def _snapshot(**overrides: Any) -> TableSnapshot:
    stats = {"last_snapshot_committed_at": OLD_COMMIT, "total_files": 10}
    lifecycle = overrides.get("lifecycle", "active")
    score, grade, deductions = compute_health_score(stats, 0, now=NOW, lifecycle=lifecycle)
    base: dict[str, Any] = dict(
        table_name="orders_backup",
        score=score,
        grade=grade,
        deductions=deductions,
        total_files=10,
        total_partitions=1,
        total_size_bytes=10 * 1024**3,
        total_snapshots=5,
        manifest_count=2,
        commits_per_day=None,
        is_streaming=False,
        last_commit_at=OLD_COMMIT,
        table_target_bytes=512 * 1024**2,
        has_sort_order=True,
        write_distribution_mode="hash",
        params={},
        scheduled_operations=frozenset(),
        partition_fields=(),
        partitions=[],
        stats=stats,
        now=NOW,
        query_count_30d=0,
    )
    base.update(overrides)
    return TableSnapshot(**base)


def test_path_to_a_offers_mark_as_archived_with_projected_grade() -> None:
    path = build_path_to_a(_snapshot())
    assert path.grade != "A"
    step = next(s for s in path.steps if s.id == "mark_archived")
    assert step.title == "Mark as archived"
    assert step.action == {"type": "set_lifecycle", "lifecycle": "archived", "suggested": True}
    assert step.required_role == "admin" and step.confirm_text == "orders_backup"
    assert step.projected_grade == "A"
    assert STALE_COMMITS_LABEL in step.fixes
    assert not any(s.title == "Write to the table, or retire it" for s in path.steps)


def test_path_to_a_for_archived_table_has_no_rewrite_steps() -> None:
    path = build_path_to_a(_snapshot(lifecycle="archived", has_sort_order=False))
    ids = {s.id for s in path.steps}
    assert "mark_archived" not in ids
    assert not ids & {"sort_order", "table_properties", "partition_spec", "optimize_all"}
    assert any("archived" in note for note in path.notes)


# --- API --------------------------------------------------------------------------------


def _seed_stale(api_db: ApiDb) -> uuid.UUID:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    stats = session.get(TableStats, table_id)
    assert stats is not None
    score, grade, deductions = compute_health_score(
        {"last_snapshot_committed_at": OLD_COMMIT}, 0, now=stats.scan_at
    )
    stats.last_snapshot_committed_at = OLD_COMMIT
    stats.overall_score = score
    stats.grade = grade
    stats.raw = {**(stats.raw or {}), "health_deductions": deductions}
    session.commit()
    session.close()
    return table_id


def test_set_lifecycle_requires_admin(api_db: ApiDb) -> None:
    table_id = _seed_stale(api_db)
    body = {"lifecycle": "archived", "reason": "nightly backup", "confirm": "events"}
    for headers in (VIEWER_HEADERS, OPERATOR_HEADERS):
        response = api_db.client.put(
            f"/api/v1/tables/{table_id}/lifecycle", headers=headers, json=body
        )
        assert response.status_code == 403


def test_set_lifecycle_requires_typed_confirmation(api_db: ApiDb) -> None:
    table_id = _seed_stale(api_db)
    response = api_db.client.put(
        f"/api/v1/tables/{table_id}/lifecycle",
        headers=ADMIN_HEADERS,
        json={"lifecycle": "archived", "reason": "nightly backup", "confirm": "nope"},
    )
    assert response.status_code == 400


def test_archive_and_restore_are_audited_and_regrade(api_db: ApiDb) -> None:
    table_id = _seed_stale(api_db)
    url = f"/api/v1/tables/{table_id}/lifecycle"
    archived = api_db.client.put(
        url,
        headers=ADMIN_HEADERS,
        json={"lifecycle": "archived", "reason": "nightly backup", "confirm": "events"},
    )
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert body["changed"] and body["previous"] == "active" and body["lifecycle"] == "archived"
    assert body["score_after"] == body["score_before"] + 20

    table = api_db.client.get(f"/api/v1/tables/{table_id}", headers=VIEWER_HEADERS).json()
    assert table["lifecycle"] == "archived"
    assert table["lifecycle_reason"] == "nightly backup"
    assert table["archive_candidate"] is False

    listed = api_db.client.get(
        "/api/v1/tables",
        headers=VIEWER_HEADERS,
        params={"filter": '{"field": "lifecycle", "op": "=", "value": "archived"}'},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [str(table_id)]

    restored = api_db.client.put(
        url,
        headers=ADMIN_HEADERS,
        json={"lifecycle": "active", "reason": "writes resumed", "confirm": "events"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["score_after"] == body["score_before"]

    session = api_db.session_factory()
    audits = session.scalars(
        select(AuditLog)
        .where(AuditLog.table_id == table_id, AuditLog.action == "table_lifecycle")
        .order_by(AuditLog.id)
    ).all()
    assert [(a.result_metrics["before"], a.result_metrics["after"]) for a in audits] == [
        ("active", "archived"),
        ("archived", "active"),
    ]
    assert audits[0].actor and audits[0].result_metrics["reason"] == "nightly backup"
    session.close()


def test_path_to_a_apply_marks_archived(api_db: ApiDb) -> None:
    table_id = _seed_stale(api_db)
    path = api_db.client.get(f"/api/v1/tables/{table_id}/path-to-a", headers=VIEWER_HEADERS)
    assert path.status_code == 200, path.text
    assert "mark_archived" in {s["id"] for s in path.json()["steps"]}
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/path-to-a/apply",
        headers=ADMIN_HEADERS,
        json={"step_id": "mark_archived", "confirm": "events"},
    )
    assert response.status_code == 200, response.text
    session = api_db.session_factory()
    assert session.get(Table, table_id).lifecycle == "archived"
    assert session.get(AuditLog, response.json()["audit_log_id"]).action == "table_lifecycle"
    session.close()


def test_migration_is_additive() -> None:
    text = Path("migrations/versions/20261112_1200_table_lifecycle.py").read_text()
    assert 'down_revision = "20261112_2330"' in text
    assert "server_default=sa.text(\"'active'\")" in text
    assert 'schema="cryo"' in text
