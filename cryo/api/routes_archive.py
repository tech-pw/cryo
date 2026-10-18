"""Archive: per-partition advice, a cost preview, and archive/restore between instant classes.

Archive and restore copy a partition's live data files onto their own keys with a new storage
class (``cryo.tiering.executor``). Only instant-access classes are reachable, so queries keep
working; see docs/ARCHIVE_TIERING_RESEARCH_2026-10-01.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.security import Principal
from cryo.cost.s3_pricing import S3StorageClass, pricing_for_region
from cryo.db.models import (
    ArchiveJob,
    PartitionAccessDaily,
    PartitionLastAccess,
    PartitionStorageClass,
    S3BucketLogConfig,
    S3RequestCostDaily,
    Source,
    Table,
    TablePartition,
    TierPolicyRecord,
)
from cryo.db.tenant_scope import get_scoped_resource
from cryo.maintenance.writes import maintenance_writes_enabled
from cryo.tiering import archive_jobs
from cryo.tiering.archive_advice import (
    GIR_MIN_DURATION_DAYS,
    GIR_RETRIEVAL_PER_GB,
    SIA_MIN_DURATION_DAYS,
    SIA_RETRIEVAL_PER_GB,
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
from cryo.tiering.executor import (
    INSTANT_TARGETS,
    TARGET_LABELS,
    BucketGuardrail,
    PermissionCheck,
    bucket_guardrails,
    check_permissions,
    iam_policy,
    parse_s3_uri,
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
    archived_partitions: int = 0
    archived_gb: float = 0.0


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
    current_class: str | None = None
    moved_at: datetime | None = None


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


def _moved(session: Session, table_id: uuid.UUID) -> dict[str, PartitionStorageClass]:
    return {
        row.partition_key: row
        for row in session.scalars(
            select(PartitionStorageClass).where(PartitionStorageClass.table_id == table_id)
        )
    }


def _summary_read(
    table_id: uuid.UUID,
    summary: ArchiveSummary,
    moved: dict[str, PartitionStorageClass] | None = None,
) -> ArchiveSummaryRead:
    moved = moved or {}
    by_tier: dict[str, float] = {}
    for row in moved.values():
        by_tier[row.storage_class] = by_tier.get(row.storage_class, 0.0) + row.bytes / 1024**3
    return ArchiveSummaryRead(
        table_id=table_id,
        executes=True,
        archived_partitions=len(moved),
        archived_gb=round(sum(by_tier.values()), 3),
        mode="manual",
        policy_name=summary.policy_name,
        policy_scope=summary.policy_scope,
        target_class=summary.target_class if summary.read_evidence == "trusted" else None,
        target_label=summary.target_label if summary.read_evidence == "trusted" else "Auto",
        read_evidence=summary.read_evidence,
        read_evidence_copy=summary.read_evidence_copy,
        bytes_by_tier={k: round(v, 3) for k, v in by_tier.items()} or None,
        projected_savings_usd_month=summary.projected_savings_usd_month,
        realized_savings_usd_month=None,
        partition_count=summary.partition_count,
        eligible_count=summary.eligible_count,
        truncated=summary.truncated,
    )


def _partition_read(
    item: PartitionAdvice, moved: PartitionStorageClass | None = None
) -> ArchivePartitionRead:
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
        current_class=moved.storage_class if moved is not None else None,
        moved_at=moved.updated_at if moved is not None else None,
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
    return _summary_read(table_id, summary, _moved(session, table_id))


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
    state: Literal["all", "standard", "archived"] = "all",
) -> ArchivePartitionPage:
    _table, _policy, advices, _summary, truncated = _advice_for_table(session, table_id)
    moved = _moved(session, table_id)
    if state == "archived":
        advices = [item for item in advices if item.key in moved]
    elif state == "standard":
        advices = [item for item in advices if item.key not in moved]
    page = advices[offset : offset + limit]
    return ArchivePartitionPage(
        items=[_partition_read(item, moved.get(item.key)) for item in page],
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


_MAX_SELECTED = 500
# "auto" is archivable too: with no read evidence it is the Intelligent-Tiering-only case.
ARCHIVABLE_SUGGESTIONS = frozenset({"auto", "cool", "archive_instant"})
_MIN_DURATION_DAYS = {"STANDARD_IA": SIA_MIN_DURATION_DAYS, "GLACIER_IR": GIR_MIN_DURATION_DAYS}
_RETRIEVAL_PER_GB = {"STANDARD_IA": SIA_RETRIEVAL_PER_GB, "GLACIER_IR": GIR_RETRIEVAL_PER_GB}
OperatorPrincipalDep = Annotated[Principal, Depends(require_operator)]


class ArchiveSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["archive", "restore"]
    target_class: str | None = Field(default=None, max_length=32)
    partition_keys: list[str] = Field(default_factory=list, max_length=_MAX_SELECTED)
    all_eligible: bool = False


class ArchiveApplyRequest(ArchiveSelection):
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool = False


class PermissionRead(BaseModel):
    action: str
    status: str
    purpose: str


class BucketGuardrailRead(BaseModel):
    name: str
    status: str
    detail: str


class ArchivePreflightRead(BaseModel):
    action: str
    target_class: str
    target_label: str
    bucket: str | None
    partitions_selected: int
    object_estimate: int
    gb_estimate: float
    request_cost_usd: float
    storage_change_usd_month: float
    retrieval_cost_usd: float
    early_delete_usd: float
    permissions: list[PermissionRead]
    guardrails: list[BucketGuardrailRead]
    missing_permissions: list[str]
    iam_policy: str | None
    warnings: list[str]
    blockers: list[str]
    requires_confirmation: bool
    can_apply: bool


class ArchiveJobRead(BaseModel):
    id: uuid.UUID
    action: str
    target_class: str
    target_label: str
    partitions: int
    status: str
    stale: bool
    objects_total: int
    objects_moved: int
    objects_skipped: int
    objects_failed: int
    gb_moved: float
    errors: list[str]
    actor: str
    reason: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ArchiveJobList(BaseModel):
    items: list[ArchiveJobRead]


def _job_read(job: ArchiveJob) -> ArchiveJobRead:
    return ArchiveJobRead(
        id=job.id,
        action=job.action,
        target_class=job.target_class,
        target_label=TARGET_LABELS.get(job.target_class, job.target_class),
        partitions=len(job.partition_keys or []),
        status=job.status,
        stale=archive_jobs.is_stale(job),
        objects_total=job.objects_total,
        objects_moved=job.objects_moved,
        objects_skipped=job.objects_skipped,
        objects_failed=job.objects_failed,
        gb_moved=round(job.bytes_moved / 1024**3, 3),
        errors=list(job.errors or []),
        actor=job.actor,
        reason=job.reason,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _source(session: Session, table: Table) -> Source:
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=409, detail="Table has no source connection")
    return source


def _target(selection: ArchiveSelection, summary: ArchiveSummary) -> str:
    if selection.action == "restore":
        return "STANDARD"
    target = selection.target_class or (
        summary.target_class if summary.read_evidence == "trusted" else None
    )
    target = (target or "INTELLIGENT_TIERING").upper()
    if target not in INSTANT_TARGETS or target == "STANDARD":
        raise HTTPException(
            status_code=422,
            detail="Archive targets are INTELLIGENT_TIERING, STANDARD_IA or GLACIER_IR. Glacier "
            "Flexible and Deep Archive make Iceberg reads fail until each file is restored.",
        )
    return target


def _selected_rows(
    session: Session,
    table: Table,
    selection: ArchiveSelection,
    advices: list[PartitionAdvice],
    moved: dict[str, PartitionStorageClass],
) -> list[TablePartition]:
    if selection.action == "restore":
        keys = set(selection.partition_keys) & set(moved) if selection.partition_keys else set()
        if selection.all_eligible:
            keys = set(moved)
    else:
        keys = set(selection.partition_keys)
        if selection.all_eligible:
            keys |= {
                item.key
                for item in advices
                if item.suggestion in ARCHIVABLE_SUGGESTIONS and item.key not in moved
            }
    if not keys:
        return []
    latest = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table.id)
    )
    if latest is None:
        return []
    rows = session.scalars(
        select(TablePartition).where(
            TablePartition.table_id == table.id,
            TablePartition.scan_at == latest,
            TablePartition.partition_key.in_(sorted(keys)),
        )
    ).all()
    return list(rows)[:_MAX_SELECTED]


def _sample(rows: list[TablePartition]) -> Any:
    for row in rows:
        raw = row.raw if isinstance(row.raw, dict) else {}
        path = raw.get("sample_file_path")
        if isinstance(path, str):
            ref = parse_s3_uri(path)
            if ref is not None:
                return ref
    return None


def _preflight(
    session: Session,
    table: Table,
    selection: ArchiveSelection,
    *,
    client_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[ArchivePreflightRead, list[str]]:
    client_factory = client_factory or archive_jobs.s3_client_for_source
    _table, _policy, advices, summary, _truncated = _advice_for_table(session, table.id)
    moved = _moved(session, table.id)
    target = _target(selection, summary)
    rows = _selected_rows(session, table, selection, advices, moved)
    keys = [row.partition_key or row.partition_path or "" for row in rows]
    source = _source(session, table)
    config = dict(source.config or {})
    prices = pricing_for_region(config.get("s3.region"))
    gb = sum(float(row.partition_size_gb or 0) for row in rows)
    objects = sum(int(row.files_per_partition or 0) for row in rows)
    standard = prices[S3StorageClass.STANDARD].storage_cost_per_gb_month
    target_model = prices.get(S3StorageClass(target))
    target_price = target_model.storage_cost_per_gb_month if target_model else standard
    put_rate = target_model.put_request_cost_per_1k if target_model else 0.005
    warnings: list[str] = []
    blockers: list[str] = []
    retrieval = 0.0
    early = 0.0
    if selection.action == "restore":
        now = datetime.now(UTC)
        change = 0.0
        for row in rows:
            state = moved.get(row.partition_key or "")
            if state is None:
                continue
            row_gb = state.bytes / 1024**3
            current = prices.get(S3StorageClass(state.storage_class))
            change += row_gb * (standard - (current.storage_cost_per_gb_month if current else 0))
            retrieval += row_gb * _RETRIEVAL_PER_GB.get(state.storage_class, 0.0)
            min_days = _MIN_DURATION_DAYS.get(state.storage_class)
            moved_at = state.updated_at
            if moved_at.tzinfo is None:
                moved_at = moved_at.replace(tzinfo=UTC)
            if min_days and current is not None:
                remaining = max(min_days - (now - moved_at).days, 0)
                early += row_gb * current.storage_cost_per_gb_month * remaining / 30
    else:
        change = gb * (target_price - standard)
        min_days = _MIN_DURATION_DAYS.get(target)
        if min_days:
            warnings.append(
                f"{TARGET_LABELS[target]} bills at least {min_days} days per file. Restoring or "
                "deleting sooner (compaction, snapshot expiry) is charged for the rest."
            )
        if target in _RETRIEVAL_PER_GB:
            warnings.append(
                f"Reads cost ${_RETRIEVAL_PER_GB[target]:.2f} per GB. Archive partitions nobody "
                "queries; Cryo's compaction skips archived partitions."
            )
    if not rows:
        blockers.append("No partitions selected.")
    if selection.action == "archive":
        by_key = {item.key: item for item in advices}
        held = [
            key
            for key in keys
            if key in by_key and by_key[key].suggestion not in ARCHIVABLE_SUGGESTIONS
        ]
        if held:
            blockers.append(
                f"{len(held)} selected partition(s) are still written, read recently, excluded, "
                "or need compaction first, so they stay in Standard."
            )
    requires_confirmation = False
    if selection.action == "archive" and target in _RETRIEVAL_PER_GB:
        requires_confirmation = summary.read_evidence != "trusted"
        if requires_confirmation:
            warnings.append(
                "Cryo has no trustworthy read evidence for this table, so it can't prove these "
                "partitions are unread. Intelligent-Tiering has no retrieval fee and is the safe "
                "choice."
            )
    bucket = None
    sample = _sample(rows)
    if sample is not None:
        bucket = sample.bucket
    elif table.location:
        ref = parse_s3_uri(table.location.rstrip("/") + "/x")
        bucket = ref.bucket if ref is not None else None
    permissions: list[PermissionCheck] = []
    guardrails: list[BucketGuardrail] = []
    if bucket is None:
        blockers.append("The table's location is not an S3 path.")
    else:
        try:
            client = client_factory(config)
            permissions = check_permissions(client, sample)
            guardrails, bucket_checks = bucket_guardrails(client, bucket)
            permissions.extend(bucket_checks)
        except Exception as exc:
            blockers.append(f"Couldn't reach S3 with this source's credentials: {exc}"[:300])
    missing = [check.action for check in permissions if check.status == "missing"]
    for guard in guardrails:
        if guard.status == "block":
            blockers.append(guard.detail)
        elif guard.status == "warn":
            warnings.append(guard.detail)
        elif guard.status == "unknown" and guard.name in {"versioning", "object_lock"}:
            blockers.append(guard.detail + " It must be readable to archive safely.")
    if sample is None and bucket is not None:
        warnings.append(
            "No sample file was recorded for these partitions, so write access is checked when "
            "the job starts. Rescan the table to check it now."
        )
    read = ArchivePreflightRead(
        action=selection.action,
        target_class=target,
        target_label=TARGET_LABELS.get(target, target),
        bucket=bucket,
        partitions_selected=len(rows),
        object_estimate=objects,
        gb_estimate=round(gb, 3),
        request_cost_usd=round(objects / 1000 * put_rate, 4),
        storage_change_usd_month=round(change, 2),
        retrieval_cost_usd=round(retrieval, 2),
        early_delete_usd=round(early, 2),
        permissions=[PermissionRead(**check.__dict__) for check in permissions],
        guardrails=[BucketGuardrailRead(**guard.__dict__) for guard in guardrails],
        missing_permissions=missing,
        iam_policy=iam_policy(bucket) if bucket and (missing or not permissions) else None,
        warnings=warnings,
        blockers=blockers,
        requires_confirmation=requires_confirmation,
        can_apply=not blockers and not missing,
    )
    return read, keys


@router.post("/tables/{table_id}/tiering/preflight", response_model=ArchivePreflightRead)
def post_archive_preflight(
    table_id: uuid.UUID,
    payload: ArchiveSelection,
    session: SessionDep,
    _principal: OperatorPrincipalDep,
) -> ArchivePreflightRead:
    """Cost, permissions and bucket guardrails for an archive or restore, without moving data.

    Probes S3 with the source's credentials: a HEAD on a sample file, a self-copy that S3
    rejects whether or not it is allowed (only the error code differs), and bucket reads.
    """
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    read, _keys = _preflight(session, table, payload)
    return read


@router.post("/tables/{table_id}/tiering/apply", response_model=ArchiveJobRead, status_code=202)
def post_archive_apply(
    table_id: uuid.UUID,
    payload: ArchiveApplyRequest,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> ArchiveJobRead:
    """Start an archive or restore job. Audited; refuses while another job runs on the table."""
    if not maintenance_writes_enabled():
        raise HTTPException(status_code=403, detail="This install can scan and report only.")
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    running = archive_jobs.active_job(session, table.id)
    if running is not None:
        raise HTTPException(status_code=409, detail="An archive job is already running here.")
    read, keys = _preflight(session, table, payload)
    if not read.can_apply:
        blockers = list(read.blockers)
        if read.missing_permissions:
            blockers.append(
                "The source's S3 credentials are missing permissions: "
                + ", ".join(read.missing_permissions)
                + "."
            )
        raise HTTPException(
            status_code=422, detail={"blockers": blockers, "preflight": read.model_dump()}
        )
    if read.requires_confirmation and not payload.confirm:
        raise HTTPException(
            status_code=409,
            detail={"requires_confirmation": True, "warnings": read.warnings},
        )
    job = ArchiveJob(
        table_id=table.id,
        action=payload.action,
        target_class=read.target_class,
        partition_keys=keys,
        status="queued",
        actor=principal.subject,
        reason=payload.reason.strip(),
        errors=[],
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    archive_jobs.submit(job.id, _session_factory(session))
    return _job_read(job)


def _session_factory(session: Session) -> Callable[[], Session]:
    bind = session.get_bind()
    return lambda: Session(bind=bind, autoflush=False, expire_on_commit=False)


@router.get(
    "/tables/{table_id}/tiering/jobs",
    response_model=ArchiveJobList,
    dependencies=[Depends(require_viewer)],
)
def get_archive_jobs(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ArchiveJobList:
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    jobs = session.scalars(
        select(ArchiveJob)
        .where(ArchiveJob.table_id == table.id)
        .order_by(ArchiveJob.created_at.desc())
        .limit(limit)
    ).all()
    return ArchiveJobList(items=[_job_read(job) for job in jobs])


__all__ = ["router"]
