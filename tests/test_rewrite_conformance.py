from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Source, Table
from cryo.optimizer.conformance import (
    DeployedRewriteEvidence,
    RewriteConformanceFixture,
    RewriteOutputSnapshot,
    deployed_attestation_sha256,
    evaluate_rewrite_conformance,
    record_deployed_rewrite_conformance,
    record_rewrite_conformance,
)

ROW_HASH = "1" * 64
SCHEMA_HASH = "2" * 64
SPEC_HASH = "3" * 64
SCOPE_HASH = "4" * 64


def test_fixture_proves_cross_engine_output_and_scope_invariants(session: Session) -> None:
    table, recommendation = _seed(session)
    fixture = _fixture()

    report = record_rewrite_conformance(session, table.id, fixture, actor="operator-1")

    assert report.status == "conformant"
    assert report.conformant is True
    assert report.qualifies_production_gate is False
    assert "cross_engine_row_multiset_preserved" in report.invariant_checks
    session.refresh(recommendation)
    evidence = (recommendation.detail or {})["output_conformance"]
    assert evidence["schema"] == "rewrite-output-conformance/v1"
    audit = session.query(AuditLog).filter_by(table_id=table.id).one()
    assert audit.status == "ADVISORY"
    assert audit.actor == "operator-1"


def test_fixture_rejects_row_loss_and_out_of_scope_touch() -> None:
    fixture = _fixture(
        spark=RewriteOutputSnapshot(
            engine="spark",
            row_count=99,
            row_multiset_sha256="5" * 64,
            schema_sha256=SCHEMA_HASH,
            partition_spec_sha256=SPEC_HASH,
            touched_scope_sha256=("6" * 64,),
        )
    )

    report = evaluate_rewrite_conformance(fixture)

    assert report.status == "nonconformant"
    assert "spark_row_count_mismatch" in report.reasons
    assert "spark_row_multiset_mismatch" in report.reasons
    assert "spark_touched_scope_outside_bound" in report.reasons
    assert "cross_engine_row_multiset_mismatch" in report.reasons


def test_deployed_collector_evidence_qualifies_only_when_fresh_and_bound(
    session: Session,
) -> None:
    table, recommendation = _seed(session)
    now = datetime.now(UTC)
    collector_id = "spiffe://cryo/production/conformance-collector"
    fixture = _deployed_fixture()
    evidence = DeployedRewriteEvidence(
        fixture=fixture,
        collected_at=now,
        deployment_id="production-us-east-1",
        environment="production",
        pto_params_sha256="5" * 64,
        rewrite_scope_sha256="6" * 64,
        attestation_sha256="",
    )
    evidence = replace(
        evidence,
        attestation_sha256=deployed_attestation_sha256(
            evidence,
            collector_id=collector_id,
        ),
    )

    report = record_deployed_rewrite_conformance(
        session,
        table.id,
        evidence,
        collector_id=collector_id,
        now=now,
    )

    assert report.qualifies_production_gate is True
    assert report.qualification_reason == "verified_deployed_cross_engine_output"
    session.refresh(recommendation)
    persisted = (recommendation.detail or {})["output_conformance"]
    assert persisted["schema"] == "rewrite-output-conformance/v2"
    assert persisted["collector_id_sha256"] != collector_id
    assert persisted["deployment_id_sha256"] != evidence.deployment_id


def test_deployed_evidence_rejects_stale_or_rebound_attestation(session: Session) -> None:
    table, _ = _seed(session)
    now = datetime.now(UTC)
    collector_id = "spiffe://cryo/production/conformance-collector"
    evidence = DeployedRewriteEvidence(
        fixture=_deployed_fixture(),
        collected_at=now - timedelta(minutes=16),
        deployment_id="production-us-east-1",
        environment="production",
        pto_params_sha256="5" * 64,
        rewrite_scope_sha256="6" * 64,
        attestation_sha256="0" * 64,
    )

    report = record_deployed_rewrite_conformance(
        session,
        table.id,
        evidence,
        collector_id=collector_id,
        now=now,
    )

    assert report.qualifies_production_gate is False
    assert "deployed_evidence_expired" in report.reasons
    assert "attestation_hash_mismatch" in report.reasons


def _fixture(
    *,
    spark: RewriteOutputSnapshot | None = None,
) -> RewriteConformanceFixture:
    baseline = RewriteOutputSnapshot(
        engine="baseline",
        row_count=100,
        row_multiset_sha256=ROW_HASH,
        schema_sha256=SCHEMA_HASH,
        partition_spec_sha256=SPEC_HASH,
        touched_scope_sha256=(),
    )
    output = RewriteOutputSnapshot(
        engine="trino",
        row_count=100,
        row_multiset_sha256=ROW_HASH,
        schema_sha256=SCHEMA_HASH,
        partition_spec_sha256=SPEC_HASH,
        touched_scope_sha256=(SCOPE_HASH,),
    )
    return RewriteConformanceFixture(
        fixture_id="day-partition-bounded-v1",
        baseline=baseline,
        trino=output,
        spark=spark
        or RewriteOutputSnapshot(
            engine="spark",
            row_count=100,
            row_multiset_sha256=ROW_HASH,
            schema_sha256=SCHEMA_HASH,
            partition_spec_sha256=SPEC_HASH,
            touched_scope_sha256=(SCOPE_HASH,),
        ),
        allowed_scope_sha256=(SCOPE_HASH,),
    )


def _deployed_fixture() -> RewriteConformanceFixture:
    baseline = replace(
        _fixture().baseline,
        snapshot_id=100,
        metadata_location_sha256="7" * 64,
    )
    trino = replace(
        _fixture().trino,
        snapshot_id=101,
        metadata_location_sha256="8" * 64,
    )
    spark = replace(
        _fixture().spark,
        snapshot_id=201,
        metadata_location_sha256="9" * 64,
    )
    return RewriteConformanceFixture(
        fixture_id="production-bounded-rewrite-v1",
        baseline=baseline,
        trino=trino,
        spark=spark,
        allowed_scope_sha256=(SCOPE_HASH,),
        provenance="deployed_collector/v1",
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
        status="pending",
        detail={
            "source": "pto",
            "shadow": True,
            "differential_oracle": {
                "pto_params_sha256": "5" * 64,
                "rewrite_scope_sha256": "6" * 64,
            },
        },
    )
    session.add_all([source, table, recommendation])
    session.commit()
    return table, recommendation
