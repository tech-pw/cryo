"""Tables page maintenance view: per-row status, triage filter fields, and the nightly funnel."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.api.routes import get_tables_maintenance_funnel, list_tables
from cryo.api.routes_funnel import latest_batch_funnel
from cryo.api.table_maintenance import FunnelRow, StateInputs, build_funnel, derive_state
from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableStats,
    TableStatsHistory,
)
from cryo.maintenance.skip_history import SkipEvaluation

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _inputs(**overrides: object) -> StateInputs:
    base: dict[str, object] = {
        "is_active": True,
        "has_stats": True,
        "needs_work": False,
        "scan_failures": 0,
        "scan_error_kind": None,
        "excluded": False,
        "retry_after": None,
        "missing_metadata_reason": None,
        "active_job_status": None,
        "last_finished_status": None,
        "selection_stage": None,
        "selection_reason": None,
    }
    base.update(overrides)
    return StateInputs(**base)  # type: ignore[arg-type]


def test_state_prefers_live_work_over_history() -> None:
    state, _ = derive_state(
        _inputs(active_job_status="running", last_finished_status="failed"), now=NOW
    )
    assert state == "running"
    state, reason = derive_state(_inputs(active_job_status="deferred"), now=NOW)
    assert (state, reason) == ("queued", "Deferred by the governor")


def test_state_explains_why_a_table_with_work_was_skipped() -> None:
    state, reason = derive_state(
        _inputs(needs_work=True, selection_stage="needs_work", selection_reason="cooldown"),
        now=NOW,
    )
    assert (state, reason) == ("skipped", "Cooldown after a recent run")
    state, reason = derive_state(
        _inputs(needs_work=True, selection_stage="eligible", selection_reason="budget_cap"),
        now=NOW,
    )
    assert (state, reason) == ("skipped", "Over the nightly budget")


def test_state_held_only_while_retry_window_is_open() -> None:
    held, _ = derive_state(_inputs(retry_after=NOW + timedelta(hours=1)), now=NOW)
    expired, _ = derive_state(_inputs(retry_after=NOW - timedelta(hours=1)), now=NOW)
    assert (held, expired) == ("held", "healthy")


def test_state_scan_problems_outrank_stale_job_history() -> None:
    state, _ = derive_state(_inputs(scan_failures=2, last_finished_status="failed"), now=NOW)
    assert state == "scan_failing"
    state, _ = derive_state(_inputs(has_stats=False), now=NOW)
    assert state == "not_scanned"


def test_funnel_links_conserve_flow() -> None:
    nodes, links = build_funnel(
        [
            FunnelRow("scanned", "evaluated", "no_work", None, 10),
            FunnelRow("scanned", "needs_work", "cooldown", None, 3),
            FunnelRow("scanned", "eligible", "budget_cap", None, 2),
            FunnelRow("scanned", "selected", None, 6, 1),
            FunnelRow("scanned", "selected", None, 2, 4),
            FunnelRow("scan_failing", None, None, None, 2),
            FunnelRow("scanned", None, None, None, 0),
        ]
    )
    counts = {node.id: node.count for node in nodes}
    assert counts["active"] == 22
    assert counts["needs_work"] == 10
    assert counts["selected"] == 5
    assert counts["failed"] == 1 and counts["succeeded"] == 4
    assert "not_evaluated" not in counts
    for node in nodes:
        outgoing = sum(link.value for link in links if link.source == node.id)
        assert outgoing in (0, node.count)
    assert {node.id: node.view for node in nodes}["skip_cooldown"] == "skipped"


def _seed(session):
    source = Source(name="lake", type="hive", config={})
    destination = Destination(id=uuid4(), name="trino", engine="trino", config={})
    session.add_all([source, destination])
    session.flush()

    def table(name: str, *, score: int, needs: bool = True, failures: int = 0) -> Table:
        row = Table(
            source_id=source.id,
            schema_name="s",
            table_name=name,
            is_active=True,
            consecutive_scan_failures=failures,
        )
        session.add(row)
        session.flush()
        session.add(
            TableStats(
                table_id=row.id,
                scan_at=datetime.now(UTC),
                needs_compaction=needs,
                grade="C",
                compaction_priority="MEDIUM",
                overall_score=score,
                total_files=100,
                small_files_count=50,
                total_size_gb=1.0,
            )
        )
        return row

    failed = table("failed", score=60)
    skipped = table("skipped", score=70)
    healthy = table("healthy", score=95, needs=False)
    broken = table("broken", score=80, failures=3)
    session.add(
        TableStatsHistory(
            table_id=failed.id,
            scan_at=datetime.now(UTC) - timedelta(days=8),
            overall_score=85,
            small_files_count=0,
            total_size_gb=1.0,
        )
    )
    run = MaintenanceRun(
        id=uuid4(),
        kind="batch",
        trigger="schedule",
        status="succeeded",
        summary={"pipeline": "batch"},
    )
    session.add(run)
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=failed.id,
            destination_id=destination.id,
            mode="full",
            status="failed",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            finished_at=datetime.now(UTC),
        )
    )
    evaluated = datetime.now(UTC)
    for row, stage, reason in [
        (failed, "selected", None),
        (skipped, "needs_work", "cooldown"),
        (healthy, "evaluated", "no_work"),
        (broken, "needs_work", "excluded"),
    ]:
        session.add(
            SkipEvaluation(
                id=uuid4(),
                run_id=run.id,
                table_id=row.id,
                stage=stage,
                reason=reason,
                evaluated_at=evaluated,
            )
        )
    session.commit()
    return run


def _list(session, predicate: dict | None = None, sort: str | None = None):
    return list_tables(
        session,
        filter=json.dumps(predicate) if predicate else None,
        sort=sort,
        limit=100,
        offset=0,
        cursor=None,
        freshness_breach=False,
        maintenance_overdue=False,
        p95_breach=False,
        no_reads_days=None,
    )


def test_list_tables_attaches_maintenance_status(session) -> None:
    run = _seed(session)
    rows = {item.table_name: item.maintenance for item in _list(session).items}
    assert rows["failed"].state == "failed"
    assert rows["failed"].last_run.run_id == run.id
    assert rows["failed"].score_delta_7d == -25
    assert rows["skipped"].state == "skipped"
    assert rows["skipped"].reason == "Cooldown after a recent run"
    assert rows["healthy"].state == "healthy"
    assert rows["broken"].state == "scan_failing"


def test_triage_filters_match_status(session) -> None:
    _seed(session)

    def names(predicate: dict) -> set[str]:
        return {item.table_name for item in _list(session, predicate).items}

    assert names({"field": "last_job_status", "op": "in", "value": ["failed", "partial"]}) == {
        "failed"
    }
    assert names({"field": "last_selection_reason", "op": "=", "value": "cooldown"}) == {"skipped"}
    assert names({"field": "score_delta_7d", "op": "<=", "value": -10}) == {"failed"}
    cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert names({"field": "scan_at", "op": "<", "value": cutoff}) == set()


def test_sort_by_score_delta(session) -> None:
    _seed(session)
    first = _list(session, sort="score_delta_7d").items[0]
    assert first.table_name == "failed"


def test_funnel_endpoint_reports_latest_nightly(session) -> None:
    run = _seed(session)
    funnel = get_tables_maintenance_funnel(session)
    counts = {node.id: node.count for node in funnel.nodes}
    assert funnel.run_id == run.id
    assert funnel.total_active == 4
    assert counts["scan_failing"] == 1
    assert counts["no_work"] == 1
    assert counts["skip_cooldown"] == 1
    assert counts["failed"] == 1


def test_scan_issues_view_predicate_compiles(session) -> None:
    _seed(session)
    cutoff = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    predicate = {
        "any": [
            {"field": "consecutive_scan_failures", "op": ">", "value": 0},
            {"field": "last_scan_error_kind", "op": "!=", "value": None},
            {"field": "scan_at", "op": "=", "value": None},
            {"field": "scan_at", "op": "<", "value": cutoff},
        ]
    }
    names = {item.table_name for item in _list(session, predicate).items}
    assert names == {"broken"}


def test_funnel_finds_nightly_run_behind_many_newer_runs(session) -> None:
    run = _seed(session)
    newer = datetime.now(UTC) + timedelta(minutes=1)
    session.add_all(
        MaintenanceRun(
            id=uuid4(),
            kind="scan" if i % 2 else "adhoc",
            trigger="api",
            status="succeeded",
            created_at=newer + timedelta(seconds=i),
            summary={"actor": "operator"},
        )
        for i in range(25)
    )
    session.commit()
    assert get_tables_maintenance_funnel(session).run_id == run.id
    assert latest_batch_funnel(session).run_id == str(run.id)
