from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Source, Table
from cryo.optimizer.differential import (
    evaluate_layout_recommendation,
    record_layout_differential,
)


def test_differential_rejects_sort_that_trino_cannot_render(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "sort",
            "sort_columns": ["event_date"],
        },
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.equivalent is False
    assert report.canary_eligible is False
    assert report.trino is not None and report.trino.supported is False
    assert report.spark is not None and report.spark.supported is True
    assert "trino_does_not_render_sort_clustering" in report.reasons


def test_differential_rejects_unbounded_binpack(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.equivalent is False
    assert report.canary_eligible is False
    assert report.trino is not None and report.trino.rendered_sql_sha256
    assert report.spark is not None and report.spark.rendered_sql_sha256
    assert report.scope is not None and report.scope.bounded is False
    assert report.reasons == (
        "missing_bounded_rewrite_scope",
        "recommendation_not_approved",
        "missing_recorded_differential_evidence",
        "missing_holdout_evidence",
        "missing_output_conformance_evidence",
        "missing_production_safety_evidence",
    )


def test_differential_rejects_unrendered_partition_and_row_group_params(
    session: Session,
) -> None:
    table = _seed(
        session,
        {
            "partition_spec": "identity(region)",
            "target_file_size_mb": 256,
            "row_group_size_mb": 32,
            "strategy": "binpack",
            "sort_columns": [],
        },
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.equivalent is False
    assert "partition_evolution_not_rendered" in report.reasons
    assert "row_group_size_not_rendered" in report.reasons


def test_record_differential_persists_versioned_evidence_and_audit(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
    )

    report = record_layout_differential(session, table.id, actor="operator-1")

    recommendation = session.get(LayoutRecommendation, report.recommendation_id)
    assert recommendation is not None
    evidence = (recommendation.detail or {})["differential_oracle"]
    assert evidence["schema"] == "layout-differential/v2"
    assert len(evidence["pto_params_sha256"]) == 64
    assert len(evidence["rewrite_scope_sha256"]) == 64
    assert evidence["report"]["canary_eligible"] is False
    audit = session.query(AuditLog).filter_by(table_id=table.id).one()
    assert audit.action == "layout_differential_recorded"
    assert audit.status == "BLOCKED"
    assert audit.actor == "operator-1"


def test_differential_proves_bounded_scope_but_not_full_engine_equivalence(
    session: Session,
) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
        rewrite_scope={
            "schema": "partition-scope/v1",
            "estimated_bytes": 1024**3,
            "file_count": 12,
            "predicates": [
                {
                    "terms": [
                        {
                            "base_column": "event_date",
                            "transform": "day",
                            "value": "2026-08-04",
                            "col_type": "date",
                        }
                    ]
                }
            ],
        },
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.scope is not None
    assert report.scope.bounded is True
    assert report.scope.equivalent is True
    assert report.scope.predicate_count == 1
    assert report.scope.normalized_predicates_sha256
    assert report.equivalent is True
    assert report.canary_eligible is False
    assert report.reasons == (
        "recommendation_not_approved",
        "missing_recorded_differential_evidence",
        "missing_holdout_evidence",
        "missing_output_conformance_evidence",
        "missing_production_safety_evidence",
    )


def test_differential_rejects_scope_over_safety_budget(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
        rewrite_scope={
            "schema": "partition-scope/v1",
            "estimated_bytes": 11 * 1024**3,
            "file_count": 501,
            "predicates": [
                {
                    "terms": [
                        {
                            "base_column": "region",
                            "transform": "identity",
                            "value": "us",
                            "col_type": "string",
                        }
                    ]
                }
            ],
        },
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.scope is not None and report.scope.bounded is False
    assert "rewrite_scope_exceeds_byte_limit" in report.reasons
    assert "rewrite_scope_exceeds_file_limit" in report.reasons


def test_approved_equivalent_scope_still_requires_holdout_and_safety_gates(
    session: Session,
) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
        rewrite_scope={
            "schema": "partition-scope/v1",
            "estimated_bytes": 1024**3,
            "file_count": 12,
            "predicates": [
                {
                    "terms": [
                        {
                            "base_column": "region",
                            "transform": "identity",
                            "value": "us",
                            "col_type": "string",
                        }
                    ]
                }
            ],
        },
        status="approved",
    )

    report = evaluate_layout_recommendation(session, table.id)

    assert report.equivalent is True
    assert report.canary_eligible is False
    assert report.reasons == (
        "missing_recorded_differential_evidence",
        "missing_holdout_evidence",
        "missing_output_conformance_evidence",
        "missing_production_safety_evidence",
    )


def test_advisory_evidence_is_distinct_from_missing_evidence(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
        rewrite_scope={
            "schema": "partition-scope/v1",
            "estimated_bytes": 1024**3,
            "file_count": 12,
            "predicates": [
                {
                    "terms": [
                        {
                            "base_column": "region",
                            "transform": "identity",
                            "value": "us",
                            "col_type": "string",
                        }
                    ]
                }
            ],
        },
        status="approved",
    )
    recommendation = session.query(LayoutRecommendation).filter_by(table_id=table.id).one()
    detail = dict(recommendation.detail or {})
    detail["holdout_evidence"] = {"report": {"qualifies_for_canary": False}}
    detail["output_conformance"] = {"report": {"qualifies_production_gate": False}}
    recommendation.detail = detail
    session.commit()

    report = evaluate_layout_recommendation(session, table.id)

    assert report.reasons == (
        "missing_recorded_differential_evidence",
        "holdout_evidence_not_qualified",
        "output_conformance_not_production_qualified",
        "missing_production_safety_evidence",
    )


def test_recorded_differential_becomes_stale_when_parameters_change(session: Session) -> None:
    table = _seed(
        session,
        {
            "partition_spec": None,
            "target_file_size_mb": 256,
            "row_group_size_mb": None,
            "strategy": "binpack",
            "sort_columns": [],
        },
    )
    record_layout_differential(session, table.id, actor="operator-1")
    recommendation = session.query(LayoutRecommendation).filter_by(table_id=table.id).one()
    detail = dict(recommendation.detail or {})
    params = dict(detail["pto_params"])
    params["target_file_size_mb"] = 512
    detail["pto_params"] = params
    recommendation.detail = detail
    session.commit()

    report = evaluate_layout_recommendation(session, table.id)

    assert "recorded_differential_evidence_stale" in report.reasons


def _seed(
    session: Session,
    pto_params: dict[str, object],
    *,
    rewrite_scope: dict[str, object] | None = None,
    status: str = "pending",
) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    recommendation = LayoutRecommendation(
        id=uuid4(),
        table_id=table.id,
        recommended_strategy=str(pto_params["strategy"]),
        recommended_columns=[],
        status=status,
        detail={
            "source": "pto",
            "shadow": True,
            "pto_params": pto_params,
            **({"rewrite_scope": rewrite_scope} if rewrite_scope is not None else {}),
        },
    )
    session.add_all([source, table, recommendation])
    session.commit()
    return table
