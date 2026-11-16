"""Operator overrides for health-grade limits (#552): fleet setting and per table, grades only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from cryo.config import Settings
from cryo.db.models import AuditLog, Table, TableStats
from cryo.scanner import health
from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    THRESHOLD_OVERRIDES_KEY,
    compute_design_findings,
    compute_health_score,
    health_thresholds_for,
    threshold_override_sources,
    validate_threshold_overrides,
)
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb
from tests.test_table_config import _seed as _seed_config

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
SNAPSHOT_LABEL = "Snapshot retention buildup"

# Shapes that exercise every check: small files, file count, snapshots, deletes, manifests,
# partitions, metadata ratio, staleness, size scaling and the streaming snapshot window.
FIXTURES: list[tuple[dict[str, Any], int]] = [
    ({"total_files": 600, "total_size_gb": 10, "total_snapshots": 400}, 500),
    (
        {
            "total_files": 250_000,
            "total_size_gb": 4_000,
            "total_partitions": 2_000,
            "avg_files_per_partition": 125,
            "total_snapshots": 90,
            "manifest_count": 700,
            "total_delete_files": 3_000,
            "delete_file_pct": 25.0,
            "metadata_to_data_ratio": 0.2,
            "last_snapshot_committed_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
        },
        20_000,
    ),
    ({"total_files": 40, "total_size_gb": 0.5, "commits_per_day": 300, "is_streaming": True}, 3),
    ({}, 0),
]


def _labels(deductions: list[Any]) -> dict[str, int]:
    return {d["label"]: d["deduction"] for d in deductions}


@pytest.fixture()
def no_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "_fleet_overrides", lambda: {})


@pytest.mark.parametrize(("stats", "small"), FIXTURES)
def test_overrides_equal_to_the_defaults_change_nothing(no_fleet, stats, small) -> None:
    baseline = compute_health_score(stats, small, now=NOW)
    same = {**stats, THRESHOLD_OVERRIDES_KEY: dict(HEALTH_THRESHOLDS)}
    assert compute_health_score(same, small, now=NOW) == baseline
    assert health_thresholds_for(same) == health_thresholds_for(stats)
    assert compute_design_findings(same) == compute_design_findings(stats)


def test_without_overrides_nothing_is_reported_as_overridden(no_fleet) -> None:
    assert threshold_override_sources({}) == {}
    assert Settings().health_threshold_overrides == {}


def test_a_table_override_lifts_a_deduction_it_cannot_otherwise_clear(no_fleet) -> None:
    stats, small = FIXTURES[0]
    _score, _grade, before = compute_health_score(stats, small, now=NOW)
    assert _labels(before)[SNAPSHOT_LABEL] == 20
    lifted = {
        **stats,
        THRESHOLD_OVERRIDES_KEY: {"snapshot_count_warn": 500, "snapshot_count_crit": 1000},
    }
    score, _grade, after = compute_health_score(lifted, small, now=NOW)
    assert SNAPSHOT_LABEL not in _labels(after)
    assert score == _score + 20
    # Every other deduction keeps its weight.
    assert {k: v for k, v in _labels(before).items() if k != SNAPSHOT_LABEL} == _labels(after)


def test_the_table_override_wins_over_the_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        health, "_fleet_overrides", lambda: {"snapshot_count_warn": 300, "snapshot_count_crit": 450}
    )
    stats = {"total_snapshots": 400, THRESHOLD_OVERRIDES_KEY: {"snapshot_count_warn": 450}}
    _s, _g, deductions = compute_health_score(stats, 0, now=NOW)
    assert SNAPSHOT_LABEL not in _labels(deductions)
    sources = threshold_override_sources(stats)
    assert sources["snapshot_count_warn"] == {"limit": 450, "default": 50, "source": "table"}
    assert sources["snapshot_count_crit"]["source"] == "fleet"


def test_overrides_replace_the_base_before_size_scaling(no_fleet) -> None:
    big = {"total_size_gb": 400, THRESHOLD_OVERRIDES_KEY: {"manifest_count_warn": 200}}
    assert health_thresholds_for(big)["manifest_count_warn"] == 400


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"snapshot_count": 10}, "Unknown"),
        ({"snapshot_count_warn": -1}, "positive"),
        ({"snapshot_count_warn": True}, "positive"),
        ({"snapshot_count_warn": 500}, "below"),
    ],
)
def test_bad_overrides_are_refused(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        validate_threshold_overrides(overrides)
    with pytest.raises(ValidationError, match=message):
        Settings(health_threshold_overrides=overrides)


def test_a_fleet_override_error_does_not_echo_other_settings() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(health_threshold_overrides={"nope": 1}, secret_key="do-not-print-this-value")
    assert "do-not-print-this-value" not in str(excinfo.value)


def test_planner_gating_never_reads_the_overrides() -> None:
    import inspect

    from cryo.planner import planner

    assert "health_threshold" not in inspect.getsource(planner)


def _analysis(stats: dict[str, Any], small: int) -> Any:
    import dataclasses

    from cryo.scanner.analyze import TableAnalysis

    score, grade, deductions = compute_health_score(stats, small, now=NOW)
    fields: dict[str, Any] = {f.name: None for f in dataclasses.fields(TableAnalysis)}
    fields.update(
        {k: v for k, v in stats.items() if k in fields},
        schema_name="s",
        table_name="t",
        scan_at=NOW,
        grade=grade,
        overall_score=score,
        small_files_count=small,
        raw={**stats, "health_deductions": deductions},
    )
    return TableAnalysis(**fields)


def test_scan_persistence_leaves_tables_without_overrides_alone(no_fleet) -> None:
    from cryo.scanner.persistence import analysis_for_threshold_overrides

    analysis = _analysis(*FIXTURES[0])
    kept = analysis_for_threshold_overrides(analysis, None, "active")
    assert kept.overall_score == analysis.overall_score
    assert kept.raw == analysis.raw


def test_scan_persistence_regrades_with_the_table_overrides(no_fleet) -> None:
    from cryo.scanner.persistence import analysis_for_threshold_overrides

    analysis = _analysis(*FIXTURES[0])
    overrides = {"snapshot_count_warn": 500, "snapshot_count_crit": 1000}
    graded = analysis_for_threshold_overrides(analysis, overrides, "active")
    assert graded.overall_score == analysis.overall_score + 20
    assert SNAPSHOT_LABEL not in _labels(graded.raw["health_deductions"])
    assert graded.raw[THRESHOLD_OVERRIDES_KEY] == overrides
    assert graded.raw["health_threshold_sources"]["snapshot_count_crit"]["source"] == "table"


# --- API --------------------------------------------------------------------------------


def _seed_snapshots(api_db: ApiDb) -> uuid.UUID:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    stats = session.get(TableStats, table_id)
    assert stats is not None
    stats.total_snapshots = 400
    stats.last_snapshot_committed_at = None
    projected = {c: getattr(stats, c) for c in TableStats.__table__.columns.keys() if c != "raw"}
    score, grade, deductions = compute_health_score(
        projected, int(stats.small_files_count or 0), now=stats.scan_at
    )
    stats.overall_score = score
    stats.grade = grade
    stats.raw = {**(stats.raw or {}), "health_deductions": deductions}
    session.commit()
    session.close()
    return table_id


def test_setting_thresholds_requires_admin(api_db: ApiDb, no_fleet) -> None:
    table_id = _seed_snapshots(api_db)
    body = {"overrides": {"snapshot_count_crit": 1000}, "reason": "keep 300 snapshots"}
    for headers in (VIEWER_HEADERS, OPERATOR_HEADERS):
        response = api_db.client.put(
            f"/api/v1/tables/{table_id}/health-thresholds", headers=headers, json=body
        )
        assert response.status_code == 403


def test_invalid_thresholds_are_a_422(api_db: ApiDb, no_fleet) -> None:
    table_id = _seed_snapshots(api_db)
    response = api_db.client.put(
        f"/api/v1/tables/{table_id}/health-thresholds",
        headers=ADMIN_HEADERS,
        json={"overrides": {"snapshot_count_warn": 900}, "reason": "too high"},
    )
    assert response.status_code == 422
    assert "below" in response.text


def test_set_and_clear_are_audited_and_regrade(api_db: ApiDb, no_fleet) -> None:
    table_id = _seed_snapshots(api_db)
    url = f"/api/v1/tables/{table_id}/health-thresholds"
    overrides = {"snapshot_count_warn": 500, "snapshot_count_crit": 1000}
    response = api_db.client.put(
        url, headers=ADMIN_HEADERS, json={"overrides": overrides, "reason": "policy keeps 400"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] and body["previous"] == {}
    assert body["overrides"] == overrides
    assert body["effective"]["snapshot_count_crit"]["source"] == "table"
    assert body["score_after"] == body["score_before"] + 20

    viewed = api_db.client.get(url, headers=VIEWER_HEADERS)
    assert viewed.status_code == 200 and viewed.json()["overrides"] == overrides

    cleared = api_db.client.put(
        url, headers=ADMIN_HEADERS, json={"overrides": {}, "reason": "back to defaults"}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["score_after"] == body["score_before"]

    session = api_db.session_factory()
    table = session.get(Table, table_id)
    assert table is not None and table.health_threshold_overrides is None
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.table_id == table_id, AuditLog.action == "table_health_thresholds")
        .order_by(AuditLog.id)
    ).all()
    assert [r.result_metrics["after"] for r in rows] == [overrides, {}]
    assert rows[0].result_metrics["before"] == {}
    assert all(r.actor for r in rows)
    stats = session.get(TableStats, table_id)
    assert stats is not None and "health_threshold_sources" not in (stats.raw or {})
    session.close()
