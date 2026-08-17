"""Tests for cryo.reporting.cost_ceiling -- the real-dollar circuit breaker on the cryo
namespace's daily spend. User-requested, 2026-08-12: a hard $100/day ceiling after real
data showed the namespace averaging $210.82/day over the trailing 14 days, over budget on
8 of 14. Halts fleet-wide admission via the existing MaintenanceControlState global-pause
mechanism -- the same one an operator's manual pause button writes to -- rather than a
parallel kill-switch.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from cryo.db.models import AuditLog, MaintenanceControlState
from cryo.db.repositories.intents import set_pause_state
from cryo.reporting.cost_ceiling import (
    enforce_cost_ceiling,
    fetch_namespace_cost_range,
    fetch_todays_namespace_cost,
)


def _grafana_range_response(rows: list[tuple[str, float, float]]) -> dict:
    """rows: [(day, used, unused), ...] -- matches _COST_RANGE_SQL's column order/names."""
    days = [r[0] for r in rows]
    used = [r[1] for r in rows]
    unused = [r[2] for r in rows]
    return {
        "results": {
            "A": {
                "frames": [
                    {
                        "schema": {
                            "fields": [
                                {"name": "day"},
                                {"name": "used_cost"},
                                {"name": "unused_cost"},
                            ]
                        },
                        "data": {"values": [days, used, unused]},
                    }
                ]
            }
        }
    }


def _grafana_response(*, used: float, unused: float) -> dict:
    return {
        "results": {
            "A": {
                "frames": [
                    {
                        "schema": {"fields": [{"name": "used_cost"}, {"name": "unused_cost"}]},
                        "data": {"values": [[used], [unused]]},
                    }
                ]
            }
        }
    }


def _mock_httpx_post(payload: dict, *, status_code: int = 200):
    def _post(url, headers=None, json=None, timeout=None):
        return httpx.Response(status_code, json=payload, request=httpx.Request("POST", url))

    return _post


def test_fetch_todays_cost_parses_used_and_unused() -> None:
    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=42.5, unused=180.3))
    ):
        check = fetch_todays_namespace_cost(
            grafana_url="https://grafana.example.com",
            grafana_token="tok",
            cluster_name="data-platform-eks",
            namespace="cryo",
            ceiling_usd=100.0,
        )

    assert check.used_cost_usd == 42.5
    assert check.unused_cost_usd == 180.3
    assert check.total_cost_usd == 222.8
    assert check.over_ceiling is True


def test_fetch_todays_cost_under_ceiling_is_not_over() -> None:
    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=10.0, unused=20.0))
    ):
        check = fetch_todays_namespace_cost(
            grafana_url="https://grafana.example.com",
            grafana_token="tok",
            cluster_name="data-platform-eks",
            namespace="cryo",
            ceiling_usd=100.0,
        )

    assert check.total_cost_usd == 30.0
    assert check.over_ceiling is False


def test_fetch_todays_cost_handles_empty_frame_as_zero() -> None:
    """CUR data lags 24-48h -- a day with no billing rows yet must read as $0, not crash."""
    empty = {"results": {"A": {"frames": []}}}
    with patch("httpx.post", side_effect=_mock_httpx_post(empty)):
        check = fetch_todays_namespace_cost(
            grafana_url="https://grafana.example.com",
            grafana_token="tok",
            cluster_name="data-platform-eks",
            namespace="cryo",
            ceiling_usd=100.0,
        )

    assert check.total_cost_usd == 0.0
    assert check.over_ceiling is False


def test_fetch_todays_cost_raises_on_grafana_query_error() -> None:
    errored = {"results": {"A": {"error": "plugin.requestFailureError"}}}
    with patch("httpx.post", side_effect=_mock_httpx_post(errored)):
        with pytest.raises(RuntimeError, match="Grafana query error"):
            fetch_todays_namespace_cost(
                grafana_url="https://grafana.example.com",
                grafana_token="tok",
                cluster_name="data-platform-eks",
                namespace="cryo",
                ceiling_usd=100.0,
            )


def test_fetch_namespace_cost_range_parses_multiple_days() -> None:
    payload = _grafana_range_response(
        [
            ("2026-08-14", 100.0, 200.0),
            ("2026-08-15", 110.0, 190.0),
            ("2026-08-16", 90.0, 210.0),
        ]
    )
    with patch("httpx.post", side_effect=_mock_httpx_post(payload)):
        days = fetch_namespace_cost_range(
            grafana_url="https://grafana.example.com",
            grafana_token="tok",
            cluster_name="data-platform-eks",
            namespace="trino-db",
            since=date(2026, 8, 14),
            until=date(2026, 8, 16),
        )

    assert len(days) == 3
    assert days[0].day == date(2026, 8, 14)
    assert days[0].used_cost_usd == 100.0
    assert days[0].unused_cost_usd == 200.0
    assert days[0].total_cost_usd == 300.0
    assert days[2].day == date(2026, 8, 16)
    assert days[2].total_cost_usd == 300.0


def test_fetch_namespace_cost_range_empty_frame_returns_empty_list() -> None:
    """CUR's 24-48h lag means recent days can legitimately have no rows yet -- must return an
    empty list, not crash or fabricate zero-cost days for a range with no data at all."""
    empty = {"results": {"A": {"frames": []}}}
    with patch("httpx.post", side_effect=_mock_httpx_post(empty)):
        days = fetch_namespace_cost_range(
            grafana_url="https://grafana.example.com",
            grafana_token="tok",
            cluster_name="data-platform-eks",
            namespace="trino-db",
            since=date(2026, 8, 14),
        )

    assert days == []


