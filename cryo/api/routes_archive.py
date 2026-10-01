"""Advisory archive reads. Dry-run plan only; no object-store writes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import (
    PartitionAccessDaily,
    PartitionLastAccess,
    S3BucketLogConfig,
    S3RequestCostDaily,
    Table,
    TablePartition,
    TierPolicyRecord,
)
from cryo.db.tenant_scope import get_scoped_resource
from cryo.tiering.archive_advice import (
    ArchivePlan,
    ArchivePolicy,
    ArchiveSummary,
    EvidenceWindow,
    PartitionAdvice,
    PartitionFacts,
    PartitionReads,
    TableContext,
    dry_run_plan,
    evaluate_partition,
    index_access,
    match_partition_reads,
    policy_from_record,
    resolve_policy,
    summarize,
)

router = APIRouter(prefix="/api/v1", tags=["archive"])

_MAX_PARTITIONS = 5000


class ArchiveSummaryRead(BaseModel):
    table_id: uuid.UUID
    dry_run: bool = True
    executes: bool = False
    mode: str
    policy_name: str | None
    policy_scope: str | None
    target_class: str | None
    target_label: str | None
    read_evidence: str
    read_evidence_copy: str | None
    bytes_by_tier: dict[str, float] | None = None
    projected_savings_usd_month: float | None
    realized_savings_usd_month: float | None
    partition_count: int
    eligible_count: int
    truncated: bool


class ArchivePartitionRead(BaseModel):
    partition_key: str
    partition_value: str | None
    size_gb: float | None
    age_days: int | None
    suggestion: str
    suggestion_label: str
    storage_class: str | None
    eligible: bool
    reason_codes: list[str]
    evidence_copy: str | None
    net_saving_usd_month: float | None
    break_even_read_share: float | None


class ArchivePartitionPage(BaseModel):
    items: list[ArchivePartitionRead]
    total: int
    limit: int
    offset: int
    truncated: bool


class GuardrailRead(BaseModel):
    name: str
    status: str


class ArchivePlanRead(BaseModel):
    dry_run: bool = True
    applied: bool = False
    writes: bool = False
    executes: bool = False
    target_class: str | None
    target_label: str | None
    object_count: int | None
    byte_count: int | None
    request_cost_usd: float | None
    early_delete_exposure_usd: float | None
    guardrails: list[GuardrailRead]
    partitions_considered: int
    partitions_selected: int


class ArchivePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_age_days: int | None = Field(default=None, ge=0)
    target_class: str | None = Field(default=None, max_length=64)
    partition_prefix: str | None = Field(default=None, max_length=512)
    max_bytes: int | None = Field(default=None, ge=0)


# Worst observed S3 server-access-log delivery delay (Yelp: 9 days); the evidence gate needs the
# window to be at least twice this.
_ASSUMED_LOG_DELAY_DAYS = 9
_MAX_INGEST_STALENESS_DAYS = 3


def _bucket(location: str | None) -> str | None:
    if not location:
        return None
    scheme, sep, rest = location.partition("://")
    if not sep or scheme not in {"s3", "s3a", "s3n"}:
        return None
    bucket, _, _ = rest.partition("/")
    return bucket or None


def _ingestion_healthy(config: S3BucketLogConfig | None) -> bool | None:
    if config is None:
        return None
    if not config.enabled or config.last_error or config.last_ingested_at is None:
        return False
    # A stalled ingest leaves old "healthy" rows behind; require a recent success.
    return (datetime.now(UTC) - config.last_ingested_at).days <= _MAX_INGEST_STALENESS_DAYS


def _facts(row: TablePartition) -> PartitionFacts:
    raw = row.raw if isinstance(row.raw, dict) else {}
    return PartitionFacts(
        key=row.partition_key or row.partition_path or row.partition_value or str(row.id),
        partition_value=row.partition_value,
        partition_path=row.partition_path,
        partition_key=row.partition_key,
        transform=row.transform,
        partition_values=row.partition_values if isinstance(row.partition_values, dict) else {},
        base_column=row.base_column,
        size_gb=row.partition_size_gb,
        file_count=row.files_per_partition,
        small_files=row.small_files_in_partition_count,
        last_write_at=raw.get("last_write_at"),
        pin_hot=bool(raw.get("pin_hot")),
        excluded=bool(raw.get("excluded")),
    )


def _context(table: Table) -> TableContext:
    raw = table.stats.raw if table.stats is not None and isinstance(table.stats.raw, dict) else {}
    tags = raw.get("tags")
    return TableContext(
        schema_name=table.schema_name,
        table_name=table.table_name,
        tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
        pin_hot=bool(raw.get("pin_hot")),
    )


def _advice_for_table(
    session: SessionDep,
    table_id: uuid.UUID,
) -> tuple[Table, ArchivePolicy | None, list[PartitionAdvice], ArchiveSummary, bool]:
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    policies = [
        policy_from_record(
            name=row.name,
            storage_class=row.storage_class,
            min_age_days=row.min_age_days,
            predicate=row.predicate,
            enabled=row.enabled,
        )
        for row in session.scalars(
            select(TierPolicyRecord).where(TierPolicyRecord.source_id == table.source_id)
        )
    ]
    policy = resolve_policy(policies, schema_name=table.schema_name, table_name=table.table_name)
    latest = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table.id)
    )
    rows: list[TablePartition] = []
    truncated = False
    if latest is not None:
        loaded = list(
            session.scalars(
                select(TablePartition)
                .where(TablePartition.table_id == table.id, TablePartition.scan_at == latest)
                .order_by(
                    TablePartition.partition_value.asc().nulls_last(),
                    TablePartition.id.asc(),
                )
                .limit(_MAX_PARTITIONS + 1)
            )
        )
        truncated = len(loaded) > _MAX_PARTITIONS
        rows = loaded[:_MAX_PARTITIONS]
    access = session.execute(
        select(
            PartitionAccessDaily.partition_path,
            func.coalesce(func.sum(PartitionAccessDaily.request_count), 0),
            func.max(PartitionAccessDaily.day),
            func.min(PartitionAccessDaily.day),
        )
        .where(PartitionAccessDaily.table_id == table.id)
        .group_by(PartitionAccessDaily.partition_path)
    ).all()
    durable = {
        path: (first_day, last_day)
        for path, first_day, last_day in session.execute(
            select(
                PartitionLastAccess.partition_path,
                PartitionLastAccess.first_read_day,
                PartitionLastAccess.last_read_day,
            ).where(PartitionLastAccess.table_id == table.id)
        ).all()
    }
    recent = {path: (count, last_day) for path, count, last_day, _first in access}
    paths = set(durable) | set(recent)
    reads = index_access(
        [
            (
                path,
                PartitionReads(
                    # A durable-only row means a read happened before the 35-day window.
                    reads=max(int(recent.get(path, (0, None))[0]), 1 if path in durable else 0),
                    bytes_read=None,
                    last_read_at=datetime.combine(
                        max(
                            day
                            for day in (
                                recent.get(path, (0, None))[1],
                                durable.get(path, (None, None))[1],
                            )
                            if day is not None
                        ),
                        time.min,
                        tzinfo=UTC,
                    ),
                    observed=True,
                ),
            )
            for path in paths
        ]
    )
    bucket = _bucket(table.location)
    config = None
    if bucket:
        config = session.scalar(
            select(S3BucketLogConfig).where(
                S3BucketLogConfig.source_id == table.source_id,
                S3BucketLogConfig.bucket == bucket,
            )
        )
    window_days = None
    if bucket:
        first_day = session.scalar(
            select(func.min(S3RequestCostDaily.day)).where(
                S3RequestCostDaily.source_id == table.source_id,
                S3RequestCostDaily.bucket == bucket,
            )
        )
        if first_day is not None and config is not None and config.last_ingested_date:
            window_days = (config.last_ingested_date - first_day).days + 1
    evidence = EvidenceWindow(
        ingestion_healthy=_ingestion_healthy(config),
        window_days=window_days,
        log_delay_days=_ASSUMED_LOG_DELAY_DAYS,
    )
    context = _context(table)
    now = datetime.now(UTC)
    facts = [_facts(row) for row in rows]
    advices = [
        evaluate_partition(
            item,
            policy,
            evidence,
            match_partition_reads(item, reads),
            context,
            now=now,
        )
        for item in facts
    ]
    summary = summarize(advices, policy, evidence, truncated=truncated)
    return table, policy, advices, summary, truncated


def _summary_read(table_id: uuid.UUID, summary: ArchiveSummary) -> ArchiveSummaryRead:
    return ArchiveSummaryRead(
        table_id=table_id,
        mode="advisory",
        policy_name=summary.policy_name,
        policy_scope=summary.policy_scope,
        target_class=summary.target_class if summary.read_evidence == "trusted" else None,
        target_label=summary.target_label if summary.read_evidence == "trusted" else "Auto",
        read_evidence=summary.read_evidence,
        read_evidence_copy=summary.read_evidence_copy,
        bytes_by_tier=None,
        projected_savings_usd_month=summary.projected_savings_usd_month,
        realized_savings_usd_month=None,
        partition_count=summary.partition_count,
        eligible_count=summary.eligible_count,
        truncated=summary.truncated,
    )


def _partition_read(item: PartitionAdvice) -> ArchivePartitionRead:
    missing = "no_read_evidence" in item.reason_codes
    blocked = item.suggestion == "blocked"
    suggestion = "blocked" if blocked else "auto" if missing else item.suggestion
    label = "Blocked" if blocked else "Auto" if missing else item.suggestion_label
    storage_class = None if blocked else "INTELLIGENT_TIERING" if missing else item.storage_class
    if storage_class in {"GLACIER", "DEEP_ARCHIVE"}:
        suggestion = "auto"
        label = "Auto"
        storage_class = "INTELLIGENT_TIERING"
    return ArchivePartitionRead(
        partition_key=item.key,
        partition_value=item.partition_value,
        size_gb=item.size_gb,
        age_days=item.age_days,
        suggestion=suggestion,
        suggestion_label=label,
        storage_class=storage_class,
        eligible=False if missing or blocked else item.eligible,
        reason_codes=list(item.reason_codes),
        evidence_copy=item.evidence_copy,
        net_saving_usd_month=None if missing or blocked else item.net_saving_usd_month,
        break_even_read_share=None if missing or blocked else item.break_even_read_share,
    )


def _plan_read(plan: ArchivePlan) -> ArchivePlanRead:
    return ArchivePlanRead(
        dry_run=True,
        applied=False,
        writes=False,
        executes=False,
        target_class=plan.target_class,
        target_label=plan.target_label,
        object_count=plan.object_count,
        byte_count=plan.byte_count,
        request_cost_usd=plan.request_cost_usd,
        early_delete_exposure_usd=plan.early_delete_exposure_usd,
        guardrails=[GuardrailRead(name=item.name, status=item.status) for item in plan.guardrails],
        partitions_considered=plan.partitions_considered,
        partitions_selected=plan.partitions_selected,
    )


@router.get(
    "/tables/{table_id}/tiering",
    response_model=ArchiveSummaryRead,
    dependencies=[Depends(require_viewer)],
)
def get_archive_summary(table_id: uuid.UUID, session: SessionDep) -> ArchiveSummaryRead:
    _table, _policy, _advices, summary, _truncated = _advice_for_table(session, table_id)
    return _summary_read(table_id, summary)


@router.get(
    "/tables/{table_id}/tiering/partitions",
    response_model=ArchivePartitionPage,
    dependencies=[Depends(require_viewer)],
)
def get_archive_partitions(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ArchivePartitionPage:
    _table, _policy, advices, _summary, truncated = _advice_for_table(session, table_id)
    page = advices[offset : offset + limit]
    return ArchivePartitionPage(
        items=[_partition_read(item) for item in page],
        total=len(advices),
        limit=limit,
        offset=offset,
        truncated=truncated,
    )


@router.post(
    "/tables/{table_id}/tiering/plan",
    response_model=ArchivePlanRead,
    dependencies=[Depends(require_viewer)],
)
def post_archive_plan(
    table_id: uuid.UUID,
    session: SessionDep,
    payload: ArchivePlanRequest,
) -> ArchivePlanRead:
    _table, policy, advices, summary, _truncated = _advice_for_table(session, table_id)
    cap = payload.max_bytes
    if cap is None and policy is not None:
        cap = policy.max_bytes_per_run
    if summary.read_evidence != "trusted":
        target = "INTELLIGENT_TIERING"
    elif payload.target_class is not None:
        target = payload.target_class
    elif policy is not None and not policy.offline_target:
        target = policy.target_class
    else:
        target = "INTELLIGENT_TIERING"
    plan = dry_run_plan(
        advices,
        target_class=target,
        min_age_days=payload.min_age_days,
        partition_prefix=payload.partition_prefix,
        max_bytes=cap,
    )
    return _plan_read(plan)


__all__ = ["router"]
