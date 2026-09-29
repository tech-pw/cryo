"""Quality results and lineage. Missing edges are a gap, not proof of no lineage."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    TableRefInput,
    candidate,
    clamp_limit,
    iso,
    make_envelope,
    relation_exists,
    require_table,
    scalar_count,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import QualityConstraint, QualityConstraintResult


class QualityInput(TableRefInput):
    limit: int = 50


def get_quality_results(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = QualityInput.model_validate(parsed.model_dump())
    table, error = require_table(ctx, spec, "get_quality_results")
    if error is not None or table is None:
        return error or make_envelope(
            "get_quality_results",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    if not relation_exists(ctx.session, "quality_constraints"):
        return make_envelope(
            "get_quality_results",
            {
                "summary": "quality_constraints source table does not exist.",
                "abstained": True,
                "abstain_reason": "quality_constraints source table does not exist",
                **candidate(table),
            },
            ctx=ctx,
            classification="unknown",
            error_category="unsupported",
            warnings=["Quality results were not invented."],
        )
    total = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(QualityConstraint)
        .where(QualityConstraint.table_id == table.id),
    )
    constraints = list(
        ctx.session.scalars(
            select(QualityConstraint)
            .where(QualityConstraint.table_id == table.id)
            .order_by(QualityConstraint.name)
            .limit(clamp_limit(spec.limit))
        )
    )
    results = []
    for constraint in constraints:
        latest = ctx.session.scalar(
            select(QualityConstraintResult)
            .where(QualityConstraintResult.constraint_id == constraint.id)
            .order_by(QualityConstraintResult.evaluated_at.desc())
            .limit(1)
        )
        results.append(
            {
                "constraint_id": str(constraint.id),
                "name": constraint.name,
                "kind": constraint.kind,
                "enabled": constraint.enabled,
                "status": latest.status if latest is not None else None,
                "evaluated_at": iso(latest.evaluated_at) if latest is not None else None,
                "observed": latest.observed if latest is not None else None,
                "reason": latest.reason if latest is not None else None,
                "unknown": [] if latest is not None else ["status", "evaluated_at"],
            }
        )
    classification = "observed" if total else "unknown"
    return make_envelope(
        "get_quality_results",
        {
            "summary": "Stored constraint evaluations. No constraints is unknown, not all-passed.",
            **candidate(table),
            "constraints": results,
            "total": total,
        },
        ctx=ctx,
        classification=classification,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        coverage={"total": total, "returned": len(results)},
        truncated=total > len(results),
        row_count=len(results),
        warnings=[] if total else ["No quality constraints are stored. That is not a pass."],
        measurement_method="quality_constraints",
    )


def get_lineage(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_lineage")
    if error is not None or table is None:
        return error or make_envelope(
            "get_lineage",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    if not relation_exists(ctx.session, "lineage_edges"):
        return make_envelope(
            "get_lineage",
            {
                "summary": "lineage_edges source table does not exist.",
                "abstained": True,
                "abstain_reason": "lineage_edges source table does not exist",
                **candidate(table),
                "upstream": [],
                "downstream": [],
            },
            ctx=ctx,
            classification="unknown",
            error_category="unsupported",
            warnings=["Lineage was not invented. No stored edges is not proof of no lineage."],
            unsupported_fields=["upstream", "downstream"],
        )
    try:
        from cryo.lineage.tracker import LineageEdge

        upstream_total = scalar_count(
            ctx.session,
            select(func.count())
            .select_from(LineageEdge)
            .where(LineageEdge.target_table_id == table.id),
        )
        downstream_total = scalar_count(
            ctx.session,
            select(func.count())
            .select_from(LineageEdge)
            .where(LineageEdge.source_table_id == table.id),
        )
        upstream = list(
            ctx.session.scalars(
                select(LineageEdge)
                .where(LineageEdge.target_table_id == table.id)
                .order_by(LineageEdge.last_observed_at.desc())
                .limit(50)
            )
        )
        downstream = list(
            ctx.session.scalars(
                select(LineageEdge)
                .where(LineageEdge.source_table_id == table.id)
                .order_by(LineageEdge.last_observed_at.desc())
                .limit(50)
            )
        )
    except SQLAlchemyError:
        return make_envelope(
            "get_lineage",
            {
                "summary": "lineage_edges could not be read.",
                "abstained": True,
                "abstain_reason": "lineage_edges source table does not exist",
                **candidate(table),
            },
            ctx=ctx,
            classification="unknown",
            error_category="unsupported",
            warnings=["Lineage was not invented."],
        )
    gap = upstream_total == 0 and downstream_total == 0
    return make_envelope(
        "get_lineage",
        {
            "summary": "Stored lineage edges. No edges is a coverage gap, not proof of no lineage.",
            **candidate(table),
            "upstream": [
                {
                    "source_table_id": str(edge.source_table_id),
                    "edge_type": edge.edge_type,
                    "job_name": edge.job_name,
                    "last_observed_at": iso(edge.last_observed_at),
                }
                for edge in upstream
            ],
            "downstream": [
                {
                    "target_table_id": str(edge.target_table_id),
                    "edge_type": edge.edge_type,
                    "job_name": edge.job_name,
                    "last_observed_at": iso(edge.last_observed_at),
                }
                for edge in downstream
            ],
            "upstream_total": upstream_total,
            "downstream_total": downstream_total,
            "lineage_gap": gap,
        },
        ctx=ctx,
        classification="unknown" if gap else "observed",
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        warnings=["No stored lineage edges is not proof that the table has no lineage."],
        coverage={"upstream_total": upstream_total, "downstream_total": downstream_total},
        measurement_method="lineage_edges",
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_quality_results",
        family="quality",
        description="Stored quality constraint results. No constraints is not a pass.",
        input_model=QualityInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_quality_results,
    ),
    ToolSpec(
        name="get_lineage",
        family="quality",
        description="Stored lineage edges. A gap is not proof of no lineage.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_lineage,
    ),
)