def test_fetch_namespace_cost_range_raises_on_grafana_query_error() -> None:
    errored = {"results": {"A": {"error": "plugin.requestFailureError"}}}
    with patch("httpx.post", side_effect=_mock_httpx_post(errored)):
        with pytest.raises(RuntimeError, match="Grafana query error"):
            fetch_namespace_cost_range(
                grafana_url="https://grafana.example.com",
                grafana_token="tok",
                cluster_name="data-platform-eks",
                namespace="trino-db",
                since=date(2026, 8, 14),
            )


class _FakeSettings:
    cost_ceiling_enabled = True
    cost_ceiling_daily_usd = 100.0
    cost_ceiling_cluster_name = "data-platform-eks"
    cost_ceiling_namespace = "cryo"


class _DisabledSettings(_FakeSettings):
    cost_ceiling_enabled = False


def test_enforce_ceiling_noop_when_disabled(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _DisabledSettings())

    result = enforce_cost_ceiling(session, grafana_url="https://g.example.com", grafana_token="tok")

    assert result is None
    assert session.scalar(select(MaintenanceControlState)) is None


def test_enforce_ceiling_pauses_fleet_when_over_budget(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())
    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=80.0, unused=150.0))
    ):
        result = enforce_cost_ceiling(
            session, grafana_url="https://g.example.com", grafana_token="tok"
        )

    assert result is not None
    assert result.over_ceiling is True

    state = session.scalar(select(MaintenanceControlState))
    assert state is not None
    assert state.paused is True
    assert state.scope_type == "global"
    assert state.scope_id == "global"
    assert state.reason is not None
    assert state.reason.startswith("cost_ceiling_breach:")
    assert state.changed_by == "cost_ceiling_checker"

    audit_actions = list(session.scalars(select(AuditLog.action)))
    assert "maintenance_pause" in audit_actions


def test_enforce_ceiling_does_not_double_pause_or_bump_generation(session, monkeypatch) -> None:
    """Once paused for a cost breach, a second over-budget check must not re-pause (no
    audit-log/generation churn) -- only the transition matters, not every tick."""
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())
    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=80.0, unused=150.0))
    ):
        enforce_cost_ceiling(session, grafana_url="https://g.example.com", grafana_token="tok")
    state_after_first = session.scalar(select(MaintenanceControlState))
    generation_after_first = state_after_first.generation

    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=90.0, unused=160.0))
    ):
        enforce_cost_ceiling(session, grafana_url="https://g.example.com", grafana_token="tok")
    state_after_second = session.scalar(select(MaintenanceControlState))

    assert state_after_second.generation == generation_after_first


def test_enforce_ceiling_resumes_when_back_under_budget(session, monkeypatch) -> None:
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())
    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=80.0, unused=150.0))
    ):
        enforce_cost_ceiling(session, grafana_url="https://g.example.com", grafana_token="tok")
    assert session.scalar(select(MaintenanceControlState)).paused is True

    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=10.0, unused=20.0))
    ):
        result = enforce_cost_ceiling(
            session, grafana_url="https://g.example.com", grafana_token="tok"
        )

    assert result.over_ceiling is False
    state = session.scalar(select(MaintenanceControlState))
    assert state.paused is False
    assert state.reason is not None
    assert state.reason.startswith("cost_ceiling_cleared")


def test_enforce_ceiling_never_resumes_an_operators_manual_pause(session, monkeypatch) -> None:
    """A pause an operator set for an unrelated reason (e.g. incident containment) must
    never be silently lifted just because today's cost happens to be under budget."""
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())
    set_pause_state(
        session,
        scope_type="global",
        scope_id="global",
        paused=True,
        changed_by="alice@example.com",
        reason="incident containment -- do not auto-resume",
    )
    session.commit()

    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=10.0, unused=20.0))
    ):
        result = enforce_cost_ceiling(
            session, grafana_url="https://g.example.com", grafana_token="tok"
        )

    assert result.over_ceiling is False
    state = session.scalar(select(MaintenanceControlState))
    # Still paused -- untouched by the cost checker, which isn't the pause's owner.
    assert state.paused is True
    assert state.changed_by == "alice@example.com"
    assert state.reason == "incident containment -- do not auto-resume"


def test_enforce_ceiling_never_overrides_an_operators_pause_with_its_own_reason(
    session, monkeypatch
) -> None:
    """The reverse direction: if an operator already has global maintenance paused for
    their own reason, a cost breach on top of that must not stomp their reason/ownership
    -- the pause is already in effect either way, so there's nothing to change."""
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())
    set_pause_state(
        session,
        scope_type="global",
        scope_id="global",
        paused=True,
        changed_by="alice@example.com",
        reason="incident containment",
    )
    session.commit()
    generation_before = session.scalar(select(MaintenanceControlState)).generation

    with patch(
        "httpx.post", side_effect=_mock_httpx_post(_grafana_response(used=80.0, unused=150.0))
    ):
        enforce_cost_ceiling(session, grafana_url="https://g.example.com", grafana_token="tok")

    state = session.scalar(select(MaintenanceControlState))
    assert state.paused is True
    assert state.changed_by == "alice@example.com"
    assert state.reason == "incident containment"
    assert state.generation == generation_before


def test_enforce_ceiling_survives_grafana_fetch_failure(session, monkeypatch) -> None:
    """A Grafana outage / auth failure must not raise out of the scheduler tick -- fail
    safe-open (no pause change), matching every other guardrail's degraded-dependency
    contract in this codebase."""
    monkeypatch.setattr("cryo.reporting.cost_ceiling.get_settings", lambda: _FakeSettings())

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    with patch("httpx.post", side_effect=_raise):
        result = enforce_cost_ceiling(
            session, grafana_url="https://g.example.com", grafana_token="tok"
        )

    assert result is None
    assert session.scalar(select(MaintenanceControlState)) is None
