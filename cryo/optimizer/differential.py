from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Table
from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.spark.sql import render_predicate as render_spark_predicate
from cryo.engines.trino.sql import render as render_trino
from cryo.engines.trino.sql import render_predicate as render_trino_predicate
from cryo.optimizer.production_safety import is_production_safety_evidence_current

MAX_CANARY_SCOPE_BYTES = 10 * 1024**3
MAX_CANARY_SCOPE_FILES = 500


@dataclass(frozen=True, slots=True)
class EngineRewriteSemantics:
    engine: str
    supported: bool
    strategy: str
    target_file_size_mb: int
    clustering_columns: tuple[str, ...]
    rendered_sql_sha256: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RewriteScopeSemantics:
    bounded: bool
    equivalent: bool
    estimated_bytes: int | None
    file_count: int | None
    predicate_count: int
    normalized_predicates_sha256: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DifferentialOracleReport:
    recommendation_id: uuid.UUID | None
    status: str
    equivalent: bool
    canary_eligible: bool
    trino: EngineRewriteSemantics | None
    spark: EngineRewriteSemantics | None
    scope: RewriteScopeSemantics | None
    reasons: tuple[str, ...]


def evaluate_layout_recommendation(
    session: Session,
    table_id: uuid.UUID,
    *,
    require_recorded_evidence: bool = True,
) -> DifferentialOracleReport:
    table = session.get(Table, table_id)
    if table is None:
        raise ValueError(f"Table not found: {table_id}")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        return DifferentialOracleReport(
            recommendation_id=None,
            status="abstained",
            equivalent=False,
            canary_eligible=False,
            trino=None,
            spark=None,
            scope=None,
            reasons=("missing_pto_recommendation",),
        )

    detail = recommendation.detail or {}
    params = detail.get("pto_params")
    if not isinstance(params, dict):
        return _abstain(recommendation.id, "missing_pto_params")
    strategy = params.get("strategy")
    target_mb = params.get("target_file_size_mb")
    row_group_mb = params.get("row_group_size_mb")
    partition_spec = params.get("partition_spec")
    sort_columns = params.get("sort_columns")
    if (
        not isinstance(strategy, str)
        or not isinstance(target_mb, int)
        or not isinstance(sort_columns, list)
        or not all(isinstance(column, str) for column in sort_columns)
    ):
        return _abstain(recommendation.id, "invalid_pto_params")

    shared_reasons: list[str] = []
    if partition_spec is not None:
        shared_reasons.append("partition_evolution_not_rendered")
    if row_group_mb is not None:
        shared_reasons.append("row_group_size_not_rendered")

    predicates, scope = _parse_rewrite_scope(detail.get("rewrite_scope"))
    current_params_sha256 = _payload_sha256(params)
    current_scope_sha256 = _payload_sha256(detail.get("rewrite_scope"))
    operation_params: dict[str, object] = {
        "strategy": strategy,
        "file_size_mb": target_mb,
        "target_file_size_bytes": target_mb * 1024 * 1024,
        "persist_target_file_size": False,
    }
    if strategy == "sort":
        operation_params["sort_columns"] = sort_columns
    elif strategy == "zorder":
        operation_params["zorder_columns"] = sort_columns
    plan = MaintenancePlan(
        table_ref=f"{table.schema_name}.{table.table_name}",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                type=OperationType.OPTIMIZE,
                params=operation_params,
                predicates=predicates or None,
            ),
        ),
    )
    trino_limitations = list(shared_reasons)
    if strategy != "binpack":
        trino_limitations.append(f"trino_does_not_render_{strategy}_clustering")
    spark_limitations = list(shared_reasons)
    trino = _semantics(
        "trino",
        strategy,
        target_mb,
        tuple(sort_columns),
        trino_limitations,
        render_trino(plan)[0].sql,
    )
    spark = _semantics(
        "spark",
        strategy,
        target_mb,
        tuple(sort_columns),
        spark_limitations,
        render_spark(plan)[0].sql,
    )
    rewrite_effects_equivalent = (
        trino.supported
        and spark.supported
        and trino.strategy == spark.strategy
        and trino.target_file_size_mb == spark.target_file_size_mb
        and trino.clustering_columns == spark.clustering_columns
    )
    side_effects_equivalent = True
    equivalent = rewrite_effects_equivalent and scope.equivalent and side_effects_equivalent
    reasons = tuple(sorted(set(trino.limitations + spark.limitations + scope.limitations)))
    if recommendation.status != "approved":
        reasons = (*reasons, "recommendation_not_approved")
    if require_recorded_evidence:
        differential = detail.get("differential_oracle")
        if not isinstance(differential, Mapping):
            reasons = (*reasons, "missing_recorded_differential_evidence")
        elif (
            differential.get("pto_params_sha256") != current_params_sha256
            or differential.get("rewrite_scope_sha256") != current_scope_sha256
        ):
            reasons = (*reasons, "recorded_differential_evidence_stale")
    holdout = detail.get("holdout_evidence")
    if not isinstance(holdout, Mapping):
        reasons = (*reasons, "missing_holdout_evidence")
    else:
        holdout_report = holdout.get("report")
        if (
            not isinstance(holdout_report, Mapping)
            or holdout_report.get("qualifies_for_canary") is not True
        ):
            reasons = (*reasons, "holdout_evidence_not_qualified")
        elif (
            holdout.get("schema") != "pto-controlled-shadow/v1"
            or not isinstance(holdout.get("experiment"), Mapping)
            or holdout["experiment"].get("pto_params_sha256") != current_params_sha256
            or holdout["experiment"].get("rewrite_scope_sha256") != current_scope_sha256
        ):
            reasons = (*reasons, "holdout_evidence_stale")
    conformance = detail.get("output_conformance")
    if not isinstance(conformance, Mapping):
        reasons = (*reasons, "missing_output_conformance_evidence")
    else:
        conformance_report = conformance.get("report")
        if (
            not isinstance(conformance_report, Mapping)
            or conformance_report.get("qualifies_production_gate") is not True
        ):
            reasons = (*reasons, "output_conformance_not_production_qualified")
        elif (
            conformance.get("pto_params_sha256") != current_params_sha256
            or conformance.get("rewrite_scope_sha256") != current_scope_sha256
        ):
            reasons = (*reasons, "output_conformance_evidence_stale")
    safety = detail.get("production_safety")
    if not isinstance(safety, Mapping):
        reasons = (*reasons, "missing_production_safety_evidence")
    else:
        safety_report = safety.get("report")
        if (
            not isinstance(safety_report, Mapping)
            or safety_report.get("qualifies_for_canary") is not True
        ):
            reasons = (*reasons, "production_safety_not_qualified")
        elif not is_production_safety_evidence_current(session, safety):
            reasons = (*reasons, "production_safety_evidence_stale")
    return DifferentialOracleReport(
        recommendation_id=recommendation.id,
        status="equivalent" if equivalent else "not_equivalent",
        equivalent=equivalent,
        canary_eligible=equivalent and not reasons,
        trino=trino,
        spark=spark,
        scope=scope,
        reasons=reasons,
    )


