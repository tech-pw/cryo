"""Nightly maintenance-status reasons, without a catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cryo.api.deps import get_current_principal, get_tenant_session
from cryo.api.routes_table_maintenance import (
    LAST_ACTIONS_NOTE,
    NEXT_NIGHTLY_NOTE,
    OVER_BUDGET_WHERE,
    router,
)
from cryo.api.security import Principal, Role
from cryo.db.models import Table
from cryo.maintenance.selection import (
    DEFAULT_MIN_DELETE_FILE_PCT,
    MaintenanceBlock,
    MaintenanceEligibilityFacts,
    MaintenancePredicate,
    maintenance_blocking_reasons,
    schema_in_maintenance_allow_list,
)
from cryo.scanner.persistence import scan_retry_due_at
from tests.auth_helpers import auth_header, ensure_test_jwt_secret

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
COOLDOWN_HOURS = 72.0
MAX_STATS_AGE_DAYS = 3.0


def _facts(**overrides: Any) -> MaintenanceEligibilityFacts:
    base = MaintenanceEligibilityFacts(
        is_active=True,
        source_enabled=True,
        source_present=True,
        last_scan_error_kind=None,
        maintenance_retry_after=None,
        stats_present=True,
        scan_at=NOW,
        schema_name="sales",
        maintenance_schema_names=(),
        needs_compaction=True,
        delete_file_pct=0.0,
        last_successful_finished_at=None,
        consecutive_scan_failures=0,
        last_scan_error_at=None,
        scan_retry_due_at=None,
    )
    return MaintenanceEligibilityFacts(**{**base.__dict__, **overrides})


def _reasons(facts: MaintenanceEligibilityFacts, **kwargs: Any) -> list[Any]:
    return maintenance_blocking_reasons(
        facts,
        now=kwargs.pop("now", NOW),
        cooldown_hours=kwargs.pop("cooldown_hours", COOLDOWN_HOURS),
        max_stats_age_days=kwargs.pop("max_stats_age_days", MAX_STATS_AGE_DAYS),
        **kwargs,
    )


def _codes(facts: MaintenanceEligibilityFacts, **kwargs: Any) -> list[str]:
    return [reason.code.value for reason in _reasons(facts, **kwargs)]


def test_inactive_blocks_with_the_flag_value() -> None:
    reasons = _reasons(_facts(is_active=False))

    assert len(reasons) == 1
    assert reasons[0].code is MaintenanceBlock.INACTIVE
    assert reasons[0].blocks_nightly_selection is True
    assert reasons[0].predicate is MaintenancePredicate.LOAD_CANDIDATES
    assert reasons[0].values == {"is_active": False}
    assert "is_active=false" in reasons[0].summary


def test_source_disabled_blocks_with_the_flag_value() -> None:
    reasons = _reasons(_facts(source_enabled=False))

    assert reasons[0].code is MaintenanceBlock.SOURCE_DISABLED
    assert reasons[0].values == {"source_present": True, "source_enabled": False}
    assert "enabled=false" in reasons[0].summary


def test_missing_source_is_the_same_inner_join_gate() -> None:
    reasons = _reasons(_facts(source_present=False, source_enabled=False))

    assert reasons[0].code is MaintenanceBlock.SOURCE_DISABLED
    assert reasons[0].values["source_present"] is False
    assert MaintenanceBlock.SCHEMA_NOT_ALLOWED.value not in _codes(
        _facts(source_present=False, source_enabled=False, schema_name="other")
    )


def test_stats_older_than_the_selection_window_include_n_and_cutoff() -> None:
    scanned = NOW - timedelta(days=MAX_STATS_AGE_DAYS, seconds=1)
    reasons = _reasons(_facts(scan_at=scanned))

    reason = reasons[0]
    assert reason.code is MaintenanceBlock.STATS_STALE
    assert reason.values["max_stats_age_days"] == MAX_STATS_AGE_DAYS
    assert reason.values["scan_at"] == scanned.isoformat()
    assert reason.values["cutoff"] == (NOW - timedelta(days=MAX_STATS_AGE_DAYS)).isoformat()
    assert f"older than {MAX_STATS_AGE_DAYS} days" in reason.summary


def test_stats_age_uses_the_callers_n_not_a_hardcoded_three() -> None:
    scanned = NOW - timedelta(days=5)
    assert _codes(_facts(scan_at=scanned), max_stats_age_days=10) == []
    assert _codes(_facts(scan_at=scanned), max_stats_age_days=3) == [
        MaintenanceBlock.STATS_STALE.value
    ]


def test_stats_exactly_at_the_cutoff_are_fresh() -> None:
    cutoff = NOW - timedelta(days=MAX_STATS_AGE_DAYS)
    assert _codes(_facts(scan_at=cutoff)) == []


def test_missing_stats_is_the_inner_join_exclusion() -> None:
    reasons = _reasons(_facts(stats_present=False, scan_at=None, needs_compaction=False))

    assert reasons[0].code is MaintenanceBlock.STATS_STALE
    assert reasons[0].values["stats_present"] is False
    assert reasons[0].values["scan_at"] is None
    assert MaintenanceBlock.NO_SMALL_FILE_OR_DELETE_WORK.value not in _codes(
        _facts(stats_present=False, scan_at=None, needs_compaction=False, delete_file_pct=0.0)
    )


def test_naive_scan_at_is_compared_as_utc() -> None:
    scanned = datetime(2026, 9, 1, 0, 0)
    reasons = _reasons(_facts(scan_at=scanned))

    assert reasons[0].code is MaintenanceBlock.STATS_STALE
    assert reasons[0].values["scan_at"].endswith("+00:00")


def test_cooldown_until_uses_the_latest_success_plus_hours() -> None:
    finished = NOW - timedelta(hours=10)
    reasons = _reasons(_facts(last_successful_finished_at=finished))

    until = finished + timedelta(hours=COOLDOWN_HOURS)
    assert reasons[0].code is MaintenanceBlock.COOLDOWN
    assert reasons[0].until == until
    assert reasons[0].values["cooldown_hours"] == COOLDOWN_HOURS
    assert reasons[0].values["finished_at"] == finished.isoformat()
    assert reasons[0].values["job_statuses"] == ["succeeded", "partial"]
    assert f"Cooldown until {until.isoformat()}" in reasons[0].summary


def test_cooldown_boundary_matches_finished_at_greater_or_equal_cutoff() -> None:
    cutoff = NOW - timedelta(hours=COOLDOWN_HOURS)
    assert _codes(_facts(last_successful_finished_at=cutoff)) == [MaintenanceBlock.COOLDOWN.value]
    just_before = cutoff - timedelta(seconds=1)
    assert _codes(_facts(last_successful_finished_at=just_before)) == []


def test_scan_retry_backoff_until_is_scan_retry_due_at() -> None:
    last_error = NOW - timedelta(days=1)
    due = scan_retry_due_at(
        SimpleNamespace(consecutive_scan_failures=3, last_scan_error_at=last_error)
    )
    reasons = _reasons(
        _facts(
            consecutive_scan_failures=3,
            last_scan_error_at=last_error,
            scan_retry_due_at=due,
        )
    )

    assert due is not None and NOW < due
    assert len(reasons) == 1
    assert reasons[0].code is MaintenanceBlock.SCAN_RETRY_BACKOFF
    assert reasons[0].predicate is MaintenancePredicate.SCAN_RETRY_DUE_AT
    assert reasons[0].blocks_nightly_selection is False
    assert reasons[0].until == due
    assert reasons[0].values["consecutive_scan_failures"] == 3
    assert f"until {due.isoformat()}" in reasons[0].summary


def test_scan_retry_in_the_past_or_absent_is_not_a_reason() -> None:
    assert _codes(_facts(scan_retry_due_at=NOW - timedelta(hours=1))) == []
    assert _codes(_facts(scan_retry_due_at=NOW)) == []
    assert _codes(_facts(consecutive_scan_failures=5, scan_retry_due_at=None)) == []


def test_scan_retry_alone_does_not_fail_nightly_selection() -> None:
    reasons = _reasons(_facts(scan_retry_due_at=NOW + timedelta(days=1)))

    assert [reason.code for reason in reasons] == [MaintenanceBlock.SCAN_RETRY_BACKOFF]
    assert not any(reason.blocks_nightly_selection for reason in reasons)


def test_no_small_file_or_delete_work_uses_the_eligibility_bar() -> None:
    reasons = _reasons(_facts(needs_compaction=False, delete_file_pct=0.5))

    assert reasons[0].code is MaintenanceBlock.NO_SMALL_FILE_OR_DELETE_WORK
    assert reasons[0].predicate is MaintenancePredicate.IS_ELIGIBLE
    assert reasons[0].blocks_nightly_selection is True
    assert reasons[0].values["min_delete_file_pct"] == DEFAULT_MIN_DELETE_FILE_PCT
    assert reasons[0].values["delete_file_pct"] == 0.5
    assert reasons[0].values["needs_compaction"] is False


def test_delete_file_pct_at_the_bar_or_needs_compaction_is_work() -> None:
    assert _codes(_facts(needs_compaction=False, delete_file_pct=DEFAULT_MIN_DELETE_FILE_PCT)) == []
    assert _codes(_facts(needs_compaction=True, delete_file_pct=0.0)) == []


def test_last_scan_error_kind_blocks_with_the_kind() -> None:
    reasons = _reasons(_facts(last_scan_error_kind="unreadable"))

    assert reasons[0].code is MaintenanceBlock.LAST_SCAN_ERROR
    assert reasons[0].blocks_nightly_selection is True
    assert reasons[0].values == {"last_scan_error_kind": "unreadable"}
    assert "unreadable" in reasons[0].summary


def test_maintenance_retry_backoff_until_matches_retry_after() -> None:
    retry_after = NOW + timedelta(hours=6)
    reasons = _reasons(_facts(maintenance_retry_after=retry_after))

    assert reasons[0].code is MaintenanceBlock.MAINTENANCE_RETRY_BACKOFF
    assert reasons[0].until == retry_after
    assert reasons[0].values == {"maintenance_retry_after": retry_after.isoformat()}
    assert f"until {retry_after.isoformat()}" in reasons[0].summary


def test_maintenance_retry_at_or_before_now_is_not_a_reason() -> None:
    assert _codes(_facts(maintenance_retry_after=NOW)) == []
    assert _codes(_facts(maintenance_retry_after=NOW - timedelta(seconds=1))) == []


def test_schema_outside_the_allow_list_blocks() -> None:
    reasons = _reasons(_facts(schema_name="sales", maintenance_schema_names=["finance", "ops"]))

    assert reasons[0].code is MaintenanceBlock.SCHEMA_NOT_ALLOWED
    assert reasons[0].values == {
        "schema_name": "sales",
        "maintenance_schema_names": ["finance", "ops"],
    }
    assert "sales" in reasons[0].summary
    assert "finance" in reasons[0].summary


def test_empty_allow_list_does_not_block() -> None:
    assert schema_in_maintenance_allow_list("sales", []) is True
    assert schema_in_maintenance_allow_list("sales", None) is True
    assert _codes(_facts(maintenance_schema_names=[])) == []
    assert _codes(_facts(maintenance_schema_names=["sales"])) == []


def test_clear_table_has_no_blocking_reasons() -> None:
    assert _reasons(_facts()) == []


def test_over_budget_is_not_a_per_table_block() -> None:
    assert "over_budget" not in {item.value for item in MaintenanceBlock}
    assert _reasons(_facts()) == []


def test_applicable_reasons_stack_in_checklist_order() -> None:
    facts = _facts(
        is_active=False,
        source_enabled=False,
        stats_present=True,
        scan_at=NOW - timedelta(days=9),
        last_successful_finished_at=NOW - timedelta(hours=1),
        scan_retry_due_at=NOW + timedelta(days=2),
        needs_compaction=False,
        delete_file_pct=0.0,
        last_scan_error_kind="failed",
        maintenance_retry_after=NOW + timedelta(hours=4),
        schema_name="sales",
        maintenance_schema_names=["finance"],
    )

    assert _codes(facts) == [
        MaintenanceBlock.INACTIVE.value,
        MaintenanceBlock.SOURCE_DISABLED.value,
        MaintenanceBlock.STATS_STALE.value,
        MaintenanceBlock.COOLDOWN.value,
        MaintenanceBlock.SCAN_RETRY_BACKOFF.value,
        MaintenanceBlock.NO_SMALL_FILE_OR_DELETE_WORK.value,
        MaintenanceBlock.LAST_SCAN_ERROR.value,
        MaintenanceBlock.MAINTENANCE_RETRY_BACKOFF.value,
        MaintenanceBlock.SCHEMA_NOT_ALLOWED.value,
    ]


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    def __init__(
        self,
        table: Any,
        source_row: Any,
        stats_row: Any,
        finished_at: datetime | None,
    ) -> None:
        self.table = table
        self.source_row = source_row
        self.stats_row = stats_row
        self.finished_at = finished_at
        self.scalar_statements: list[Any] = []

    def get(self, model: Any, ident: Any) -> Any:
        if model is Table and self.table is not None and self.table.id == ident:
            return self.table
        return None

    def execute(self, stmt: Any) -> _Result:
        keys = [column.key for column in stmt.selected_columns]
        if "enabled" in keys:
            return _Result(self.source_row)
        if "scan_at" in keys:
            return _Result(self.stats_row)
        raise AssertionError(keys)

    def scalar(self, stmt: Any) -> datetime | None:
        self.scalar_statements.append(stmt)
        return self.finished_at


def _table(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "schema_name": "sales",
        "table_name": "orders",
        "is_active": True,
        "last_scan_error_kind": None,
        "maintenance_retry_after": None,
        "consecutive_scan_failures": 0,
        "last_scan_error_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(session: _FakeSession, *, principal: Principal | None) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def _session() -> Any:
        yield session

    app.dependency_overrides[get_tenant_session] = _session
    if principal is not None:
        app.dependency_overrides[get_current_principal] = lambda: principal
    return TestClient(app)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(maintenance_cooldown_hours=72, maintenance_max_stats_age_days=3)


def _patch_settings(monkeypatch: Any) -> None:
    monkeypatch.setattr("cryo.api.routes_table_maintenance.get_settings", _settings)


def _viewer() -> Principal:
    return Principal(subject="viewer", roles=frozenset({Role.VIEWER}))


def test_missing_token_is_unauthorized() -> None:
    client = _client(_FakeSession(None, None, None, None), principal=None)

    response = client.get(f"/api/v1/tables/{uuid.uuid4()}/maintenance-status")

    assert response.status_code == 401


def test_evidence_collector_is_forbidden() -> None:
    client = _client(_FakeSession(None, None, None, None), principal=None)

    response = client.get(
        f"/api/v1/tables/{uuid.uuid4()}/maintenance-status",
        headers=auth_header("evidence_collector"),
    )

    assert response.status_code == 403


def test_missing_table_is_not_found() -> None:
    client = _client(_FakeSession(None, None, None, None), principal=_viewer())

    response = client.get(f"/api/v1/tables/{uuid.uuid4()}/maintenance-status")

    assert response.status_code == 404
    assert response.json()["detail"] == "Table not found"


def test_route_returns_inactive_and_the_omitted_contract(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch)
    table = _table(is_active=False)
    session = _FakeSession(
        table,
        SimpleNamespace(enabled=True, maintenance_schema_names=[]),
        SimpleNamespace(scan_at=NOW, needs_compaction=True, delete_file_pct=0.0),
        None,
    )
    client = _client(session, principal=_viewer())

    response = client.get(f"/api/v1/tables/{table.id}/maintenance-status")

    assert response.status_code == 200
    body = response.json()
    assert body["table_id"] == str(table.id)
    assert body["eligible"] is False
    assert body["blocking_reasons"][0]["code"] == "inactive"
    assert body["blocking_reasons"][0]["values"] == {"is_active": False}
    assert body["blocking_reasons"][0]["blocks_nightly_selection"] is True
    assert body["last_actions"] == []
    assert "unbounded scan" in body["last_actions_note"]
    assert body["last_actions_note"] == LAST_ACTIONS_NOTE
    assert body["next_nightly_at"] is None
    assert "not derivable without new config" in body["next_nightly_note"]
    assert body["next_nightly_note"] == NEXT_NIGHTLY_NOTE
    assert body["omitted"] == [
        {
            "code": "over_budget_last_run",
            "where": OVER_BUDGET_WHERE,
            "why": body["omitted"][0]["why"],
        }
    ]
    assert "fit_budget" in body["omitted"][0]["why"]
    assert "load_candidates" in body["omitted"][0]["why"]
    assert body["thresholds"] == {
        "cooldown_hours": 72.0,
        "max_stats_age_days": 3.0,
        "min_delete_file_pct": DEFAULT_MIN_DELETE_FILE_PCT,
    }
    statement = str(session.scalar_statements[0])
    assert "LIMIT" in statement.upper()
    assert "finished_at" in statement
    assert "plan" not in statement


def test_route_uses_settings_for_the_stats_age_window(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "cryo.api.routes_table_maintenance.get_settings",
        lambda: SimpleNamespace(maintenance_cooldown_hours=10, maintenance_max_stats_age_days=2),
    )
    table = _table()
    session = _FakeSession(
        table,
        SimpleNamespace(enabled=True, maintenance_schema_names=[]),
        SimpleNamespace(
            scan_at=datetime.now(UTC) - timedelta(days=3),
            needs_compaction=True,
            delete_file_pct=2.0,
        ),
        None,
    )
    client = _client(session, principal=_viewer())

    body = client.get(f"/api/v1/tables/{table.id}/maintenance-status").json()

    stale = next(reason for reason in body["blocking_reasons"] if reason["code"] == "stats_stale")
    assert stale["values"]["max_stats_age_days"] == 2.0
    assert "older than 2" in stale["summary"] or "older than 2.0" in stale["summary"]
    assert body["thresholds"]["cooldown_hours"] == 10.0


def test_route_scan_retry_until_matches_scan_retry_due_at(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch)
    last_error = datetime.now(UTC) - timedelta(days=1)
    table = _table(consecutive_scan_failures=3, last_scan_error_at=last_error)
    session = _FakeSession(
        table,
        SimpleNamespace(enabled=True, maintenance_schema_names=[]),
        SimpleNamespace(
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            delete_file_pct=0.0,
        ),
        None,
    )
    client = _client(session, principal=_viewer())

    body = client.get(f"/api/v1/tables/{table.id}/maintenance-status").json()

    due = scan_retry_due_at(table)
    backoff = next(
        reason for reason in body["blocking_reasons"] if reason["code"] == "scan_retry_backoff"
    )
    assert due is not None
    parsed = datetime.fromisoformat(backoff["until"].replace("Z", "+00:00"))
    assert parsed == due
    assert backoff["blocks_nightly_selection"] is False
    assert backoff["predicate"] == "scan_retry_due_at"


def test_route_eligible_table_still_reports_the_empty_history_fields(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch)
    table = _table()
    session = _FakeSession(
        table,
        SimpleNamespace(enabled=True, maintenance_schema_names=[]),
        SimpleNamespace(
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            delete_file_pct=0.0,
        ),
        None,
    )
    client = _client(session, principal=_viewer())

    body = client.get(f"/api/v1/tables/{table.id}/maintenance-status").json()

    assert body["eligible"] is True
    assert body["blocking_reasons"] == []
    assert body["last_actions"] == []
    assert body["next_nightly_at"] is None
