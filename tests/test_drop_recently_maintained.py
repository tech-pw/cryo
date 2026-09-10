"""Direct unit tests for scheduler.drop_recently_maintained -- 2026-09-10.

Before cryo.db.repositories.presets.table_to_candidate/scheduler_store.SQLSchedulerStore's
last_maintained_at fix, every TableCandidate reaching this function had
last_maintained_at=None (never populated upstream), so `isinstance(last, datetime)` was
always False and this cooldown filter silently kept every candidate regardless of how
recently it was actually maintained or what cooldown_hours a preset configured. These
tests exercise the function itself against both the old (None) and new (populated) shapes
to make that regression concrete and pin the fixed behavior going forward.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.scheduler.rules import PresetConfig, TableCandidate
from cryo.scheduler.scheduler import drop_recently_maintained


def _candidate(table_id: str, *, last_maintained_at: datetime | None) -> TableCandidate:
    return TableCandidate(
        id=table_id,
        schema_name="analytics",
        table_name=f"table_{table_id}",
        last_maintained_at=last_maintained_at,
    )


def _preset(cooldown_hours: int) -> PresetConfig:
    return PresetConfig(
        id="preset-1",
        name="test-preset",
        destination_id="dest-1",
        guardrails={"cooldown_hours": cooldown_hours},
    )


def test_never_maintained_table_always_kept() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    candidates = [_candidate("t1", last_maintained_at=None)]

    kept = drop_recently_maintained(candidates, _preset(24), now=now)

    assert [c.id for c in kept] == ["t1"]


def test_recently_maintained_table_dropped_within_cooldown() -> None:
    """The actual regression: a table maintained 1h ago with a 24h cooldown must be
    dropped -- before the fix, last_maintained_at was always None here and this table
    would incorrectly survive the filter."""
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    candidates = [_candidate("t1", last_maintained_at=now - timedelta(hours=1))]

    kept = drop_recently_maintained(candidates, _preset(24), now=now)

    assert kept == []


def test_table_maintained_past_cooldown_is_kept() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    candidates = [_candidate("t1", last_maintained_at=now - timedelta(hours=25))]

    kept = drop_recently_maintained(candidates, _preset(24), now=now)

    assert [c.id for c in kept] == ["t1"]


def test_zero_cooldown_keeps_everything_regardless_of_last_maintained_at() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    candidates = [_candidate("t1", last_maintained_at=now - timedelta(minutes=1))]

    kept = drop_recently_maintained(candidates, _preset(0), now=now)

    assert [c.id for c in kept] == ["t1"]


def test_mixed_fleet_only_drops_recently_maintained_tables() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    candidates = [
        _candidate("fresh", last_maintained_at=None),
        _candidate("recent", last_maintained_at=now - timedelta(hours=2)),
        _candidate("stale", last_maintained_at=now - timedelta(days=3)),
    ]

    kept = drop_recently_maintained(candidates, _preset(24), now=now)

    assert {c.id for c in kept} == {"fresh", "stale"}
