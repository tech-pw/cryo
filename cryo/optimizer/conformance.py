from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Table


@dataclass(frozen=True, slots=True)
class RewriteOutputSnapshot:
    engine: str
    row_count: int
    row_multiset_sha256: str
    schema_sha256: str
    partition_spec_sha256: str
    touched_scope_sha256: tuple[str, ...]
    snapshot_id: int | None = None
    metadata_location_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RewriteConformanceFixture:
    fixture_id: str
    baseline: RewriteOutputSnapshot
    trino: RewriteOutputSnapshot
    spark: RewriteOutputSnapshot
    allowed_scope_sha256: tuple[str, ...]
    provenance: str = "synthetic_fixture"


@dataclass(frozen=True, slots=True)
class RewriteConformanceReport:
    status: str
    fixture_id: str
    provenance: str
    conformant: bool
    invariant_checks: tuple[str, ...]
    reasons: tuple[str, ...]
    qualifies_production_gate: bool = False
    qualification_reason: str = "synthetic_fixture_not_production_evidence"


@dataclass(frozen=True, slots=True)
class DeployedRewriteEvidence:
    fixture: RewriteConformanceFixture
    collected_at: datetime
    deployment_id: str
    environment: str
    pto_params_sha256: str
    rewrite_scope_sha256: str
    attestation_sha256: str


def evaluate_rewrite_conformance(
    fixture: RewriteConformanceFixture,
) -> RewriteConformanceReport:
    reasons: list[str] = []
    checks: list[str] = []
    _validate_snapshot(fixture.baseline, "baseline", reasons)
    _validate_snapshot(fixture.trino, "trino", reasons)
    _validate_snapshot(fixture.spark, "spark", reasons)
    for snapshot, expected_engine in (
        (fixture.baseline, "baseline"),
        (fixture.trino, "trino"),
        (fixture.spark, "spark"),
    ):
        if snapshot.engine != expected_engine:
            reasons.append(f"{expected_engine}_engine_label_mismatch")
    allowed = set(fixture.allowed_scope_sha256)
    if not allowed:
        reasons.append("empty_allowed_scope")
    elif any(not _is_sha256(value) for value in allowed):
        reasons.append("invalid_allowed_scope_sha256")

    for output, prefix in ((fixture.trino, "trino"), (fixture.spark, "spark")):
        _equal(output.row_count, fixture.baseline.row_count, f"{prefix}_row_count", checks, reasons)
        _equal(
            output.row_multiset_sha256,
            fixture.baseline.row_multiset_sha256,
            f"{prefix}_row_multiset",
            checks,
            reasons,
        )
        _equal(
            output.schema_sha256,
            fixture.baseline.schema_sha256,
            f"{prefix}_schema",
            checks,
            reasons,
        )
        _equal(
            output.partition_spec_sha256,
            fixture.baseline.partition_spec_sha256,
            f"{prefix}_partition_spec",
            checks,
            reasons,
        )
        touched = set(output.touched_scope_sha256)
        if not touched:
            reasons.append(f"{prefix}_empty_touched_scope")
        elif not touched.issubset(allowed):
            reasons.append(f"{prefix}_touched_scope_outside_bound")
        else:
            checks.append(f"{prefix}_scope_bounded")

    _equal(
        fixture.trino.row_multiset_sha256,
        fixture.spark.row_multiset_sha256,
        "cross_engine_row_multiset",
        checks,
        reasons,
    )
    _equal(
        fixture.trino.schema_sha256,
        fixture.spark.schema_sha256,
        "cross_engine_schema",
        checks,
        reasons,
    )
    _equal(
        fixture.trino.partition_spec_sha256,
        fixture.spark.partition_spec_sha256,
        "cross_engine_partition_spec",
        checks,
        reasons,
    )
    if fixture.provenance != "synthetic_fixture":
        reasons.append("unsupported_fixture_provenance")
    conformant = not reasons
    return RewriteConformanceReport(
        status="conformant" if conformant else "nonconformant",
        fixture_id=fixture.fixture_id,
        provenance=fixture.provenance,
        conformant=conformant,
        invariant_checks=tuple(checks),
        reasons=tuple(sorted(set(reasons))),
    )


