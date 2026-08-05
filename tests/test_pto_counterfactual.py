from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Source, Table
from cryo.optimizer.pto import (
    CounterfactualArm,
    CounterfactualExperiment,
    evaluate_counterfactual_experiment,
    record_counterfactual_experiment,
)

PARAMS_HASH = "1" * 64
SCOPE_HASH = "2" * 64


def test_controlled_shadow_qualifies_only_with_sufficient_safe_lift(session: Session) -> None:
    table, recommendation = _seed(session)

    report = record_counterfactual_experiment(
        session,
        table.id,
        _experiment(),
        actor="operator-1",
    )

    assert report.status == "qualified"
    assert report.scan_improvement_pct == 30.0
    assert report.file_read_improvement_pct == 20.0
    assert report.p95_latency_change_pct == 5.0
    assert report.qualifies_for_canary is True
    session.refresh(recommendation)
    evidence = (recommendation.detail or {})["holdout_evidence"]
    assert evidence["schema"] == "pto-controlled-shadow/v1"
    audit = session.query(AuditLog).filter_by(table_id=table.id).one()
    assert audit.status == "QUALIFIED"


def test_controlled_shadow_rejects_regression_and_underpowered_arms() -> None:
    experiment = _experiment(
        control=CounterfactualArm(29, "3" * 64, 1000, 100, 0, 100),
        candidate=CounterfactualArm(29, "3" * 64, 1100, 110, 2, 130),
    )

    report = evaluate_counterfactual_experiment(experiment)

    assert report.qualifies_for_canary is False
    assert "insufficient_control_queries" in report.reasons
    assert "insufficient_candidate_queries" in report.reasons


def test_controlled_shadow_rejects_evidence_binding_mismatch(session: Session) -> None:
    table, _recommendation = _seed(session)
    experiment = _experiment(rewrite_scope_sha256="9" * 64)

    report = record_counterfactual_experiment(session, table.id, experiment, actor="operator-1")

    assert report.qualifies_for_canary is False
    assert "rewrite_scope_evidence_mismatch" in report.reasons


def test_controlled_shadow_rejects_different_workloads_between_arms() -> None:
    experiment = _experiment(
        candidate=CounterfactualArm(50, "8" * 64, 7_000, 800, 0, 105),
    )

    report = evaluate_counterfactual_experiment(experiment)

    assert report.qualifies_for_canary is False
    assert "workload_signature_mismatch" in report.reasons


def _experiment(
    *,
    control: CounterfactualArm | None = None,
    candidate: CounterfactualArm | None = None,
    rewrite_scope_sha256: str = SCOPE_HASH,
) -> CounterfactualExperiment:
    return CounterfactualExperiment(
        experiment_id="shadow-2026-08-05",
        workload_sha256="3" * 64,
        dataset_sha256="4" * 64,
        pto_params_sha256=PARAMS_HASH,
        rewrite_scope_sha256=rewrite_scope_sha256,
        control=control or CounterfactualArm(50, "3" * 64, 10_000, 1_000, 0, 100),
        candidate=candidate or CounterfactualArm(50, "3" * 64, 7_000, 800, 0, 105),
        isolated_clone=True,
    )


def _seed(session: Session) -> tuple[Table, LayoutRecommendation]:
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
        recommended_strategy="binpack",
        recommended_columns=[],
        status="approved",
        detail={
            "source": "pto",
            "shadow": True,
            "differential_oracle": {
                "pto_params_sha256": PARAMS_HASH,
                "rewrite_scope_sha256": SCOPE_HASH,
            },
        },
    )
    session.add_all([source, table, recommendation])
    session.commit()
    return table, recommendation
