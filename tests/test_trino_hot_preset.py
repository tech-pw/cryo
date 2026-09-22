"""Tests for cryo.scheduler.trino_hot_preset.repin_trino_hot_tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.db.models import Destination, Preset, QueryLog, Source, Table, TableAssignment
from cryo.scheduler.trino_hot_preset import EmptyHotRankingError, repin_trino_hot_tables

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
_ASSIGNED_BY = "trino_hot_preset_repin"


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str) -> Table:
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    return table


def _seed_destination(session, *, name: str = "spark-test") -> Destination:
    destination = Destination(id=uuid4(), name=name, engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    return destination


def _seed_preset(session, destination: Destination, *, name: str = "daily-trino-hot-tables") -> Preset:
    preset = Preset(
        id=uuid4(),
        name=name,
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params={},
        guardrails={},
        enabled=True,
    )
    session.add(preset)
    session.flush()
    return preset


def _log_query(session, *, source: Source, table: Table, when: datetime) -> None:
    session.add(
        QueryLog(
            source_id=source.id, table_id=table.id, query_id=f"q-{uuid4()}",
            engine="trino", executed_at=when,
        )
    )


def test_repin_raises_when_preset_not_found(session):
    with pytest.raises(ValueError):
        repin_trino_hot_tables(session, preset_name="nonexistent-preset", now=NOW)


def test_repin_pins_hot_tables_to_the_preset(session):
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    hot = _seed_table(session, source, name="hot")
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    result = repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)

    assert result.pinned == 1
    assert result.unpinned == 0
    assignment = session.get(TableAssignment, hot.id)
    assert assignment is not None
    assert assignment.preset_id == preset.id
    assert assignment.pinned is True
    assert assignment.assigned_by == _ASSIGNED_BY


def test_repin_unpins_tables_that_fell_out_of_top_n(session):
    """A table this job pinned yesterday that's no longer in today's top-N must be released --
    otherwise the preset would accumulate every table it ever pinned, forever."""
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    was_hot = _seed_table(session, source, name="was_hot")
    session.add(
        TableAssignment(
            table_id=was_hot.id, preset_id=preset.id, assigned_by=_ASSIGNED_BY, pinned=True
        )
    )
    # Someone else is hot today. A ranking with at least one table is what distinguishes
    # "was_hot cooled off" from "telemetry is down", which is refused outright -- see
    # test_repin_refuses_to_unpin_everything_on_an_empty_ranking.
    still_hot = _seed_table(session, source, name="still_hot")
    _log_query(session, source=source, table=still_hot, when=NOW - timedelta(days=1))
    session.commit()

    # No QueryLog rows at all for was_hot in this window -- it's no longer in the top-N.
    result = repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)

    assert result.unpinned == 1
    assert session.get(TableAssignment, was_hot.id) is None


def test_repin_leaves_manually_pinned_tables_alone(session):
    """A table an operator explicitly pinned to this preset (assigned_by != this job's marker)
    must never be silently unpinned just because it's not in today's Trino top-N."""
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    manual = _seed_table(session, source, name="manual")
    session.add(
        TableAssignment(
            table_id=manual.id, preset_id=preset.id, assigned_by="operator:aamir", pinned=True
        )
    )
    hot = _seed_table(session, source, name="hot")
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    result = repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)

    assert result.unpinned == 0
    assignment = session.get(TableAssignment, manual.id)
    assert assignment is not None
    assert assignment.assigned_by == "operator:aamir"


def test_repin_never_touches_assignments_belonging_to_other_presets(session):
    source = _seed_source(session)
    destination = _seed_destination(session)
    trino_hot_preset = _seed_preset(session, destination, name="daily-trino-hot-tables")
    other_preset = _seed_preset(session, destination, name="other-preset")
    table = _seed_table(session, source, name="t")
    session.add(
        TableAssignment(
            table_id=table.id, preset_id=other_preset.id, assigned_by="operator:x", pinned=True
        )
    )
    hot = _seed_table(session, source, name="hot")
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    # table isn't queried on Trino at all, so it's not a repin candidate either way.
    result = repin_trino_hot_tables(session, preset_name=trino_hot_preset.name, limit=10, now=NOW)

    assert result.unpinned == 0
    assignment = session.get(TableAssignment, table.id)
    assert assignment is not None
    assert assignment.preset_id == other_preset.id


def test_repin_takes_over_a_table_this_job_pinned_to_a_different_preset(session):
    """TableAssignment.table_id is the primary key, so a hot table already assigned elsewhere
    cannot simply be inserted -- reassign the existing row instead of raising on the PK."""
    source = _seed_source(session)
    destination = _seed_destination(session)
    trino_hot_preset = _seed_preset(session, destination, name="daily-trino-hot-tables")
    other_preset = _seed_preset(session, destination, name="cold-sweep")
    hot = _seed_table(session, source, name="hot")
    session.add(
        TableAssignment(
            table_id=hot.id, preset_id=other_preset.id, assigned_by=_ASSIGNED_BY, pinned=True
        )
    )
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    result = repin_trino_hot_tables(session, preset_name=trino_hot_preset.name, limit=10, now=NOW)

    assert result.pinned == 1
    assignment = session.get(TableAssignment, hot.id)
    assert assignment is not None
    assert assignment.preset_id == trino_hot_preset.id


def test_repin_leaves_an_operator_pin_on_another_preset_in_place(session):
    """Taking a table over is for pins this job made. An operator's explicit choice wins."""
    source = _seed_source(session)
    destination = _seed_destination(session)
    trino_hot_preset = _seed_preset(session, destination, name="daily-trino-hot-tables")
    other_preset = _seed_preset(session, destination, name="operator-owned")
    hot = _seed_table(session, source, name="hot")
    session.add(
        TableAssignment(
            table_id=hot.id, preset_id=other_preset.id, assigned_by="operator:aamir", pinned=True
        )
    )
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    result = repin_trino_hot_tables(session, preset_name=trino_hot_preset.name, limit=10, now=NOW)

    assert result.pinned == 0
    assignment = session.get(TableAssignment, hot.id)
    assert assignment is not None
    assert assignment.preset_id == other_preset.id


def test_repin_refuses_to_unpin_everything_on_an_empty_ranking(session):
    """An empty ranking means telemetry is missing, not that nothing is queried. Reconciling
    to it would strip every pin and leave the hottest tables unmaintained."""
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    pinned = _seed_table(session, source, name="pinned_yesterday")
    session.add(
        TableAssignment(
            table_id=pinned.id, preset_id=preset.id, assigned_by=_ASSIGNED_BY, pinned=True
        )
    )
    session.commit()

    with pytest.raises(EmptyHotRankingError):
        repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)

    assert session.get(TableAssignment, pinned.id) is not None


def test_repin_is_idempotent_when_top_n_is_unchanged(session):
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    hot = _seed_table(session, source, name="hot")
    _log_query(session, source=source, table=hot, when=NOW - timedelta(days=1))
    session.commit()

    first = repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)
    assert first.pinned == 1

    second = repin_trino_hot_tables(session, preset_name=preset.name, limit=10, now=NOW)
    assert second.pinned == 0
    assert second.unchanged == 1
    assert second.unpinned == 0


def test_repin_respects_limit(session):
    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination)
    tables = [_seed_table(session, source, name=f"t{i}") for i in range(5)]
    for table in tables:
        _log_query(session, source=source, table=table, when=NOW - timedelta(days=1))
    session.commit()

    result = repin_trino_hot_tables(session, preset_name=preset.name, limit=2, now=NOW)

    assert result.pinned == 2
