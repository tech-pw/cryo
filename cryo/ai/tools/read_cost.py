"""Cost, reclaim impact, and query activity. Measured and modeled stay separate."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    TableRefInput,
    candidate,
    latest_stats,
    make_envelope,
    require_table,
    scalar_count,
    sql_dt,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import AuditLog, QueryLog, Table, TableCost, TableQueryActivity, TableStats
from cryo.reporting.impact_signals import _RECLAIM_ACTIONS


class ActivityInput(TableRefInput):
    days: int = 30


class ImpactInput(BaseModel):
    days: int = 7


def _table_optional(
    ctx: ToolContext, parsed: BaseModel, tool_name: str
) -> tuple[Table | None, EvidenceEnvelope | None]:
    spec = TableRefInput.model_validate(parsed.model_dump())
    if spec.table_id is None and not spec.schema_name and not spec.table_name and not spec.name:
        return None, None
    return require_table(ctx, spec, tool_name)


def get_cost_evidence(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = _table_optional(ctx, parsed, "get_cost_evidence")
    if error is not None:
        return error
    if table is None:
        measured_rows = scalar_count(
            ctx.session,
            select(func.count())
            .select_from(TableCost)
            .where(TableCost.request_cost_usd_month.is_not(None)),
        )
        cost_rows = scalar_count(ctx.session, select(func.count()).select_from(TableCost))
        measured_sum = ctx.session.scalar(
            select(func.sum(TableCost.request_cost_usd_month)).where(
                TableCost.request_cost_usd_month.is_not(None)
            )
        )
        modeled_sum = ctx.session.scalar(
            select(func.sum(TableStats.estimated_monthly_s3_cost_usd)).where(
                TableStats.estimated_monthly_s3_cost_usd.is_not(None)
            )
        )
        modeled_rows = scalar_count(
            ctx.session,
            select(func.count())
            .select_from(TableStats)
            .where(TableStats.estimated_monthly_s3_cost_usd.is_not(None)),
        )
        classification = "observed" if measured_rows else "modeled" if modeled_rows else "unknown"
        return make_envelope(
            "get_cost_evidence",
            {
                "summary": "Measured request cost and modeled estimates are separate, not summed.",
                "scope": "fleet",
                "measured": {
                    "request_cost_usd_month": measured_sum if measured_rows else None,
                    "tables_with_request_cost": measured_rows,
                    "tables_missing_request_cost": cost_rows - measured_rows if cost_rows else None,
                },
                "modeled": {
                    "estimated_monthly_s3_cost_usd": modeled_sum if modeled_rows else None,
                    "tables_with_estimate": modeled_rows,
                    "source": "table_stats_heuristic",
                },
            },
            ctx=ctx,
            classification=classification,
            units={
                "request_cost_usd_month": "USD/month",
                "estimated_monthly_s3_cost_usd": "USD/month",
            },
            warnings=[
                "Measured and modeled costs are not combined. A missing measurement is not zero."
            ],
            coverage={"measured_tables": measured_rows, "modeled_tables": modeled_rows},
            measurement_method="mixed",
        )
    cost = ctx.session.get(TableCost, table.id)
    stats = latest_stats(ctx.session, table.id)
    measured = None
    if cost is not None and (
        cost.request_cost_usd_month is not None or cost.get_requests_30d is not None
    ):
        measured = {
            "request_cost_usd_month": cost.request_cost_usd_month,
            "get_requests_30d": cost.get_requests_30d,
            "put_requests_30d": cost.put_requests_30d,
            "head_requests_30d": cost.head_requests_30d,
            "pricing_source": cost.request_cost_pricing_source,
            "precision": cost.precision,
        }
    modeled = None
    if stats is not None and (
        stats.estimated_monthly_s3_cost_usd is not None
        or stats.projected_savings_usd_month is not None
    ):
        modeled = {
            "estimated_monthly_s3_cost_usd": stats.estimated_monthly_s3_cost_usd,
            "projected_savings_usd_month": stats.projected_savings_usd_month,
            "projected_monthly_s3_cost_usd": stats.projected_monthly_s3_cost_usd,
            "source": "table_stats_heuristic",
        }
    if cost is not None and cost.precision == "heuristic" and modeled is None:
        modeled = {
            "current_monthly_cost_usd": cost.current_monthly_cost_usd,
            "projected_savings_usd_month": cost.projected_savings_usd_month,
            "precision": cost.precision,
            "source": "table_cost_heuristic",
        }
    unknown = []
    if measured is None or (measured and measured.get("request_cost_usd_month") is None):
        unknown.append("request_cost_usd_month")
    if modeled is None:
        unknown.append("estimated_monthly_s3_cost_usd")
    classification = "observed" if measured else "modeled" if modeled else "unknown"
    return make_envelope(
        "get_cost_evidence",
        {
            "summary": "Measured and modeled costs are separate and were not summed.",
            **candidate(table),
            "measured": measured,
            "modeled": modeled,
            "unknown": unknown,
        },
        ctx=ctx,
        classification=classification,
        observed_at=cost.scan_at if cost is not None else (stats.scan_at if stats else None),
        units={"request_cost_usd_month": "USD/month", "estimated_monthly_s3_cost_usd": "USD/month"},
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        warnings=["Do not add measured and modeled into one number. Null cost is not zero."],
        measurement_method="mixed",
    )


def get_maintenance_impact(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = ImpactInput.model_validate(parsed.model_dump())
    cutoff = sql_dt(ctx.session, ctx.now - timedelta(days=spec.days))
    base = (
        AuditLog.status == "OK",
        AuditLog.action.in_(_RECLAIM_ACTIONS),
        AuditLog.ts >= cutoff,
    )
    succeeded = scalar_count(ctx.session, select(func.count()).select_from(AuditLog).where(*base))
    measured_actions = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(AuditLog)
        .where(*base, AuditLog.actual_bytes_freed.is_not(None)),
    )
    unmeasured_actions = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(AuditLog)
        .where(*base, AuditLog.actual_bytes_freed.is_(None)),
    )
    byte_sum = None
    if measured_actions:
        byte_sum = ctx.session.scalar(
            select(func.sum(AuditLog.actual_bytes_freed)).where(
                *base, AuditLog.actual_bytes_freed.is_not(None)
            )
        )
    classification = "unknown" if measured_actions == 0 else "observed"
    return make_envelope(
        "get_maintenance_impact",
        {
            "summary": (
                "Bytes freed count only successful reclaim actions with a stored measurement. "
                "Unmeasured is not zero."
            ),
            "window_days": spec.days,
            "successful_reclaim_actions": succeeded,
            "actions_with_bytes_evidence": measured_actions,
            "actions_without_bytes_evidence": unmeasured_actions,
            "bytes_freed": byte_sum,
            "reclaim_actions": list(_RECLAIM_ACTIONS),
        },
        ctx=ctx,
        classification=classification,
        units={"bytes_freed": "bytes"},
        warnings=[
            "Null actual_bytes_freed is unmeasured, not zero. Only status OK reclaim actions count."
        ],
        coverage={
            "successful_reclaim_actions": succeeded,
            "measured": measured_actions,
            "unmeasured": unmeasured_actions,
        },
        measurement_method="audit_log",
    )


def get_query_activity(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = ActivityInput.model_validate(parsed.model_dump())
    table, error = _table_optional(ctx, spec, "get_query_activity")
    if error is not None:
        return error
    log_filters: list[Any] = []
    activity_filters: list[Any] = []
    identity: dict[str, Any] = {"scope": "fleet"}
    links: list[dict[str, str]] = []
    if table is not None:
        log_filters.append(QueryLog.table_id == table.id)
        activity_filters.append(TableQueryActivity.table_id == table.id)
        identity = candidate(table)
        links = [table_link(table)]
    log_count = scalar_count(
        ctx.session, select(func.count()).select_from(QueryLog).where(*log_filters)
    )
    warnings = ["No observed reads is not proof of no use."]
    if log_count == 0:
        activity_rows = scalar_count(
            ctx.session,
            select(func.count()).select_from(TableQueryActivity).where(*activity_filters),
        )
        activity_sum = ctx.session.scalar(
            select(func.sum(TableQueryActivity.query_count)).where(*activity_filters)
        )
        warnings.insert(
            0,
            "QueryLog is empty for this scope. Fell back to TableQueryActivity.",
        )
        classification = "unknown" if activity_rows == 0 else "observed"
        return make_envelope(
            "get_query_activity",
            {
                "summary": "Fell back to day-bucket activity. Absence is not proof of no use.",
                **identity,
                "source": "table_query_activity",
                "query_log_rows": 0,
                "activity_rows": activity_rows,
                "stored_query_count": activity_sum if activity_rows else None,
                "observed_use": None if activity_rows == 0 else True,
            },
            ctx=ctx,
            classification=classification,
            source_links=links,
            warnings=warnings,
            coverage={
                "query_log_rows": 0,
                "activity_rows": activity_rows,
                "source": "table_query_activity",
            },
            measurement_method="table_query_activity",
            units={"stored_query_count": "queries"},
        )
    byte_sum = ctx.session.scalar(
        select(func.sum(QueryLog.bytes_read)).where(*log_filters, QueryLog.bytes_read.is_not(None))
    )
    bytes_known = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(QueryLog)
        .where(*log_filters, QueryLog.bytes_read.is_not(None)),
    )
    return make_envelope(
        "get_query_activity",
        {
            "summary": "Observed QueryLog rows. A table missing from the log is not proven unused.",
            **identity,
            "source": "query_logs",
            "query_log_rows": log_count,
            "bytes_read": byte_sum if bytes_known else None,
            "bytes_read_known_rows": bytes_known,
            "observed_use": True,
        },
        ctx=ctx,
        source_links=links,
        warnings=warnings,
        units={"bytes_read": "bytes", "query_log_rows": "queries"},
        coverage={
            "query_log_rows": log_count,
            "bytes_read_known_rows": bytes_known,
            "source": "query_logs",
        },
        measurement_method="query_logs",
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_cost_evidence",
        family="cost",
        description="Measured request cost and modeled estimates, kept separate.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_cost_evidence,
    ),
    ToolSpec(
        name="get_maintenance_impact",
        family="cost",
        description="Bytes freed from successful reclaim audits. Unmeasured is not zero.",
        input_model=ImpactInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_maintenance_impact,
    ),
    ToolSpec(
        name="get_query_activity",
        family="cost",
        description="QueryLog, else TableQueryActivity. No reads is not proof of no use.",
        input_model=ActivityInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_query_activity,
    ),
)