def record_rewrite_conformance(
    session: Session,
    table_id: uuid.UUID,
    fixture: RewriteConformanceFixture,
    *,
    actor: str,
) -> RewriteConformanceReport:
    if session.get(Table, table_id) is None:
        raise ValueError(f"Table not found: {table_id}")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        raise ValueError(f"PTO recommendation not found for table: {table_id}")
    differential = (recommendation.detail or {}).get("differential_oracle")
    if not isinstance(differential, dict):
        raise ValueError("Recorded differential evidence is required")
    report = evaluate_rewrite_conformance(fixture)
    evidence: dict[str, object] = {
        "schema": "rewrite-output-conformance/v1",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "pto_params_sha256": differential.get("pto_params_sha256"),
        "rewrite_scope_sha256": differential.get("rewrite_scope_sha256"),
        "fixture": _fixture_payload(fixture),
        "report": asdict(report),
    }
    detail = dict(recommendation.detail or {})
    detail["output_conformance"] = evidence
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="rewrite_output_conformance_recorded",
            status="ADVISORY" if report.conformant else "BLOCKED",
            engine="cross_engine_fixture",
            detail=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            actor=actor,
        )
    )
    session.commit()
    return report


def deployed_attestation_sha256(
    evidence: DeployedRewriteEvidence,
    *,
    collector_id: str,
) -> str:
    material = {
        "schema": "deployed-rewrite-evidence/v1",
        "collector_id": collector_id,
        "collected_at": evidence.collected_at.isoformat(),
        "deployment_id": evidence.deployment_id,
        "environment": evidence.environment,
        "pto_params_sha256": evidence.pto_params_sha256,
        "rewrite_scope_sha256": evidence.rewrite_scope_sha256,
        "fixture": _fixture_payload(evidence.fixture),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def record_deployed_rewrite_conformance(
    session: Session,
    table_id: uuid.UUID,
    evidence: DeployedRewriteEvidence,
    *,
    collector_id: str,
    now: datetime | None = None,
) -> RewriteConformanceReport:
    if session.get(Table, table_id) is None:
        raise ValueError(f"Table not found: {table_id}")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        raise ValueError(f"PTO recommendation not found for table: {table_id}")
    differential = (recommendation.detail or {}).get("differential_oracle")
    if not isinstance(differential, dict):
        raise ValueError("Recorded differential evidence is required")

    report = _evaluate_deployed_evidence(
        evidence,
        collector_id=collector_id,
        expected_pto_params_sha256=differential.get("pto_params_sha256"),
        expected_scope_sha256=differential.get("rewrite_scope_sha256"),
        now=now or datetime.now(UTC),
    )
    persisted: dict[str, object] = {
        "schema": "rewrite-output-conformance/v2",
        "evaluated_at": (now or datetime.now(UTC)).isoformat(),
        "pto_params_sha256": evidence.pto_params_sha256,
        "rewrite_scope_sha256": evidence.rewrite_scope_sha256,
        "collector_id_sha256": sha256(collector_id.encode()).hexdigest(),
        "deployment_id_sha256": sha256(evidence.deployment_id.encode()).hexdigest(),
        "environment": evidence.environment,
        "collected_at": evidence.collected_at.isoformat(),
        "attestation_sha256": evidence.attestation_sha256,
        "fixture": _fixture_payload(evidence.fixture),
        "report": asdict(report),
    }
    detail = dict(recommendation.detail or {})
    detail["output_conformance"] = persisted
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="deployed_rewrite_output_conformance_recorded",
            status="OK" if report.qualifies_production_gate else "BLOCKED",
            engine="cross_engine_deployed",
            detail=json.dumps(persisted, sort_keys=True, separators=(",", ":")),
            actor=collector_id,
        )
    )
    session.commit()
    return report