def record_layout_differential(
    session: Session,
    table_id: uuid.UUID,
    *,
    actor: str,
) -> DifferentialOracleReport:
    report = evaluate_layout_recommendation(
        session,
        table_id,
        require_recorded_evidence=False,
    )
    if report.recommendation_id is None:
        return report

    recommendation = session.get(LayoutRecommendation, report.recommendation_id)
    if recommendation is None:
        raise ValueError(f"Recommendation not found: {report.recommendation_id}")
    detail = dict(recommendation.detail or {})
    params = detail.get("pto_params")
    evidence = {
        "schema": "layout-differential/v2",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "pto_params_sha256": _payload_sha256(params),
        "rewrite_scope_sha256": _payload_sha256(detail.get("rewrite_scope")),
        "report": _report_payload(report),
    }
    detail["differential_oracle"] = evidence
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="layout_differential_recorded",
            status="OK" if report.equivalent else "BLOCKED",
            engine="differential_oracle",
            detail=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            actor=actor,
        )
    )
    session.commit()
    return report


def _semantics(
    engine: str,
    strategy: str,
    target_mb: int,
    columns: tuple[str, ...],
    limitations: list[str],
    sql: str,
) -> EngineRewriteSemantics:
    effective_columns = columns if strategy in {"sort", "zorder"} else ()
    return EngineRewriteSemantics(
        engine=engine,
        supported=not limitations,
        strategy=strategy,
        target_file_size_mb=target_mb,
        clustering_columns=effective_columns,
        rendered_sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
        limitations=tuple(limitations),
    )


def _report_payload(report: DifferentialOracleReport) -> dict[str, object]:
    return {
        "recommendation_id": str(report.recommendation_id)
        if report.recommendation_id is not None
        else None,
        "status": report.status,
        "equivalent": report.equivalent,
        "canary_eligible": report.canary_eligible,
        "trino": _semantics_payload(report.trino),
        "spark": _semantics_payload(report.spark),
        "scope": _scope_payload(report.scope),
        "reasons": list(report.reasons),
    }


def _semantics_payload(semantics: EngineRewriteSemantics | None) -> dict[str, object] | None:
    if semantics is None:
        return None
    return {
        "engine": semantics.engine,
        "supported": semantics.supported,
        "strategy": semantics.strategy,
        "target_file_size_mb": semantics.target_file_size_mb,
        "clustering_columns": list(semantics.clustering_columns),
        "rendered_sql_sha256": semantics.rendered_sql_sha256,
        "limitations": list(semantics.limitations),
    }


