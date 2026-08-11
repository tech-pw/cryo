"""Regression, 2026-07-24: execute_layout_recommendation used to dispatch the table's generic
assigned preset (typically plain binpack compaction) and unconditionally mark the recommendation
"applied" regardless of what actually ran -- it never read recommended_strategy/recommended_columns,
so approving and "executing" a sort/z-order recommendation silently did neither. These tests cover
the real dispatch: sort/zorder rewrite via Spark, best-effort write.sort-order persistence for
sort, and only marking "applied" when the rewrite genuinely succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from cryo.api.routes_smart_maintenance import (
    ExecuteLayoutRecommendationRequest,
    execute_layout_recommendation,
)
from cryo.api.security import Principal, Role
from cryo.config import get_settings
from cryo.db.models import (
    Destination,
    EffectReceipt,
    LayoutRecommendation,
    MaintenanceIntent,
    Preset,
    Source,
    Table,
    TableAssignment,
    TableStats,
)
from cryo.domain.enums import EngineType
from cryo.engines.base import ExecResult, RenderedStatement
from cryo.engines.spark.k8s import JobRunResult, JobRunState

PRINCIPAL = Principal(subject="test-operator", roles=frozenset({Role.OPERATOR}))


def _seed(
    session,
    *,
    recommended_strategy: str = "sort",
    recommended_columns: list[str] | None = None,
    status: str = "approved",
    total_files: int | None = None,
    column_quality: list[str] | None = None,
):
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={
            "catalog_name": "hive",
            "uri": "thrift://trino-metastore-v2.penpencil.co:9083",
            "warehouse": "s3://pw-data-cdp-dev-bronze-layer/",
        },
    )
    main_destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    fallback_destination = Destination(
        id=uuid4(),
        name=f"fallback-{uuid4()}",
        engine="spark_emr",
        config={"catalog": "cdp", "spark_k8s": {"namespace": "cryo"}},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="lms",
        table_name="silver_adminactions",
        is_active=True,
    )
    session.add_all([source, main_destination, fallback_destination, table])
    session.flush()

    main_destination.config = {"sql_fallback_destination_id": str(fallback_destination.id)}
    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4()}",
        destination_id=main_destination.id,
        operations=["expire_snapshots"],
        params={},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id))

    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy=recommended_strategy,
        recommended_columns=recommended_columns
        if recommended_columns is not None
        else ["event_date"],
        confidence=0.8,
        status=status,
    )
    session.add(recommendation)
    if total_files is not None or column_quality is not None:
        raw = {}
        if column_quality is not None:
            raw["column_quality"] = [{"column_name": c} for c in column_quality]
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=datetime.now(UTC),
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="C",
                overall_score=60,
                total_files=total_files or 0,
                small_files_count=total_files or 0,
                total_partitions=1,
                total_size_gb=1.0,
                raw=raw,
            )
        )
    session.commit()
    return recommendation.id


def _seed_with_spark_primary_destination(
    session,
    *,
    recommended_strategy: str = "sort",
    recommended_columns: list[str] | None = None,
    status: str = "approved",
):
    """Table whose effective preset routes straight to a Spark destination (e.g. spark-medium
    -> spark-k8s-fallback), with no sql_fallback_destination_id of its own -- Spark isn't a
    fallback here, it IS the primary engine. Regression, 2026-08-11: the fallback-id-only check
    in execute_layout_recommendation rejected every such table with a 422 even though nothing
    else was missing (found live on lsq_pw.silver_prospect_activity_base, effective preset
    spark-medium -> destination spark-k8s-fallback, engine=spark_emr, config has no
    sql_fallback_destination_id since it's not acting as anyone's fallback).
    """
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={
            "catalog_name": "lsq_pw",
            "uri": "thrift://trino-prod-metastore.penpencil.co:9083",
            "warehouse": "s3://pw-data-cdp-prod-gold-layer/",
        },
    )
    spark_destination = Destination(
        id=uuid4(),
        name="spark-k8s-fallback",
        engine="spark_emr",
        config={"catalog": "cdp", "spark_k8s": {"namespace": "cryo"}},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="lsq_pw",
        table_name="silver_prospect_activity_base",
        is_active=True,
    )
    session.add_all([source, spark_destination, table])
    session.flush()

    preset = Preset(
        id=uuid4(),
        name=f"spark-medium-{uuid4()}",
        destination_id=spark_destination.id,
        operations=["optimize"],
        params={},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id))

    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy=recommended_strategy,
        recommended_columns=recommended_columns
        if recommended_columns is not None
        else ["ProspectActivityAutoId"],
        confidence=0.8,
        status=status,
    )
    session.add(recommendation)
    session.commit()
    return recommendation.id


def _mock_spark_client(monkeypatch, wait_results: list[JobRunResult]) -> MagicMock:
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = [f"cryo-spark-{i}" for i in range(len(wait_results))]
    mock_client.wait_for_completion.side_effect = wait_results
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient",
        lambda config: mock_client,
    )
    return mock_client


def _success(job_id: str) -> JobRunResult:
    return JobRunResult(job_run_id=job_id, state=JobRunState.SUCCESS)


def _failure(job_id: str, detail: str) -> JobRunResult:
    return JobRunResult(job_run_id=job_id, state=JobRunState.FAILED, state_details=detail)


def test_execute_applies_sort_strategy_and_persists_write_sort_order(session, monkeypatch) -> None:
    rec_id = _seed(session, recommended_strategy="sort", recommended_columns=["event_date"])
    mock_client = _mock_spark_client(monkeypatch, [_success("rewrite"), _success("persist")])

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    assert len(response.job_ids) == 1
    assert mock_client.submit_spark_sql.call_count == 2
    rewrite_sql = mock_client.submit_spark_sql.call_args_list[0].args[0]
    persist_sql = mock_client.submit_spark_sql.call_args_list[1].args[0]
    assert "rewrite_data_files" in rewrite_sql
    assert "WRITE ORDERED BY" in persist_sql
    assert "`event_date` ASC" in persist_sql

    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "applied"
    assert recommendation.applied_at is not None
    assert recommendation.applied_run_id == response.run_id


def test_execute_applies_zorder_strategy_without_sort_order_persist(session, monkeypatch) -> None:
    rec_id = _seed(
        session, recommended_strategy="zorder", recommended_columns=["tenant_id", "event_date"]
    )
    mock_client = _mock_spark_client(monkeypatch, [_success("rewrite")])

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    assert mock_client.submit_spark_sql.call_count == 1
    rewrite_sql = mock_client.submit_spark_sql.call_args_list[0].args[0]
    assert "zorder" in rewrite_sql.lower()

    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "applied"


def test_execute_uses_destination_directly_when_it_is_already_spark(session, monkeypatch) -> None:
    rec_id = _seed_with_spark_primary_destination(
        session, recommended_strategy="sort", recommended_columns=["ProspectActivityAutoId"]
    )
    mock_client = _mock_spark_client(monkeypatch, [_success("rewrite"), _success("persist")])

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    assert mock_client.submit_spark_sql.call_count == 2
    rewrite_sql = mock_client.submit_spark_sql.call_args_list[0].args[0]
    persist_sql = mock_client.submit_spark_sql.call_args_list[1].args[0]
    assert "rewrite_data_files" in rewrite_sql
    assert "WRITE ORDERED BY" in persist_sql
    assert "`ProspectActivityAutoId` ASC" in persist_sql

    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "applied"
    assert recommendation.applied_at is not None


def test_execute_rejects_columns_not_present_on_real_schema(session, monkeypatch) -> None:
    """Regression, 2026-08-11: recommended_columns comes straight from QueryPattern.column_name
    telemetry, never validated against the table's real Iceberg schema -- confirmed live on
    lsq_pw.silver_prospect_activity_base, whose recommendation included 'mx_customobject_1'
    (not a real column, a query alias) and dispatched a real Spark job that failed deep in a
    Java stack trace only after the rewrite had already started. Must fail fast instead."""
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["ProspectActivityAutoId", "mx_customobject_1"],
        column_quality=["ProspectActivityId", "ProspectActivityAutoId", "ActivityEvent"],
    )
    mock_client = _mock_spark_client(monkeypatch, [])

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
        )

    assert exc_info.value.status_code == 422
    assert "mx_customobject_1" in exc_info.value.detail
    mock_client.submit_spark_sql.assert_not_called()

    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "approved"


def test_execute_resolves_case_mismatched_columns_to_real_schema_casing(
    session, monkeypatch
) -> None:
    """Query telemetry routinely lowercases column names (e.g. 'activityevent'), but
    NamedReference.bind (what WRITE ORDERED BY resolves against) is case-sensitive -- confirmed
    live, the real column is 'ActivityEvent'. Resolve to the schema's real casing instead of
    failing outright, since this is a benign, fully-recoverable mismatch."""
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["activityevent"],
        column_quality=["ProspectActivityAutoId", "ActivityEvent"],
    )
    mock_client = _mock_spark_client(monkeypatch, [_success("rewrite"), _success("persist")])

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    persist_sql = mock_client.submit_spark_sql.call_args_list[1].args[0]
    assert "`ActivityEvent` ASC" in persist_sql
    assert "`activityevent`" not in persist_sql


def test_execute_skips_schema_validation_when_no_column_quality_available(
    session, monkeypatch
) -> None:
    """A table with no TableStats.column_quality yet (e.g. never scanned) must not be blocked
    outright -- fall back to trusting the recommendation rather than rejecting everything."""
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
        column_quality=None,
    )
    mock_client = _mock_spark_client(monkeypatch, [_success("rewrite"), _success("persist")])

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    mock_client.submit_spark_sql.assert_called()


def test_execute_rejects_when_not_approved(session, monkeypatch) -> None:
    rec_id = _seed(session, status="pending")
    mock_client = _mock_spark_client(monkeypatch, [])

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
        )

    assert exc_info.value.status_code == 400
    assert "approved" in exc_info.value.detail
    mock_client.submit_spark_sql.assert_not_called()
    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "pending"


def test_execute_rejects_strategy_with_no_actionable_rewrite(session, monkeypatch) -> None:
    rec_id = _seed(session, recommended_strategy="none", recommended_columns=[])
    mock_client = _mock_spark_client(monkeypatch, [])

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
        )

    assert exc_info.value.status_code == 400
    assert "No actionable rewrite" in exc_info.value.detail
    mock_client.submit_spark_sql.assert_not_called()


def test_execute_marks_failed_on_rewrite_failure_and_does_not_mark_applied(
    session, monkeypatch
) -> None:
    """This is the exact regression this route was rewritten to fix: previously any dispatch
    (even a failed or entirely wrong one) unconditionally flipped the recommendation to
    "applied". A failed rewrite must surface as an error and leave the recommendation alone."""
    rec_id = _seed(session, recommended_strategy="sort", recommended_columns=["event_date"])
    _mock_spark_client(monkeypatch, [_failure("rewrite", "rewrite_data_files failed: boom")])

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
        )

    assert exc_info.value.status_code == 502
    assert "boom" in exc_info.value.detail

    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "approved"
    assert recommendation.applied_at is None
    assert recommendation.applied_run_id is None


def test_execute_succeeds_even_if_sort_order_persist_fails(session, monkeypatch) -> None:
    """write.sort-order persistence is best-effort: the physical rewrite already happened, so a
    failure only to ALTER TABLE ... WRITE ORDERED BY shouldn't roll back a real, successful
    rewrite or block marking the recommendation applied."""
    rec_id = _seed(session, recommended_strategy="sort", recommended_columns=["event_date"])
    _mock_spark_client(
        monkeypatch,
        [_success("rewrite"), _failure("persist", "ALTER TABLE failed: permission denied")],
    )

    response = execute_layout_recommendation(
        rec_id, ExecuteLayoutRecommendationRequest(), session, PRINCIPAL
    )

    assert response.status == "succeeded"
    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "applied"


def test_durable_execute_tracks_rewrite_and_sort_order_effects(
    session,
    monkeypatch,
) -> None:
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
    )
    _mock_spark_client(monkeypatch, [_success("rewrite"), _success("persist")])
    settings = get_settings().model_copy(
        update={
            "durable_intents_enabled": True,
            "plan_verifier_enabled": False,
        }
    )
    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.get_settings",
        lambda: settings,
    )

    response = execute_layout_recommendation(
        rec_id,
        ExecuteLayoutRecommendationRequest(),
        session,
        PRINCIPAL,
    )

    intent = session.query(MaintenanceIntent).one()
    receipts = session.query(EffectReceipt).order_by(EffectReceipt.step_index).all()
    assert response.status == "succeeded"
    assert intent.status == "succeeded"
    assert intent.canonical_plan["operations"][0]["params"]["persist_sort_order"] is True
    assert [receipt.state for receipt in receipts] == ["succeeded", "succeeded"]
    assert [receipt.step_index for receipt in receipts] == [0, 1]
    assert all(receipt.destination_id == intent.destination_id for receipt in receipts)


def test_durable_execute_does_not_replay_unknown_sort_order_effect(
    session,
    monkeypatch,
) -> None:
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
    )
    mock_client = MagicMock()
    mock_client.submit_spark_sql.side_effect = ["rewrite-job", "persist-job"]
    mock_client.wait_for_completion.side_effect = [
        _success("rewrite-job"),
        TimeoutError("persist response timed out"),
    ]
    monkeypatch.setattr(
        "cryo.engines.spark.k8s_engine.SparkK8sClient",
        lambda config: mock_client,
    )
    settings = get_settings().model_copy(
        update={
            "durable_intents_enabled": True,
            "plan_verifier_enabled": False,
        }
    )
    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.get_settings",
        lambda: settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id,
            ExecuteLayoutRecommendationRequest(),
            session,
            PRINCIPAL,
        )

    assert exc_info.value.status_code == 502
    session.expire_all()
    recommendation = session.get(LayoutRecommendation, rec_id)
    assert recommendation.status == "approved"
    intent = session.query(MaintenanceIntent).one()
    receipts = session.query(EffectReceipt).order_by(EffectReceipt.step_index).all()
    assert intent.status == "reconcile_required"
    assert [receipt.state for receipt in receipts] == ["succeeded", "unknown"]
    assert receipts[1].external_reference == "persist-job"


def test_durable_layout_stops_after_post_effect_budget_breach(
    session,
    monkeypatch,
) -> None:
    rec_id = _seed(
        session,
        recommended_strategy="sort",
        recommended_columns=["event_date"],
        total_files=1,
    )
    executed: list[RenderedStatement] = []

    class BudgetBreachEngine:
        type = EngineType.SPARK_EMR

        def execute_plan(self, plan, ctx=None):
            statement = RenderedStatement(
                action=plan.operations[0].type,
                sql="rewrite",
                engine=EngineType.SPARK_EMR,
            )
            executed.append(statement)
            return [
                ExecResult(
                    ok=True,
                    engine_ref="rewrite-budget",
                    detail="rewrite",
                    duration_ms=100,
                    output_files_count=2,
                )
            ]

        def execute(self, statement, ctx=None):
            executed.append(statement)
            return ExecResult(ok=True, engine_ref="persist-budget", detail=statement.sql)

    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.SparkK8sEngine",
        lambda config: BudgetBreachEngine(),
    )
    settings = get_settings().model_copy(
        update={
            "durable_intents_enabled": True,
            "plan_verifier_enabled": False,
        }
    )
    monkeypatch.setattr(
        "cryo.api.routes_smart_maintenance.get_settings",
        lambda: settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        execute_layout_recommendation(
            rec_id,
            ExecuteLayoutRecommendationRequest(),
            session,
            PRINCIPAL,
        )

    assert exc_info.value.status_code == 409
    assert "output_files_budget_exceeded" in exc_info.value.detail
    assert len(executed) == 1
    session.expire_all()
    recommendation = session.get(LayoutRecommendation, rec_id)
    intent = session.query(MaintenanceIntent).one()
    receipt = session.query(EffectReceipt).one()
    assert recommendation.status == "approved"
    assert intent.status == "failed"
    assert receipt.state == "succeeded"
