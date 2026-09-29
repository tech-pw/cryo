from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from cryo.ai.assistant.context import resolve
from cryo.ai.tools.bootstrap import register_builtin_tools
from cryo.ai.tools.read_identity import TableRefInput
from cryo.ai.tools.read_metadata import CommitTimeInput, list_tables_by_commit_time
from cryo.ai.tools.read_snapshots import list_snapshots
from cryo.ai.tools.registry import ToolContext, get_registry
from cryo.api.extensions_helpers import build_mcp_server
from cryo.api.security import Principal, Role
from cryo.config import Settings
from cryo.db.models import MaintenanceRun, Source, Table, TableStats


def _principal(role: Role) -> Principal:
    return Principal(subject=role.value, roles=frozenset({role}))


def _ctx(session, principal: Principal) -> ToolContext:
    return ToolContext(
        session=session,
        principal=principal,
        settings=Settings(),
        now=datetime(2026, 9, 28, 12, tzinfo=UTC),
        timezone="UTC",
    )


def _source(session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:8]}", type="rest", config={})
    session.add(source)
    return source


def _table(session, source: Source, name: str, **kwargs: object) -> Table:
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="raw",
        table_name=name,
        is_active=True,
        consecutive_scan_failures=0,
        **kwargs,
    )
    session.add(table)
    return table


def _stats(session, table: Table, *, commit: datetime | None, failures: int = 0) -> None:
    table.consecutive_scan_failures = failures
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime(2026, 9, 28, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            last_snapshot_committed_at=commit,
        )
    )


def test_registry_hides_operator_tools_from_viewer() -> None:
    register_builtin_tools()
    register_builtin_tools()
    registry = get_registry()
    viewer = _principal(Role.VIEWER)
    operator = _principal(Role.OPERATOR)
    viewer_names = {spec.name for spec in registry.visible(viewer)}
    operator_names = {spec.name for spec in registry.visible(operator)}
    assert "prepare_action" not in viewer_names
    assert "get_lake_health" in viewer_names
    assert "prepare_action" in operator_names
    assert all(spec.tier < 3 for spec in registry.specs.values())
    forbidden = registry.call("prepare_action", _ctx(None, viewer), {"action": "optimize"})
    assert forbidden.error_category == "forbidden"


def test_list_tables_by_commit_time_separates_unknown_buckets(session) -> None:
    source = _source(session)
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    stale = _table(session, source, "stale")
    recent = _table(session, source, "recent")
    _table(session, source, "never")
    failed = _table(session, source, "failed", last_scan_error="metadata missing")
    null_commit = _table(session, source, "null_commit")
    _stats(session, stale, commit=now - timedelta(days=30))
    _stats(session, recent, commit=now - timedelta(hours=1))
    _stats(session, failed, commit=now - timedelta(days=30), failures=3)
    _stats(session, null_commit, commit=None)
    session.commit()

    envelope = list_tables_by_commit_time(
        _ctx(session, _principal(Role.VIEWER)),
        CommitTimeInput(older_than_days=7, limit=50),
    )
    not_updated = {row["table_name"] for row in envelope.result["not_updated"]["rows"]}
    unknown = envelope.result["unknown"]
    never_names = {row["table_name"] for row in unknown["never_scanned"]["rows"]}
    failed_names = {row["table_name"] for row in unknown["scan_failed"]["rows"]}
    null_names = {row["table_name"] for row in unknown["null_commit_time"]["rows"]}

    assert not_updated == {"stale"}
    assert never_names == {"never"}
    assert failed_names == {"failed"}
    assert null_names == {"null_commit"}
    assert "recent" not in not_updated
    assert envelope.result["not_updated"]["total"] == 1
    assert unknown["never_scanned"]["total"] == 1
    assert unknown["scan_failed"]["total"] == 1
    assert unknown["null_commit_time"]["total"] == 1
    assert envelope.result["as_of"] == "scan"
    assert "not a live catalog" in envelope.warnings[0]


def test_run_preset_does_not_insert_run(session) -> None:
    before = session.scalar(select(func.count()).select_from(MaintenanceRun))
    server = build_mcp_server(session)
    result = server.call(
        "run_preset",
        {
            "confirm": True,
            "table_id": str(uuid.uuid4()),
            "destination_id": str(uuid.uuid4()),
            "preset_id": str(uuid.uuid4()),
        },
    )
    after = session.scalar(select(func.count()).select_from(MaintenanceRun))
    assert result == {
        "error": "not_authorized_here",
        "detail": "Use Ask Cryo action review. confirm=true is not authorization.",
    }
    assert before == after == 0


def test_list_snapshots_does_not_invent_ids(session) -> None:
    source = _source(session)
    table = _table(session, source, "events")
    _stats(session, table, commit=datetime(2026, 9, 1, tzinfo=UTC))
    session.commit()
    envelope = list_snapshots(
        _ctx(session, _principal(Role.VIEWER)),
        TableRefInput(table_id=table.id),
    )
    assert envelope.classification == "unknown"
    assert "snapshot_id" in envelope.unsupported_fields
    assert envelope.result["snapshots"] == []
    assert envelope.result["abstained"] is True
    assert "snapshot catalog" in envelope.result["abstain_reason"]


def test_resolve_does_not_guess_ambiguous_table(session) -> None:
    left = _source(session)
    right = _source(session)
    _table(session, left, "events")
    _table(session, right, "events")
    session.commit()
    resolved = resolve(
        session,
        _principal(Role.VIEWER),
        "status of raw.events",
        {"table_id": str(uuid.uuid4())},
        [],
    )
    assert resolved["ambiguous"] is True
    assert resolved["entities"] == []
    assert len(resolved["candidates"]) == 2


def test_prepare_action_tier3_is_not_a_tool(session) -> None:
    register_builtin_tools()
    before = session.scalar(select(func.count()).select_from(MaintenanceRun))
    envelope = get_registry().call(
        "prepare_action",
        _ctx(session, _principal(Role.OPERATOR)),
        {"action": "force-admit", "table_id": str(uuid.uuid4())},
    )
    after = session.scalar(select(func.count()).select_from(MaintenanceRun))
    assert envelope.error_category == "not_a_tool"
    assert envelope.result["ui_link"].startswith("/tables/")
    assert before == after