def _evaluate_deployed_evidence(
    evidence: DeployedRewriteEvidence,
    *,
    collector_id: str,
    expected_pto_params_sha256: object,
    expected_scope_sha256: object,
    now: datetime,
) -> RewriteConformanceReport:
    base_report = evaluate_rewrite_conformance(
        RewriteConformanceFixture(
            fixture_id=evidence.fixture.fixture_id,
            baseline=evidence.fixture.baseline,
            trino=evidence.fixture.trino,
            spark=evidence.fixture.spark,
            allowed_scope_sha256=evidence.fixture.allowed_scope_sha256,
        )
    )
    qualification_reasons: list[str] = []
    if evidence.fixture.provenance != "deployed_collector/v1":
        qualification_reasons.append("unsupported_deployed_provenance")
    if evidence.environment != "production":
        qualification_reasons.append("non_production_environment")
    if not collector_id:
        qualification_reasons.append("missing_collector_identity")
    if not evidence.deployment_id:
        qualification_reasons.append("missing_deployment_id")
    if evidence.collected_at.tzinfo is None:
        qualification_reasons.append("naive_collected_at")
    else:
        age = now - evidence.collected_at
        if age > timedelta(minutes=15):
            qualification_reasons.append("deployed_evidence_expired")
        if age < -timedelta(minutes=5):
            qualification_reasons.append("deployed_evidence_from_future")
    if evidence.pto_params_sha256 != expected_pto_params_sha256:
        qualification_reasons.append("pto_params_hash_mismatch")
    if evidence.rewrite_scope_sha256 != expected_scope_sha256:
        qualification_reasons.append("rewrite_scope_hash_mismatch")
    if evidence.attestation_sha256 != deployed_attestation_sha256(
        evidence, collector_id=collector_id
    ):
        qualification_reasons.append("attestation_hash_mismatch")
    _validate_deployed_snapshots(evidence.fixture, qualification_reasons)
    qualifies = base_report.conformant and not qualification_reasons
    return RewriteConformanceReport(
        status=base_report.status,
        fixture_id=evidence.fixture.fixture_id,
        provenance=evidence.fixture.provenance,
        conformant=base_report.conformant,
        invariant_checks=base_report.invariant_checks,
        reasons=tuple(sorted(set((*base_report.reasons, *qualification_reasons)))),
        qualifies_production_gate=qualifies,
        qualification_reason=(
            "verified_deployed_cross_engine_output"
            if qualifies
            else "deployed_evidence_not_qualified"
        ),
    )


def _validate_deployed_snapshots(
    fixture: RewriteConformanceFixture,
    reasons: list[str],
) -> None:
    snapshots = (fixture.baseline, fixture.trino, fixture.spark)
    for snapshot in snapshots:
        if snapshot.snapshot_id is None or snapshot.snapshot_id < 1:
            reasons.append(f"{snapshot.engine}_missing_snapshot_id")
        if snapshot.metadata_location_sha256 is None or not _is_sha256(
            snapshot.metadata_location_sha256
        ):
            reasons.append(f"{snapshot.engine}_invalid_metadata_location_sha256")
    baseline_id = fixture.baseline.snapshot_id
    if fixture.trino.snapshot_id == baseline_id:
        reasons.append("trino_output_snapshot_not_distinct")
    if fixture.spark.snapshot_id == baseline_id:
        reasons.append("spark_output_snapshot_not_distinct")


def _validate_snapshot(
    snapshot: RewriteOutputSnapshot,
    label: str,
    reasons: list[str],
) -> None:
    if snapshot.row_count < 0:
        reasons.append(f"{label}_negative_row_count")
    for field_name, value in (
        ("row_multiset", snapshot.row_multiset_sha256),
        ("schema", snapshot.schema_sha256),
        ("partition_spec", snapshot.partition_spec_sha256),
    ):
        if not _is_sha256(value):
            reasons.append(f"{label}_invalid_{field_name}_sha256")
    if any(not _is_sha256(value) for value in snapshot.touched_scope_sha256):
        reasons.append(f"{label}_invalid_touched_scope_sha256")


def _equal(
    actual: object,
    expected: object,
    name: str,
    checks: list[str],
    reasons: list[str],
) -> None:
    if actual == expected:
        checks.append(f"{name}_preserved")
    else:
        reasons.append(f"{name}_mismatch")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _fixture_payload(fixture: RewriteConformanceFixture) -> dict[str, object]:
    return {
        "fixture_id": fixture.fixture_id,
        "provenance": fixture.provenance,
        "baseline": asdict(fixture.baseline),
        "trino": asdict(fixture.trino),
        "spark": asdict(fixture.spark),
        "allowed_scope_sha256": list(fixture.allowed_scope_sha256),
    }