def _scope_payload(scope: RewriteScopeSemantics | None) -> dict[str, object] | None:
    if scope is None:
        return None
    return {
        "bounded": scope.bounded,
        "equivalent": scope.equivalent,
        "estimated_bytes": scope.estimated_bytes,
        "file_count": scope.file_count,
        "predicate_count": scope.predicate_count,
        "normalized_predicates_sha256": scope.normalized_predicates_sha256,
        "limitations": list(scope.limitations),
    }


def _parse_rewrite_scope(
    value: object,
) -> tuple[tuple[PartitionPredicate, ...], RewriteScopeSemantics]:
    if not isinstance(value, Mapping):
        return (), _unsupported_scope("missing_bounded_rewrite_scope")
    if value.get("schema") != "partition-scope/v1":
        return (), _unsupported_scope("unsupported_rewrite_scope_schema")
    estimated_bytes = value.get("estimated_bytes")
    file_count = value.get("file_count")
    raw_predicates = value.get("predicates")
    if (
        not isinstance(estimated_bytes, int)
        or isinstance(estimated_bytes, bool)
        or estimated_bytes <= 0
        or not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count <= 0
        or not isinstance(raw_predicates, list)
        or not raw_predicates
    ):
        return (), _unsupported_scope("invalid_bounded_rewrite_scope")
    limitations: list[str] = []
    if estimated_bytes > MAX_CANARY_SCOPE_BYTES:
        limitations.append("rewrite_scope_exceeds_byte_limit")
    if file_count > MAX_CANARY_SCOPE_FILES:
        limitations.append("rewrite_scope_exceeds_file_limit")
    predicates = _parse_predicates(raw_predicates)
    if predicates is None:
        limitations.append("invalid_rewrite_scope_predicates")
        return (), RewriteScopeSemantics(
            bounded=False,
            equivalent=False,
            estimated_bytes=estimated_bytes,
            file_count=file_count,
            predicate_count=0,
            normalized_predicates_sha256=None,
            limitations=tuple(limitations),
        )
    trino_rendered = tuple(render_trino_predicate(predicate) for predicate in predicates)
    spark_rendered = tuple(render_spark_predicate(predicate) for predicate in predicates)
    equivalent = trino_rendered == spark_rendered and not limitations
    if trino_rendered != spark_rendered:
        limitations.append("rewrite_scope_predicates_differ")
    normalized = json.dumps(trino_rendered, separators=(",", ":"))
    return predicates, RewriteScopeSemantics(
        bounded=not limitations,
        equivalent=equivalent,
        estimated_bytes=estimated_bytes,
        file_count=file_count,
        predicate_count=len(predicates),
        normalized_predicates_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
        limitations=tuple(limitations),
    )


def _parse_predicates(value: list[object]) -> tuple[PartitionPredicate, ...] | None:
    predicates: list[PartitionPredicate] = []
    for raw_predicate in value:
        if not isinstance(raw_predicate, Mapping):
            return None
        raw_terms = raw_predicate.get("terms")
        if not isinstance(raw_terms, list) or not raw_terms:
            return None
        terms: list[PartitionPredicateTerm] = []
        for raw_term in raw_terms:
            if not isinstance(raw_term, Mapping):
                return None
            base_column = raw_term.get("base_column")
            transform = raw_term.get("transform")
            col_type = raw_term.get("col_type")
            if (
                not isinstance(base_column, str)
                or not base_column
                or not isinstance(transform, str)
                or not isinstance(col_type, str)
            ):
                return None
            try:
                parsed_transform = PartitionTransform(transform)
            except ValueError:
                return None
            if parsed_transform not in {
                PartitionTransform.IDENTITY,
                PartitionTransform.DAY,
                PartitionTransform.MONTH,
                PartitionTransform.YEAR,
            }:
                return None
            term_value = raw_term.get("value")
            if term_value is None and parsed_transform is not PartitionTransform.IDENTITY:
                return None
            terms.append(
                PartitionPredicateTerm(
                    base_column=base_column,
                    transform=parsed_transform,
                    value=term_value,
                    col_type=col_type,
                )
            )
        predicates.append(PartitionPredicate(terms=tuple(terms)))
    return tuple(predicates)


def _unsupported_scope(reason: str) -> RewriteScopeSemantics:
    return RewriteScopeSemantics(
        bounded=False,
        equivalent=False,
        estimated_bytes=None,
        file_count=None,
        predicate_count=0,
        normalized_predicates_sha256=None,
        limitations=(reason,),
    )


def _payload_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _abstain(recommendation_id: uuid.UUID, reason: str) -> DifferentialOracleReport:
    return DifferentialOracleReport(
        recommendation_id=recommendation_id,
        status="abstained",
        equivalent=False,
        canary_eligible=False,
        trino=None,
        spark=None,
        scope=None,
        reasons=(reason,),
    )
