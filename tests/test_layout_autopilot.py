"""Tests for the layout-recommendation autopilot (cryo.scheduler.service._run_layout_autopilot).

Context, 2026-08-11: execute_layout_recommendation() (approve -> Spark sort/z-order rewrite ->
persist write.sort-order) was fully built and working, but its only caller was a human clicking
"execute" in the Smart Maintenance UI one table at a time. This is the wiring that lets a preset
opt in to having its tables' pending recommendations auto-approved and auto-executed on a
schedule -- deliberately gated per-preset (params.auto_apply_layout_recommendations), not a
single fleet-wide switch, since a sort/z-order rewrite is a real data rewrite (tier2), not the
metadata-only property flips property_autopilot handles.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import Destination, LayoutRecommendation, Preset, TableAssignment
from tests.test_scheduler_telemetry import (
    _bound_session_factory,
    _make_service,
    _seed_source,
    _seed_table,
)


def _seed_preset(session, *, destination: Destination, auto_apply: bool) -> Preset:
    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4()}",
        destination_id=destination.id,
        operations=["optimize"],
        params={"auto_apply_layout_recommendations": auto_apply} if auto_apply else {},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    return preset


def _seed_destination(session) -> Destination:
    destination = Destination(
        id=uuid4(),
        name=f"dest-{uuid4()}",
        engine="spark_emr",
        config={},
    )
    session.add(destination)
    session.flush()
    return destination


def _fake_settings(max_tables: int = 20):
    return type("FakeSettings", (), {"layout_autopilot_max_tables_per_tick": max_tables})()


def test_layout_autopilot_skips_tables_whose_preset_has_not_opted_in(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    table = _seed_table(session, source, table_name="not_opted_in")
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination=destination, auto_apply=False)
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id))
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
        confidence=0.9,
        status="pending",
    )
    session.add(recommendation)
    session.commit()

    mock_execute = MagicMock()
    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.execute_layout_recommendation", mock_execute
    )

    service = _make_service()
    service._run_layout_autopilot(_fake_settings())

    mock_execute.assert_not_called()
    session.refresh(recommendation)
    assert recommendation.status == "pending"


def test_layout_autopilot_approves_and_executes_for_opted_in_preset(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    table = _seed_table(session, source, table_name="opted_in")
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination=destination, auto_apply=True)
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id))
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
        confidence=0.9,
        status="pending",
    )
    session.add(recommendation)
    session.commit()

    captured: dict = {}

    def _fake_execute(recommendation_id, payload, exec_session, principal):
        captured["recommendation_id"] = recommendation_id
        captured["principal"] = principal
        rec = exec_session.get(LayoutRecommendation, recommendation_id)
        rec.status = "applied"
        exec_session.commit()

    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.execute_layout_recommendation", _fake_execute
    )

    service = _make_service()
    service._run_layout_autopilot(_fake_settings())

    assert captured["recommendation_id"] == recommendation.id
    assert captured["principal"].subject == "cryo-layout-autopilot"
    session.refresh(recommendation)
    assert recommendation.status == "applied"
    assert recommendation.approved_by == "cryo-layout-autopilot"


def test_layout_autopilot_respects_max_tables_per_tick(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    source = _seed_source(session)
    destination = _seed_destination(session)
    preset = _seed_preset(session, destination=destination, auto_apply=True)
    recommendations = []
    for i in range(3):
        table = _seed_table(session, source, table_name=f"table_{i}")
        session.add(TableAssignment(table_id=table.id, preset_id=preset.id))
        rec = LayoutRecommendation(
            id=uuid4(),
            table_id=table.id,
            recommended_strategy="sort",
            recommended_columns=["event_date"],
            confidence=0.9,
            status="pending",
        )
        session.add(rec)
        recommendations.append(rec)
    session.commit()

    executed_ids: list = []

    def _fake_execute(recommendation_id, payload, exec_session, principal):
        executed_ids.append(recommendation_id)
        rec = exec_session.get(LayoutRecommendation, recommendation_id)
        rec.status = "applied"
        exec_session.commit()

    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.execute_layout_recommendation", _fake_execute
    )

    service = _make_service()
    service._run_layout_autopilot(_fake_settings(max_tables=1))

    assert len(executed_ids) == 1
