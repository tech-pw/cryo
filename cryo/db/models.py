from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from cryo.db.base import Base

_sa_text = text

JsonDict = dict[str, Any]
JsonList = list[JsonDict]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    admin_user_id: Mapped[str] = mapped_column(Text, nullable=False)


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Empty list = all namespaces in the catalog are in scope for scan + maintenance.
    maintenance_schema_names: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    tables: Mapped[list[Table]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    tier_policies: Mapped[list[TierPolicyRecord]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    aws_integration: Mapped[AWSIntegration | None] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    aws_cost_insight_config: Mapped[AWSCostInsightConfig | None] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )


class Destination(TimestampMixin, Base):
    __tablename__ = "destinations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    offpeak_start: Mapped[int | None] = mapped_column(Integer)
    offpeak_end: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Per-destination query-log ingestion config. Optional block -- if absent/disabled, the curator
    # never writes raw query rows for this destination, so the metadata DB never sees the
    # per-query row churn that 1M-queries/day engines would otherwise produce. The same config
    # also drives retention on the destination's Iceberg query-log table (snapshot expiry).
    query_log_config: Mapped[JsonDict | None] = mapped_column(JSONB)
    # Days to keep raw query rows in the Iceberg archive. NULL = no expiry (used for the
    # read-through mode that points at the user's own external Iceberg table -- retention then
    # belongs to them, not us). Defaults to 30 on first write so a freshly-enabled destination
    # can't accidentally grow the archive without bound.
    query_log_retention_days: Mapped[int | None] = mapped_column(Integer)
    # Monotonically-advancing high-water mark of executed_at we've successfully archived, per
    # destination. Lets the curator recover from crashes without re-archiving rows it already
    # wrote to Iceberg, and avoids the duplicate-row problem if a fetch retried after a transient
    # Trino error.
    query_log_watermark: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    presets: Mapped[list[Preset]] = relationship(back_populates="destination")
    jobs: Mapped[list[MaintenanceJob]] = relationship(back_populates="destination")


class AIProvider(TimestampMixin, Base):
    """Configuration for AI/LLM providers (Anthropic, OpenAI, etc.)."""

    __tablename__ = "ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(Text, nullable=False)  # anthropic, openai, etc.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)  # encrypted API key
    model: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # claude-sonnet-4-20250514, gpt-4o, etc.
    config: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class SlackConfig(TimestampMixin, Base):
    """Configuration for Slack integration (webhooks, channels, schedules)."""

    __tablename__ = "slack_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    webhook_url_encrypted: Mapped[str | None] = mapped_column(Text)  # encrypted webhook URL
    channel: Mapped[str | None] = mapped_column(Text)  # override channel (optional)
    daily_report_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    daily_report_cron: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'0 9 * * *'")
    )
    daily_report_use_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class ScannerConfig(TimestampMixin, Base):
    """Singleton scanner schedule and execution defaults managed by Cryo."""

    __tablename__ = "scanner_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    schedule_cron: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'0 2 * * *'")
    )
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UTC'"))
    num_chunks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4"))
    full_scan_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cost_full_scan_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    max_concurrent_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )


class Table(Base):
    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("source_id", "schema_name", "table_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Tracks scans that fail to even read the table (e.g. a stale dbt __dbt_tmp pointer whose
    # metadata.json was deleted), as distinct from a table that scans fine but needs maintenance.
    # consecutive_scan_failures resets to 0 on any successful scan -- a single transient blip
    # (network hiccup) shouldn't flag a table as broken, only a run of repeated failures.
    consecutive_scan_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_scan_error: Mapped[str | None] = mapped_column(Text)
    last_scan_error_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # "failed" (may succeed on retry) or "unreadable" (metadata gone from object storage, so
    # retrying cannot help) -- see cryo/scanner/failure_classifier.py. Lets them be
    # listed and handed to whoever owns the lakehouse instead of being rescanned forever.
    last_scan_error_kind: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Single accountable owner for SLA/quality breach alerts (see TableAlertSubscription for the
    # broader consumer/watcher list). Free-text email -- no user directory to validate against.
    owner_email: Mapped[str | None] = mapped_column(Text)
    # S3 location (e.g. ``s3://bucket/path/to/table``) of the table's root. Computed live
    # at scan time and previously discarded; persisted here so the S3 request-cost tracking
    # feature (``docs/s3-request-cost-tracking.md`` §4.1) can do per-source bucket
    # discovery via ``SELECT DISTINCT bucket FROM tables WHERE source_id = X`` without
    # needing a separate catalog-discovery step. Updated in place on each scan, not
    # appended-to, so this column is not a scale concern.
    location: Mapped[str | None] = mapped_column(Text)
    # Critical vitals action plan, 2026-09-20, P1 "contain repeated NoSuchKey work safely":
    # a maintenance job failing with cryo.engines.base.is_table_not_found_error(error) means
    # the table's registered location's metadata.json/manifest is confirmed missing in S3 --
    # a maintenance retry cannot fix that (there's nothing to load), but the table doesn't
    # necessarily meet deactivate_orphaned_evolving_catalog_entries' narrow location-IS-NULL
    # heuristic (confirmed live: nurture_service.silver_next_day_question_summary_evolving has
    # a real non-null location whose metadata.json 404s in S3 -- the null-location automated
    # cleanup structurally cannot catch this, and is_active=False is a bigger, less-reversible
    # decision than "stop re-dispatching for a while" per the plan's own "missing metadata is
    # not permission to deactivate the table" guidance). These two columns implement the
    # narrower, fully reversible containment instead: persisted retry-after + reason, checked
    # at scheduling time (see cryo.scheduler.rules.tables_for_preset), so a confirmed-missing
    # table stops consuming a guaranteed-failing maintenance cycle every single admission wave
    # without touching is_active or dropping the row.
    maintenance_missing_metadata_reason: Mapped[str | None] = mapped_column(Text)
    maintenance_retry_after: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    source: Mapped[Source] = relationship(back_populates="tables")
    stats: Mapped[TableStats | None] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    partitions: Mapped[list[TablePartition]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    columns: Mapped[list[TableColumn]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    history: Mapped[list[TableStatsHistory]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    cost: Mapped[TableCost | None] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    cost_history: Mapped[list[TableCostHistory]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    assignment: Mapped[TableAssignment | None] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[MaintenanceJob]] = relationship(back_populates="table")
    audit_entries: Mapped[list[AuditLog]] = relationship(back_populates="table")
    protected_snapshots: Mapped[list[ProtectedSnapshot]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    alert_subscriptions: Mapped[list[TableAlertSubscription]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    alert_dispatches: Mapped[list[TableAlertDispatch]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    dark_table_candidate: Mapped[DarkTableCandidate | None] = relationship(
        back_populates="table", cascade="all, delete-orphan", uselist=False
    )


class TableStats(Base):
    __tablename__ = "table_stats"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), primary_key=True
    )
    scan_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    needs_compaction: Mapped[bool] = mapped_column(Boolean, nullable=False)
    compaction_priority: Mapped[str] = mapped_column(Text, nullable=False)
    grade: Mapped[str] = mapped_column(Text, nullable=False)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_files: Mapped[int] = mapped_column(BigInteger, nullable=False)
    small_files_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_partitions: Mapped[int | None] = mapped_column(Integer)
    max_files_per_partition: Mapped[int | None] = mapped_column(Integer)
    avg_files_per_partition: Mapped[float | None] = mapped_column(Float)
    partition_skew_ratio: Mapped[float | None] = mapped_column(Float)
    total_records: Mapped[int | None] = mapped_column(BigInteger)
    total_size_gb: Mapped[float] = mapped_column(Float, nullable=False)
    avg_file_size_mb: Mapped[float | None] = mapped_column(Float)
    total_snapshots: Mapped[int | None] = mapped_column(Integer)
    total_delete_files: Mapped[int | None] = mapped_column(Integer)
    equality_delete_files: Mapped[int | None] = mapped_column(Integer)
    position_delete_files: Mapped[int | None] = mapped_column(Integer)
    delete_file_pct: Mapped[float | None] = mapped_column(Float)
    detection_tier: Mapped[str | None] = mapped_column(Text)
    snapshot_watermark: Mapped[int | None] = mapped_column(BigInteger)
    last_snapshot_committed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), index=True
    )
    last_full_reconcile_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    manifest_count: Mapped[int | None] = mapped_column(Integer)
    partition_col: Mapped[str | None] = mapped_column(Text)
    partition_col_type: Mapped[str | None] = mapped_column(Text)
    write_distribution_mode: Mapped[str | None] = mapped_column(Text)
    has_sort_order: Mapped[bool | None] = mapped_column(Boolean)
    target_file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    estimated_monthly_s3_cost_usd: Mapped[float | None] = mapped_column(Float)
    projected_monthly_s3_cost_usd: Mapped[float | None] = mapped_column(Float)
    projected_savings_usd_month: Mapped[float | None] = mapped_column(Float)
    reclaimable_bytes: Mapped[int | None] = mapped_column(BigInteger)
    query_count_30d: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)
    # Recovery plan §3, 2026-09-20: resumable per-table partition-scoring coverage cursor. A
    # dedicated small scalar column, deliberately NOT stored inside `raw` -- that JSONB column
    # already caused one real OOM incident this same day (averaged 522KB/row, max 10.3MB,
    # ~6.8GB total across the fleet when read in bulk) precisely because it accumulates
    # unbounded nested data over time; a hot, frequently-read/written cursor value has no
    # business living in the same blob. NULL means "not mid-sweep for this table's current
    # scan" (either never started, or the last sweep reached the end and reset it -- see
    # update_partition_benefit_scores). Paired with
    # partition_coverage_cursor_scan_at so a fresh scan (new scan_at) never accidentally
    # resumes against a stale cursor computed for the previous scan's partition rows.
    partition_coverage_cursor: Mapped[int | None] = mapped_column(BigInteger)
    partition_coverage_cursor_scan_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    table: Mapped[Table] = relationship(back_populates="stats")


class TableCost(Base):
    __tablename__ = "table_cost"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), primary_key=True
    )
    scan_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    precision: Mapped[str] = mapped_column(Text, nullable=False)
    price_per_gb_month: Mapped[float] = mapped_column(Float, nullable=False)
    live_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dead_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    orphan_bytes: Mapped[int | None] = mapped_column(BigInteger)
    listed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    last_full_cost_scan_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    reclaim_expire_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    reclaim_orphan_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    reclaim_optimize_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    reclaim_manifests_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    current_monthly_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    projected_monthly_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    projected_savings_usd_month: Mapped[float] = mapped_column(Float, nullable=False)
    query_request_savings_usd_month: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    # Measured S3 request-cost rollup (docs/s3-request-cost-tracking.md §6.3), distinct from
    # query_request_savings_usd_month above which is a *projected savings estimate* from
    # internal query telemetry, not a measured AWS request count. These four are a 30-day
    # rolling summary, recomputed from S3RequestCostDaily (the bounded, retained per-day
    # table) after each ingestion run -- this is the one-row-per-table read path the UI
    # queries; S3RequestCostDaily is the write/audit path behind it. NULL here (not 0) means
    # "no ingestion has run for this table yet", distinguishing "measured zero requests" from
    # "not tracked" -- the UI must not render a NULL as $0.
    get_requests_30d: Mapped[int | None] = mapped_column(BigInteger)
    put_requests_30d: Mapped[int | None] = mapped_column(BigInteger)
    list_requests_30d: Mapped[int | None] = mapped_column(BigInteger)
    # Added 2026-09-08 (docs/s3-total-cost-optimization.md §1.1, P0#2/#3): HEAD was previously
    # silently folded into get_requests_30d in recompute_table_cost_rollup, so a real HEAD
    # regression (found live: 57.7% of tables got MORE HEAD requests after maintenance,
    # statistically significant the wrong way) had no dedicated field the UI could ever surface
    # -- it just inflated the GET number without anyone being able to tell why. delete_requests_30d
    # is tracked for visibility/operational-load reasons (throttling risk, cleanup volume) even
    # though DELETE itself is free -- see _operation_tier's own docstring in
    # cryo/scanner/s3_request_cost_ingest.py for the pricing fix this pairs with.
    head_requests_30d: Mapped[int | None] = mapped_column(BigInteger)
    delete_requests_30d: Mapped[int | None] = mapped_column(BigInteger)
    request_cost_usd_month: Mapped[float | None] = mapped_column(Float)
    # "live" (fetch_s3_request_prices succeeded for every day in the window) or
    # "fallback_default" (at least one day's rollup used the fallback price) -- the UI uses
    # this, not request_cost_usd_month's mere presence, to decide whether to show the
    # "approximate" label from §6.4. Never let a fallback-derived number look as confident as
    # a fully live-priced one.
    request_cost_pricing_source: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)

    table: Mapped[Table] = relationship(back_populates="cost")


class TableCostHistory(Base):
    __tablename__ = "table_cost_history"
    __table_args__ = (Index("ix_table_cost_history_table_scan", "table_id", "scan_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    scan_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    current_monthly_cost_usd: Mapped[float | None] = mapped_column(Float)
    projected_savings_usd_month: Mapped[float | None] = mapped_column(Float)
    reclaimable_bytes: Mapped[int | None] = mapped_column(BigInteger)
    precision: Mapped[str | None] = mapped_column(Text)

    table: Mapped[Table] = relationship(back_populates="cost_history")


class TablePartition(Base):
    __tablename__ = "table_partitions"
    __table_args__ = (
        Index("ix_table_partitions_table_scan", "table_id", "scan_at"),
        Index("ix_table_partitions_values_gin", "partition_values", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    scan_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    partition_value: Mapped[str | None] = mapped_column(Text)
    partition_values: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    partition_fields: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    partition_spec_id: Mapped[int | None] = mapped_column(Integer)
    partition_path: Mapped[str | None] = mapped_column(Text)
    partition_key: Mapped[str | None] = mapped_column(Text)
    base_column: Mapped[str | None] = mapped_column(Text)
    base_column_type: Mapped[str | None] = mapped_column(Text)
    transform: Mapped[str | None] = mapped_column(Text)
    files_per_partition: Mapped[int | None] = mapped_column(Integer)
    small_files_in_partition_count: Mapped[int | None] = mapped_column(Integer)
    partition_size_gb: Mapped[float | None] = mapped_column(Float)
    compaction_status: Mapped[str | None] = mapped_column(Text)
    # AutoComp-style benefit scoring (SIGMOD 2025)
    query_frequency: Mapped[float | None] = mapped_column(
        Float
    )  # queries/day hitting this partition
    compaction_benefit_score: Mapped[float | None] = mapped_column(
        Float
    )  # small_files × query_frequency
    # MOOP multi-objective score (SIGMOD 2025 §A2): file_entropy is the Shannon entropy of the
    # partition's file-size histogram (higher = more benefit from compaction); autocomp_traits
    # is {t1,t2,t3,t4,s_c,weights_version} -- the normalized trait inputs to s_c, kept alongside
    # the score itself so the UI/admission gate don't need to recompute normalization.
    file_entropy: Mapped[float | None] = mapped_column(Float)
    autocomp_traits: Mapped[JsonDict | None] = mapped_column(JSONB)
    last_compacted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    input_files_last_compaction: Mapped[int | None] = mapped_column(
        Integer
    )  # effectiveness ratio tracking
    output_files_last_compaction: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)

    table: Mapped[Table] = relationship(back_populates="partitions")


class TableColumn(TimestampMixin, Base):
    """Latest per-column schema index populated at scan time for catalog search."""

    __tablename__ = "table_columns"
    __table_args__ = (Index("ix_table_columns_column_name", "column_name"),)

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    column_name: Mapped[str] = mapped_column(Text, primary_key=True)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    table: Mapped[Table] = relationship(back_populates="columns")


class TableStatsHistory(Base):
    __tablename__ = "table_stats_history"
    __table_args__ = (Index("ix_table_stats_history_table_scan", "table_id", "scan_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    scan_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    small_files_count: Mapped[int | None] = mapped_column(BigInteger)
    total_files: Mapped[int | None] = mapped_column(BigInteger)
    total_size_gb: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(Text)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)

    table: Mapped[Table] = relationship(back_populates="history")


class FleetImpactHistory(Base):
    """One row per calendar day: the fleet-wide numbers Cryo actually produced that day,
    written once (idempotent upsert on ``report_date``) from the same data the daily report
    already computes -- see cryo.reporting.impact_history.record_daily_impact.

    Exists so "how much did Cryo reclaim/cost/fix" is a durable, queryable trend instead of
    something re-derived by hand from AuditLog each time someone asks -- every number in this
    session's management-report work (20.1 TiB reclaim, tables touched, measurement coverage)
    came from one-off queries against raw tables with no persisted record of what was reported
    on any given day. bytes_freed/measurement_state carry the same "unmeasured is not zero"
    discipline as ImpactSignal itself (see impact_signals.py's own docstring) -- a NULL/
    "unmeasured" day must never be summed as if it were a real zero.
    """

    __tablename__ = "fleet_impact_history"
    __table_args__ = (UniqueConstraint("report_date", name="uq_fleet_impact_history_report_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # The calendar day this row reports on (compute_impact_signal's "yesterday", i.e. the day
    # before the report ran) -- not the timestamp the report itself ran at.
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Reclaim -- mirrors ImpactSignal.today / DailyImpact exactly.
    bytes_freed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    measurement_state: Mapped[str] = mapped_column(Text, nullable=False)
    actions_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_touched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_with_bytes_evidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_without_bytes_evidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    optimize_files_in: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    optimize_files_out: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Fleet snapshot at report time (not the reporting day specifically -- these are current-
    # state gauges, same source as the daily digest's own cost/health section).
    active_tables: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_grade_d_or_f: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size_tb: Mapped[float | None] = mapped_column(Float)
    total_monthly_cost_usd: Mapped[float | None] = mapped_column(Float)
    projected_savings_usd_month: Mapped[float | None] = mapped_column(Float)

    # Reliability, same window/status semantics as MaintenanceJob.status.
    jobs_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cryo's own infra cost (K8s namespace, used+unused) for the reporting day, when available
    # -- same source as cost_ceiling.py's circuit breaker. NULL, not 0, when the Grafana/CUR
    # lookup wasn't available that day.
    infra_cost_usd: Mapped[float | None] = mapped_column(Float)

    raw: Mapped[JsonDict | None] = mapped_column(JSONB)


class ProtectedSnapshot(Base):
    __tablename__ = "protected_snapshots"
    __table_args__ = (Index("ix_protected_snapshots_table", "table_id", "snapshot_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    label: Mapped[str | None] = mapped_column(Text)
    protected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table: Mapped[Table] = relationship(back_populates="protected_snapshots")


class DeletionTracking(Base):
    """Tracks rows pending hard-delete for GDPR/CCPA compliance.

    Soft-deleted rows remain query-visible until the next successful
    expire_snapshots pass clears them. This table records the
    soft-delete timestamp, the count, and the projected hard-delete
    cutoff date the expiry will reach.
    """

    __tablename__ = "deletion_tracking"
    __table_args__ = (Index("ix_deletion_tracking_table", "table_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    soft_deleted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    pending_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blocked_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class TableAlertSubscription(Base):
    """A consumer/watcher who wants SLA or data-quality breach alerts for a table.

    Distinct from ``Table.owner_email`` -- the owner is the single accountable person,
    subscriptions are the broader "anyone who reads this table and wants to know" list.
    """

    __tablename__ = "table_alert_subscriptions"
    __table_args__ = (UniqueConstraint("table_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table: Mapped[Table] = relationship(back_populates="alert_subscriptions")


class TableAlertDispatch(Base):
    """Record of a breach-alert notification actually sent, for cooldown + audit history."""

    __tablename__ = "table_alert_dispatches"
    __table_args__ = (Index("ix_table_alert_dispatches_table", "table_id", "sent_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    recipients: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    sent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table: Mapped[Table] = relationship(back_populates="alert_dispatches")


class DarkTableCandidate(Base):
    """A table with sustained zero query activity across several consecutive 30-day windows.

    ``consecutive_zero_query_windows`` -- not a single ``query_count_30d == 0`` snapshot -- is
    what makes this a real signal instead of a false positive on a legitimately-quarterly-use
    table: see ``cryo.telemetry.dark_tables.detect_dark_table_candidates`` for the detection
    job's own reasoning. Detection/surfacing only -- ``status`` starts at ``'candidate'`` and
    this model has no code path that deletes or archives the underlying table data; that's a
    deliberately separate, future, explicitly opt-in action.
    """

    __tablename__ = "dark_table_candidates"
    __table_args__ = (UniqueConstraint("table_id", name="uq_dark_table_candidates_table"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    consecutive_zero_query_windows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    first_zero_query_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    total_size_gb: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_monthly_cost_usd: Mapped[float | None] = mapped_column(Float)
    # 'candidate' | 'dismissed' | 'archived' (see class docstring -- 'archived' is reserved,
    # nothing in this codebase writes it yet).
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="candidate")
    dismissed_by: Mapped[str | None] = mapped_column(Text)
    dismissed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    dismissed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table: Mapped[Table] = relationship(back_populates="dark_table_candidate")


class TierPolicyRecord(TimestampMixin, Base):
    __tablename__ = "tier_policies"
    __table_args__ = (UniqueConstraint("source_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_class: Mapped[str] = mapped_column(Text, nullable=False)
    min_age_days: Mapped[int] = mapped_column(Integer, nullable=False)
    predicate: Mapped[JsonDict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    source: Mapped[Source] = relationship(back_populates="tier_policies")


class AWSIntegration(TimestampMixin, Base):
    """Optional, free-tier-only AWS integration for a lakehouse's S3 bucket.

    Deliberately limited to APIs that cost nothing: S3's default daily CloudWatch storage metrics
    (BucketSizeBytes/NumberOfObjects — published automatically, no setup) read via
    ``GetMetricStatistics`` (not ``GetMetricData``, which AWS always bills regardless of the
    CloudWatch free tier), plus the free AWS Price List API for real regional/storage-class unit
    prices. Cost Explorer, CloudWatch request-metrics filters, and CUR all cost money or need
    billing-admin setup — none of that is wired up here on purpose. See
    cryo/integrations/aws_s3_metrics.py.
    """

    __tablename__ = "aws_integrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    access_key_id: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_tested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)

    source: Mapped[Source] = relationship(back_populates="aws_integration")


class Preset(TimestampMixin, Base):
    __tablename__ = "presets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.destinations.id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'standard'"))
    operations: Mapped[JsonList] = mapped_column(JSONB, nullable=False)
    params: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    schedule_cron: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    guardrails: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    destination: Mapped[Destination] = relationship(back_populates="presets")
    rules: Mapped[list[PresetRule]] = relationship(
        back_populates="preset", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[TableAssignment]] = relationship(back_populates="preset")
    runs: Mapped[list[MaintenanceRun]] = relationship(back_populates="preset")


class PresetRule(Base):
    __tablename__ = "preset_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    preset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.presets.id", ondelete="CASCADE"), nullable=False
    )
    predicate: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    preset: Mapped[Preset] = relationship(back_populates="rules")


class TableAssignment(Base):
    __tablename__ = "table_assignments"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), primary_key=True
    )
    preset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.presets.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    param_overrides: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    table: Mapped[Table] = relationship(back_populates="assignment")
    preset: Mapped[Preset] = relationship(back_populates="assignments")


class MaintenanceRun(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    preset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.presets.id")
    )
    trigger: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    deadline_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # A one-off future-scheduled run (distinct from a Preset's recurring schedule_cron).
    # NULL means "not scheduled -- either an immediate run, or already dispatched". When set,
    # status is "scheduled" and the jobs exist but are not yet enqueued; the scheduler's
    # _maybe_dispatch_scheduled_runs tick flips status to "queued" and enqueues once
    # scheduled_at has passed. See docs/ux-improvement-loop.md for the originating design
    # conversation and cryo/scheduler/service.py for the dispatch step.
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    summary: Mapped[JsonDict | None] = mapped_column(JSONB)

    preset: Mapped[Preset | None] = relationship(back_populates="runs")
    jobs: Mapped[list[MaintenanceJob]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MaintenanceJob(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_run_status", "run_id", "status"),
        Index("ix_jobs_table_finished", "table_id", "finished_at"),
        # One active job per (table, operation set). dedupe_key is NULL for forced jobs and for
        # jobs from creators that do not dedupe, which exempts them. See job_dedupe.py.
        Index(
            "ux_jobs_active_table_ops",
            "table_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'admitted', 'running', 'deferred') AND dedupe_key IS NOT NULL"
            ),
            sqlite_where=text(
                "status IN ('queued', 'admitted', 'running', 'deferred') AND dedupe_key IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.destinations.id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[JsonDict | None] = mapped_column(JSONB)
    # Explicit operation subset this job was trimmed or requested to, set only when dedupe
    # narrowed it. NULL means the run summary / mode decides. When set it bounds the plan the
    # same way a run summary's operations list does.
    operations: Mapped[JsonList | None] = mapped_column(JSONB)
    # Canonical effective-operations string used by ux_jobs_active_table_ops; NULL = exempt.
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    engine_ref: Mapped[str | None] = mapped_column(Text)
    replica: Mapped[str | None] = mapped_column(Text)
    # Stamped by mark_status() the instant a job transitions to ADMITTED. Exists purely so
    # reconcile_stuck_admitted_jobs (queue/reconcile.py) can tell "just admitted this wave" from
    # "admitted N hours ago and never dispatched" -- neither started_at nor finished_at cover
    # this: started_at is only set on RUNNING, and finished_at is cleared (not set) on ADMITTED.
    # Only the "the queue lost this entirely" case is left (a Redis restart without
    # persistence); a worker dying mid-handoff is now recovered by the consumer group itself.
    admitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Plan Verifier (arXiv:2510.09567) -- persisted verdict so the worker + audit trail can see
    # whether the plan was proof-checked at enqueue time, and reproduce the verdict hash.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    plan_hash: Mapped[str | None] = mapped_column(Text)
    current_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_intents.id", use_alter=True),
        nullable=True,
        index=True,
    )
    intent_version: Mapped[int | None] = mapped_column(Integer)
    verifier_version: Mapped[str | None] = mapped_column(Text)
    verifier_violations: Mapped[JsonList | None] = mapped_column(JSONB)
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.native_dispatches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Operator-triggered bypass of soft scheduling gates (cooldown, benefit/moop score,
    # offpeak window) in decide_job_admission -- set via POST /jobs/{id}/force-admit when a
    # manually-triggered run keeps cycling through queued/deferred because an unrelated
    # successful run against the same table just reset its cooldown clock. Table locks, PII,
    # and ABAC checks are real safety gates and are never bypassed by this flag.
    force_admit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Write-ahead statement index for worker step ledger (docs/WORKER_STEP_LEDGER.md).
    current_step_index: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[MaintenanceRun] = relationship(back_populates="jobs")
    table: Mapped[Table] = relationship(back_populates="jobs")
    destination: Mapped[Destination] = relationship(back_populates="jobs")
    audit_entries: Mapped[list[AuditLog]] = relationship(back_populates="job")
    dispatch: Mapped[NativeDispatch | None] = relationship(back_populates="jobs")
    steps: Mapped[list[JobStep]] = relationship(back_populates="job", cascade="all, delete-orphan")


class AnalysisTask(TimestampMixin, Base):
    """Durable, resumable heavy-analysis work unit (recovery plan §3, 2026-09-20).

    Root incident this exists to fix: the scheduler process ran a multi-hour, ~13k-table
    benefit-score/realized-value sweep as an in-process thread with no persisted progress. A
    restart (crash, OOM, redeploy) threw away all progress and started the entire sweep over
    from table 1 -- the crash-loop was actively costing scheduling throughput, not just
    restarting harmlessly. This table lets the actual analysis work run as a short-lived,
    separately-resourced K8s Job (see cryo/analysis/runner.py's one-shot CLI entrypoint) while
    the scheduler stays a lightweight coordinator that only claims/dispatches/reconciles these
    rows -- heavy allocation lives and dies in a bounded child process, never in the scheduler's
    own long-lived memory space.

    One active logical task per (task_type, scope) is enforced by the partial unique index
    below (WHERE status IN ('pending', 'leased', 'running')) -- a new due task for the same
    (task_type, scope) cannot be created while a prior generation is still open, so restarts
    reconcile the existing row instead of ever creating a second one racing it.

    generation increments each time a *new* logical run of this task starts (not each retry --
    retries reuse attempt/lease_generation on the same generation). A stale child that finishes
    after being superseded (e.g. its lease expired and another owner already claimed a new
    generation) can be detected by comparing its own captured generation against the current
    row's generation before committing output -- see AnalysisTaskRepository.commit_progress's
    docstring for the compare-and-set this enables.
    """

    __tablename__ = "analysis_tasks"
    __table_args__ = (
        # Enforces "one active logical task per (task_type, scope)" -- see class docstring.
        Index(
            "ux_analysis_tasks_active_type_scope",
            "task_type",
            "scope",
            unique=True,
            postgresql_where=text("status IN ('pending', 'leased', 'running')"),
        ),
        Index("ix_analysis_tasks_due_at", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # e.g. "benefit_score_sweep" -- see cryo/analysis/registry.py for the known task types.
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Free-form scope key distinguishing concurrent instances of the same task_type -- "fleet"
    # for a whole-fleet sweep today; a specific table_id/shard key if this is ever split
    # narrower. Never NULL: use a fixed literal like "fleet" for singleton tasks so the unique
    # index above has a real value to constrain on.
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    # Incremented each new logical run (not each retry of the same run) -- see class docstring.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    due_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # The scan/data watermark this generation is pinned against (e.g. a scan_at timestamp or
    # snapshot id) -- see recovery plan §3's "never mix generations silently": if new scan
    # evidence invalidates the watermark mid-sweep, the coordinator starts a fresh generation
    # rather than silently blending old and new pages into one cursor's progress.
    input_watermark: Mapped[str | None] = mapped_column(Text)
    # Opaque, task-type-defined resume position (e.g. a table_id to resume keyset pagination
    # from). NULL means "not started" for this generation.
    cursor: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lease_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # The dispatched K8s Job's name -- deterministic (derived from id+generation+attempt, see
    # cryo/analysis/coordinator.py) so an uncertain create response (timeout, ambiguous error)
    # can be reconciled by re-reading the Job's actual status instead of blindly retrying and
    # risking a duplicate. Kubernetes Jobs are not exactly-once; this is what makes replay safe.
    external_job_ref: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    processed_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Review finding F08, 2026-09-20: opaque, task-type-defined extra checkpoint state that
    # doesn't fit the single-string `cursor` field -- e.g. the benefit-score sweep's
    # realized-value refresh budget, which must be consumed once across an entire fleet
    # generation's batches, not reset to its full allowance on every individual batch (the bug:
    # _DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP=100 was being re-applied per 200-table
    # batch, so a ~13,000-table fleet could spend roughly 6,500 expensive refreshes per
    # generation instead of the intended 100). NULL means "no extra state yet" for this
    # generation; start_new_generation resets it to NULL along with cursor/processed_count.
    task_state: Mapped[JsonDict | None] = mapped_column(JSONB)


class IncidentCohort(TimestampMixin, Base):
    """A named, durable record of runs/jobs affected by a specific production incident
    (recovery plan §4/§5, 2026-09-20).

    Real gap this fixes: the Sep 17-18 zombie-run investigation (2,058/hour orphaned_deferred
    spike, traced via ad-hoc kubectl exec SQL queries to a set of runs from that outage window)
    existed only as a one-off finding in an investigation session -- nothing persisted which
    specific run/job IDs were confirmed part of that incident, the query used to derive them, or
    what happened to each one afterward. A later engineer (or agent) re-investigating the same
    symptom has no way to tell "this is the same known cohort, already tracked" from "this is a
    new, unrelated occurrence" without re-deriving the whole finding from scratch -- and worse,
    has no record to check before assuming a stale job is safe to fail outright.

    Deliberately read-heavy, not an automation trigger: creating a cohort record does not, by
    itself, fail/cancel/relabel anything. Actual remediation of each member still goes through
    the existing reconciliation machinery (cryo.worker.orphan_recovery.recover_orphaned_jobs,
    cryo.queue.reconcile.reconcile_stuck_admitted_jobs) which already checks external execution
    state before touching a job -- this table is where that machinery's *outcome* per member
    gets recorded for accountability, not a new code path for mutating job/run state.
    """

    __tablename__ = "incident_cohorts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Human-readable stable slug, e.g. "sep-17-18-zombie-runs" -- not a UUID, so it can be typed
    # into a doc/runbook/query without a lookup.
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # The exact SQL (or equivalent) used to derive membership -- so "is this run part of the
    # cohort" is always re-verifiable against the original evidence, not just trusted by name.
    evidence_query: Mapped[str] = mapped_column(Text, nullable=False)


class IncidentCohortMember(TimestampMixin, Base):
    """One run believed to be part of an IncidentCohort, plus the current reconciliation
    outcome for it. See IncidentCohort's docstring for the "persist, don't auto-mutate"
    design intent.

    reconciliation_status is deliberately a free-text-ish enum-like string, not tied to
    MaintenanceRun/MaintenanceJob's own status vocabulary -- "attempt ended", "intent still
    outstanding", and "run scope closed" are three genuinely distinct concepts (recovery plan
    §4) that a run's own `status` column cannot represent simultaneously, and conflating them
    was exactly what made the original Sep 17-18 investigation hard to reason about precisely.
    """

    __tablename__ = "incident_cohort_members"
    __table_args__ = (
        Index("ux_incident_cohort_members_cohort_run", "cohort_id", "run_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.incident_cohorts.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id"), nullable=False
    )
    # "pending" (not yet reconciled), "attempt_ended" (the specific job attempt finished, terminal
    # or not, but the run's overall scope may still have outstanding work), "run_scope_closed"
    # (the run itself reached a terminal status -- distinct from any one job/attempt within it),
    # "unresolved" (investigated, genuinely still stuck, needs a human decision). Never
    # "reconciled_as_success" for a job that actually failed -- see IncidentCohort's docstring:
    # this table records outcomes, it never relabels history.
    reconciliation_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    # Review finding F11, 2026-09-20: the original seed script selected every run created in a
    # time window and inserted them all as equally-weighted "members" -- broader than an
    # evidence-confirmed "this run was actually affected by the incident" cohort. A window
    # match is a real, useful starting point (it's how the investigation actually found these
    # runs), but it is NOT the same claim as "confirmed affected," and collapsing the two let a
    # later reader treat every window-matched run as equally implicated. "candidate" (default:
    # matched the window query, not yet individually verified), "confirmed" (independently
    # checked against the run's own evidence -- e.g. its job statuses/timestamps -- and found
    # to actually exhibit the incident's symptom), "excluded" (checked and found NOT actually
    # affected, e.g. a run that happens to have been created in the window but has no
    # orphaned_deferred/stuck-job symptoms at all).
    membership_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'candidate'")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    reconciled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class MaintenanceIntent(TimestampMixin, Base):
    __tablename__ = "maintenance_intents"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_maintenance_intents_job_version"),
        UniqueConstraint("idempotency_key", name="uq_maintenance_intents_idempotency_key"),
        Index("ix_maintenance_intents_table_status", "table_id", "status"),
        Index("ix_maintenance_intents_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_intents.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id"), nullable=False
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id"), nullable=False
    )
    table_ref: Mapped[str] = mapped_column(Text, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.destinations.id"), nullable=False
    )
    operation_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    observed_snapshot_id: Mapped[int | None] = mapped_column(BigInteger)
    policy_id: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str | None] = mapped_column(Text)
    canonical_plan: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    approval_bindings: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    fencing_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    budget: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    capability_requirements: Mapped[JsonList] = mapped_column(JSONB, nullable=False)
    containment_action: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class MaintenanceFence(TimestampMixin, Base):
    __tablename__ = "maintenance_fences"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_intents.id", ondelete="SET NULL"),
        nullable=True,
    )


class MaintenanceAttempt(TimestampMixin, Base):
    __tablename__ = "maintenance_attempts"
    __table_args__ = (
        UniqueConstraint("intent_id", "attempt_number", name="uq_attempts_intent_number"),
        Index("ix_maintenance_attempts_status_heartbeat", "status", "heartbeat_at"),
        Index(
            "uq_maintenance_attempts_active_intent",
            "intent_id",
            unique=True,
            postgresql_where=text("status = 'started'"),
            sqlite_where=text("status = 'started'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_intents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error: Mapped[JsonDict | None] = mapped_column(JSONB)


class EffectReceipt(TimestampMixin, Base):
    __tablename__ = "effect_receipts"
    __table_args__ = (
        UniqueConstraint("intent_id", "step_index", name="uq_effect_receipts_intent_step"),
        UniqueConstraint("idempotency_key", name="uq_effect_receipts_idempotency_key"),
        Index("ix_effect_receipts_state_probe", "state", "next_probe_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_intents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.maintenance_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.destinations.id"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    external_reference: Mapped[str | None] = mapped_column(Text)
    submission: Mapped[JsonDict | None] = mapped_column(JSONB)
    result: Mapped[JsonDict | None] = mapped_column(JSONB)
    error: Mapped[JsonDict | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    probe_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_probe_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class MaintenanceControlState(TimestampMixin, Base):
    __tablename__ = "maintenance_control_state"
    __table_args__ = (UniqueConstraint("scope_type", "scope_id", name="uq_control_state_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)


class MaintenanceExclusion(Base):
    """Operator opt-out from maintenance. ``table_name`` NULL excludes the whole schema.

    Scans still read excluded tables so health stays visible; only maintenance skips them.
    """

    __tablename__ = "maintenance_exclusions"
    __table_args__ = (
        Index(
            "uq_maintenance_exclusions_target",
            "source_id",
            "schema_name",
            text("coalesce(table_name, '')"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = open-ended (schema/table opt-outs from Control Room); set = a time-boxed hold.
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class TableOperationExclusion(Base):
    """One maintenance operation the planner must never plan for one table.

    Unlike ``MaintenanceExclusion`` the table is still selected and maintained; only the
    named operation is dropped from every plan built for it.
    """

    __tablename__ = "table_operation_exclusions"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


_IMMUTABLE_INTENT_FIELDS = (
    "tenant_id",
    "job_id",
    "parent_intent_id",
    "version",
    "actor_type",
    "actor_id",
    "source_id",
    "table_id",
    "table_ref",
    "destination_id",
    "operation_scope",
    "observed_snapshot_id",
    "policy_id",
    "policy_version",
    "canonical_plan",
    "plan_hash",
    "idempotency_key",
    "approval_bindings",
    "fencing_epoch",
    "budget",
    "capability_requirements",
    "containment_action",
    "expires_at",
)


@event.listens_for(MaintenanceIntent, "before_update")
def _reject_maintenance_intent_rebinding(
    _mapper: object,
    _connection: object,
    target: MaintenanceIntent,
) -> None:
    state = inspect(target)
    changed = [
        field for field in _IMMUTABLE_INTENT_FIELDS if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError("maintenance intent bound fields are immutable: " + ", ".join(changed))


class JobStep(Base):
    """Write-ahead per-statement ledger for a maintenance job.

    ``begin_step`` before engine execute; ``complete_step`` after. See
    ``docs/WORKER_STEP_LEDGER.md``.
    """

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "step_index", name="uq_job_steps_job_step"),
        Index("ix_job_steps_job_status", "job_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'started'"))
    engine_ref: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    job: Mapped[MaintenanceJob] = relationship(back_populates="steps")


class NativeDispatch(Base):
    """One Kubernetes Job that executes 1..M native maintenance table jobs.

    Created by the Governor on admit. JobWatcher finalizes member jobs when the
    K8s Job completes. Memory requests are hard-capped at 16Gi.
    """

    __tablename__ = "native_dispatches"
    __table_args__ = (
        Index("ix_native_dispatches_run", "run_id"),
        Index("ix_native_dispatches_status", "status"),
        Index("ix_native_dispatches_k8s_job", "k8s_job_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.destinations.id"), nullable=False
    )
    k8s_job_name: Mapped[str | None] = mapped_column(Text)
    k8s_namespace: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'default'")
    )
    cpu_milli: Mapped[int] = mapped_column(Integer, nullable=False)
    mem_mib: Mapped[int] = mapped_column(Integer, nullable=False)
    table_job_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    dedicated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    jobs: Mapped[list[MaintenanceJob]] = relationship(back_populates="dispatch")


class ChunkCheckpoint(Base):
    """Per-chunk execution checkpoint for native engine.

    Written after each chunk commits to Iceberg. Allows resume after mid-run failure:
    the worker queries which chunks already succeeded, then re-runs only the remaining ones.
    Chunk ordering is defined by the planner's greedy_pack, which produces a deterministic
    sequence of partition-disjoint chunks.

    ``table_id`` disambiguates chunks when multiple tables share a run (multi-table pods).
    """

    __tablename__ = "chunk_checkpoints"
    __table_args__ = (
        Index("ix_chunk_checkpoints_run", "run_id"),
        Index("ix_chunk_checkpoints_table", "table_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    partition_summary: Mapped[str | None] = mapped_column(Text)
    input_files: Mapped[int | None] = mapped_column(Integer)
    output_files: Mapped[int | None] = mapped_column(Integer)
    bytes_removed: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    committed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    raw_result: Mapped[JsonDict | None] = mapped_column(JSONB)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_table_ts", "table_id", "ts"),
        Index("ix_audit_log_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cryo.jobs.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    actual_bytes_freed: Mapped[int | None] = mapped_column(BigInteger)
    # Compaction effectiveness ratio tracking (Feature 3)
    input_files_count: Mapped[int | None] = mapped_column(Integer)
    output_files_count: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    committed_snapshot_id: Mapped[str | None] = mapped_column(Text)
    # Async fallback-dispatch tracking (): engine_ref is the dispatched Spark
    # job's K8s Job name, set when status="SUBMITTED" so a later sweep can look up its
    # real outcome without run_wave() blocking on it. resolved_at marks when that sweep
    # last updated the row, distinguishing "still pending" from "already resolved."
    engine_ref: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    table: Mapped[Table | None] = relationship(back_populates="audit_entries")
    job: Mapped[MaintenanceJob | None] = relationship(back_populates="audit_entries")


# ============================================================================
# Query Telemetry & Layout Optimization
# ============================================================================


class QueryLog(Base):
    """Raw query log entries from query engines (Trino, Spark, etc.)."""

    __tablename__ = "query_logs"
    __table_args__ = (
        Index("ix_query_logs_table_id", "table_id"),
        Index("ix_query_logs_executed_at", "executed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id"), nullable=False
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id")
    )
    query_id: Mapped[str] = mapped_column(Text, nullable=False)  # Engine's query ID
    engine: Mapped[str] = mapped_column(Text, nullable=False)  # trino, spark, etc.
    query_text: Mapped[str | None] = mapped_column(Text)  # Full SQL (optional)
    query_type: Mapped[str | None] = mapped_column(Text)  # SELECT, INSERT, etc.
    filter_columns: Mapped[JsonList | None] = mapped_column(
        JSONB
    )  # [{column, operator, cardinality}]
    join_columns: Mapped[JsonList | None] = mapped_column(JSONB)  # [column, ...]
    projection_columns: Mapped[JsonList | None] = mapped_column(JSONB)  # Selected columns
    executed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    rows_read: Mapped[int | None] = mapped_column(BigInteger)
    bytes_read: Mapped[int | None] = mapped_column(BigInteger)
    files_read: Mapped[int | None] = mapped_column(Integer)
    files_pruned: Mapped[int | None] = mapped_column(Integer)  # Files skipped via stats
    user_name: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)  # Full engine response

    table: Mapped[Table | None] = relationship()


class QueryPattern(Base):
    """Aggregated query patterns per table for layout optimization."""

    __tablename__ = "query_patterns"
    __table_args__ = (
        UniqueConstraint("table_id", "column_name", name="uq_query_patterns_table_column"),
        Index("ix_query_patterns_table_id", "table_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id"), nullable=False
    )
    column_name: Mapped[str] = mapped_column(Text, nullable=False)
    filter_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    equality_filter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    range_filter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    join_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    projection_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    avg_selectivity: Mapped[float | None] = mapped_column(Float)  # Estimated % of rows matched
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    table: Mapped[Table] = relationship()


class TableQueryActivity(Base):
    """Day-bucketed per-table query counts, the source of truth for TableStats.query_count_30d.

    query_patterns is aggregated per (table, column) -- filter/join/projection counts, not a
    query-level counter -- so it can't answer "how many queries touched this table" on its own.
    Both telemetry ingestion paths (legacy query_logs poll and the Iceberg read-through source)
    increment this once per matched query, independent of how many columns that query touched,
    so a rolling 30-day sum here is a real distinct-query count. Day-bucketed (not a single
    running total) so the 30-day window can be computed with a plain SUM(...) WHERE day >= cutoff
    instead of needing incremental decay logic as old activity ages out.
    """

    __tablename__ = "table_query_activity"
    __table_args__ = (
        UniqueConstraint("table_id", "day", name="uq_table_query_activity_table_day"),
        Index("ix_table_query_activity_table_id_day", "table_id", "day"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    table: Mapped[Table] = relationship()


class LayoutRecommendation(TimestampMixin, Base):
    """Layout optimization recommendations per table."""

    __tablename__ = "layout_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id"), nullable=False, unique=True
    )
    current_sort_order: Mapped[JsonList | None] = mapped_column(JSONB)  # Current table sort order
    recommended_strategy: Mapped[str] = mapped_column(Text, nullable=False)  # none, sort, zorder
    recommended_columns: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False
    )  # Columns to sort/cluster by
    estimated_prune_improvement: Mapped[float | None] = mapped_column(
        Float
    )  # Expected % improvement
    current_prune_efficiency: Mapped[float | None] = mapped_column(
        Float
    )  # Current file pruning rate
    analysis_query_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )  # pending, applied, rejected
    approval_note: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    dismissed_by: Mapped[str | None] = mapped_column(Text)
    dismissed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    dismissed_reason: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    applied_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id")
    )
    baseline_scan_bytes: Mapped[int | None] = mapped_column(BigInteger)
    post_apply_scan_bytes: Mapped[int | None] = mapped_column(BigInteger)
    observed_prune_improvement: Mapped[float | None] = mapped_column(Float)
    # Write-once snapshot of current_prune_efficiency captured at execute_layout_recommendation
    # time (the moment a rewrite is actually dispatched) -- see migration 20260817_0043 for why
    # current_prune_efficiency itself can't serve as this baseline: generate_layout_recommendation
    # overwrites it in place on every regeneration tick, with no status check, so an applied
    # recommendation's real "before" value silently gets clobbered by the next routine refresh.
    applied_prune_efficiency_baseline: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[JsonDict | None] = mapped_column(JSONB)  # Additional analysis details
    bloom_filter_columns: Mapped[JsonList | None] = mapped_column(
        JSONB
    )  # Bloom filter recommendations

    table: Mapped[Table] = relationship()


class GovernorBanditState(TimestampMixin, Base):
    __tablename__ = "governor_bandit_state"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))


class ReportSendState(TimestampMixin, Base):
    """When each scheduled report was last actually delivered.

    This lives in the database rather than in ReportScheduler's memory because the
    scheduler is a restartable pod and cron_is_due() treats "no last run" as due. On
    2026-09-23 that sent the daily report to Slack 5 times and fired all four reports
    on 8 separate pod starts, so process memory is not a safe place to keep it.
    """

    __tablename__ = "report_send_state"

    report_key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sent_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class QualityConstraint(TimestampMixin, Base):
    """A user-declared expectation over a table's stored scan metrics (cryo.quality.constraints).

    ``field_id`` pins column constraints to the Iceberg field, so a rename keeps the rule and a
    dropped column turns it unknown instead of silently matching a new column of the same name.
    """

    __tablename__ = "quality_constraints"
    __table_args__ = (UniqueConstraint("table_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    field_id: Mapped[int | None] = mapped_column(Integer)
    column_name: Mapped[str | None] = mapped_column(Text)
    params: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    # 'warn' results stay on the table page; 'error' failures also raise a table alert.
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'error'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[str | None] = mapped_column(Text)


class QualityConstraintResult(Base):
    """One evaluation of a constraint against one scan; Deequ's metrics repository."""

    __tablename__ = "quality_constraint_results"
    __table_args__ = (Index(None, "constraint_id", "evaluated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    constraint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.quality_constraints.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evaluated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(BigInteger)
    scanned_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    observed: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))


class ObservationArchiveMeta(TimestampMixin, Base):
    """Singleton-ish row tracking the local observation lakehouse (SANER 2026, arXiv:2512.02795).
    Records when the archive was last compacted so /observability/stats + the cron compactor can
    surface that without re-walking every file."""

    __tablename__ = "observation_archive_meta"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    archive_path: Mapped[str] = mapped_column(Text, nullable=False)
    last_compact_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_compact_files_merged: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_compact_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class TableEmbedding(TimestampMixin, Base):
    __tablename__ = "table_embeddings"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stats_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSONB, nullable=False)


class ColumnStatsSketch(Base):
    """Per-column Puffin-sidecar sketch record (FORMATS '26 / arXiv:2605.30308).

    Each row is one ``(column, sketch_type, blob)`` triple emitted by the scanner. The
    Puffin blob itself lives on object storage; we keep only the locator + size on disk so
    the API endpoint can pull bytes back on demand.
    """

    __tablename__ = "column_stats_sketch"
    __table_args__ = (Index("idx_css_table_column", "table_id", "column_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    column_name: Mapped[str] = mapped_column(Text, nullable=False)
    sketch_type: Mapped[str] = mapped_column(Text, nullable=False)
    puffin_path: Mapped[str] = mapped_column(Text, nullable=False)
    puffin_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    puffin_size: Mapped[int] = mapped_column(Integer, nullable=False)
    field_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_from: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    scan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class SilentDataLossEvent(Base):
    """A detected silent-data-loss condition for a table (arXiv:2604.20081).

    The scheduler writes one row per detection event. ``orphan_data_files`` lists files
    that exist on S3 but are not referenced by any committed snapshot; ``missing_files``
    lists files referenced by committed snapshots that no longer exist on S3. The
    ``repaired_at`` + ``audit_log_id`` columns are populated by the manual repair endpoint.
    """

    __tablename__ = "silent_data_loss_events"
    __table_args__ = (Index("idx_silent_data_loss_table_time", "table_id", "detected_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    orphan_data_files: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    orphan_size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    missing_files: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    repaired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    audit_log_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cryo.audit_log.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AWSCostInsightConfig(TimestampMixin, Base):
    """Per-source configuration for real (CUR / Inventory-based) AWS cost attribution.

    Deliberately separate from ``AWSIntegration`` (which is the free-tier CloudWatch + Pricing
    List metrics integration) so the policy guarantee on that table is preserved. CUR is free
    delivery to a bucket you own; S3 Inventory is a free daily report; both differ in access
    pattern (delivered files vs. live API calls) and require one-time billing-admin setup.
    See ``cryo/integrations/aws_cost_insights.py``.
    """

    __tablename__ = "aws_cost_insight_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # CUR delivery location — the bucket AWS writes CUR reports to. Reads happen via
    # s3:GetObject on the user's own CUR bucket; CUR itself is a free delivery (the user pays
    # only S3 storage for the delivered files).
    cur_bucket: Mapped[str | None] = mapped_column(Text)
    cur_prefix: Mapped[str | None] = mapped_column(Text)
    cur_report_name: Mapped[str | None] = mapped_column(Text)
    # Optional CUR path override for testing against parquet/CSV fixtures without real AWS.
    # When set, the ingestor reads this file instead of listing the bucket — invaluable for
    # dev/CI. Stored as a plain path; not a secret.
    fixture_path: Mapped[str | None] = mapped_column(Text)
    # S3 Inventory configuration — optional second signal for per-prefix (per-table) sizing.
    inventory_bucket: Mapped[str | None] = mapped_column(Text)
    inventory_prefix: Mapped[str | None] = mapped_column(Text)
    inventory_format: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'CSV'")
    )
    access_key_id: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_ingested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_ingested_through: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_ingest_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_ingest_error: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="aws_cost_insight_config")


class AWSCurLineItem(Base):
    """One normalized AWS CUR line item — partitioned by usage_date for efficient range scans.

    Storing one row per ``lineItem/lineItemId`` means a full month of CUR can be tens of millions
    of rows; the ``(source_id, usage_date)`` composite index makes daily/bucket aggregations a
    single index seek + range scan. Cost values are stored as ``NUMERIC(18,8)`` because AWS
    bills to 8 decimal places.
    """

    __tablename__ = "aws_cur_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    usage_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    usage_type: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    unblended_cost: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    usage_quantity: Mapped[float | None] = mapped_column(Numeric(18, 8))
    usage_unit: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_cur_source_date", "source_id", "usage_date"),
        Index("idx_cur_bucket_date", "bucket", "usage_date"),
    )


class AWSInventoryObject(Base):
    """A normalized S3 Inventory row — one per object — joined with table prefix on read.

    Inventory reports deliver ``Bucket``, ``Key``, ``Size``, ``LastModifiedDate`` and
    ``StorageClass`` per object; we keep the raw row plus a denormalized ``table_prefix``
    (extracted from ``Key`` at ingest time) so per-table cost attribution is a simple
    ``GROUP BY table_prefix``.
    """

    __tablename__ = "aws_inventory_objects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    inventory_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_class: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    table_prefix: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_inv_source_date", "source_id", "inventory_date"),
        Index("idx_inv_bucket_key", "bucket", "key"),
        Index("idx_inv_prefix", "source_id", "table_prefix"),
    )


class S3BucketLogConfig(TimestampMixin, Base):
    """Per-bucket S3 server access-log ingestion config (``docs/s3-request-cost-tracking.md`` §6.2).

    One row per ``(source_id, bucket)``. The bucket is *discovered* via §4.1 (``SELECT
    DISTINCT bucket FROM tables WHERE source_id = X``) — never hand-typed into a config
    block — so the table stays self-maintaining as tables are added or moved between
    buckets. ``logging_status`` records the most recent ``GetBucketLogging`` response:
    ``enabled`` (already logging), ``disabled`` (turned off, can auto-provision §4.3),
    or ``unknown`` (most recent check failed / permission denied). ``manual_override``
    is True when the user typed the target bucket/prefix themselves (§4.4) — the
    teardown path in §9.4 needs this to know which statements on the target bucket's
    policy Cryo added vs. pre-existed.

    NOTE: this is the third per-source, bucket-scoped AWS config table alongside
    ``AWSIntegration`` and ``AWSCostInsightConfig``. Three is a pattern; see the
    "schema-convergence" note in §6.2 of the design doc.

    Credentials: deliberately the same ``access_key_id``/``secret_ref`` shape as
    ``AWSIntegration``/``AWSCostInsightConfig`` (§6.2 of the design doc), rather than an
    implicit "borrow whichever other AWS config row this source happens to have" — a source
    can have zero, one, or both of those other rows, and guessing which one's credentials
    are scoped correctly for ``GetBucketLogging``/``PutBucketLogging``/``PutBucketPolicy``
    would be a silent correctness trap. ``None`` here means "not yet configured" (falls
    through to §4.4 manual entry with no auto-detect), not "borrow from elsewhere."
    """

    __tablename__ = "s3_bucket_log_configs"
    __table_args__ = (
        UniqueConstraint("source_id", "bucket", name="uq_s3_log_config_source_bucket"),
        Index("ix_s3_log_config_source", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text)
    # Same access-key + encrypted-secret-reference shape as AWSIntegration/AWSCostInsightConfig
    # (see class docstring) -- explicit per-row credentials, never implicitly borrowed.
    access_key_id: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    logging_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    log_target_bucket: Mapped[str | None] = mapped_column(Text)
    log_target_prefix: Mapped[str | None] = mapped_column(Text)
    manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Statement Sid(s) Cryo inserted on the target bucket's policy during §4.3.1
    # auto-provisioning. The teardown path (§9.4) uses these to delete exactly the
    # statements it added — never touching statements Cryo didn't insert.
    cryo_added_policy_sids: Mapped[list[str] | None] = mapped_column(JSONB)
    last_checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_ingested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_ingested_date: Mapped[date | None] = mapped_column(Date)
    # Per-bucket ingestion error message, surfaced in the Source detail UI per §9.3. A
    # ``None`` here is "last run succeeded" — never leave stale error text behind.
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class S3RequestCostDaily(TimestampMixin, Base):
    """Per-bucket, per-day, per-table, per-operation S3 request counts and cost.

    One row per ``(source_id, bucket, table_id, day, operation_tier)`` — UPSERTed
    every ingestion run (§5 step 5), with re-aggregation of the trailing 2–3 day window
    so the same ``day`` may be updated in place rather than appended to.

    This table is the **bounded, retained-window** audit trail behind the rolling-30-day
    summary fields on ``TableCost`` (``get_requests_30d`` etc.) -- ``TableCost`` is what UI
    reads (fast, one row per table, matches the design doc's flat-footprint goal exactly),
    this table is what ingestion writes to and what a retention job prunes (see
    ``prune_s3_request_cost_daily`` in ``cryo/scanner/cost.py``, default 35-day retention --
    a few days more than the 30-day rollup window so a delayed rollup run never reads a
    just-pruned day). At 5k tables and a 35-day retention window that's ~175k rows tops,
    not the unbounded-with-all-history alternative.

    Note: ``operation_tier`` is one of ``"get"``, ``"put"``, ``"list"``, ``"head"``, ``"other"``
    -- finer-grained than AWS's own Price List SKU grouping (``Requests-Tier1`` = PUT/COPY/
    POST/LIST, ``Requests-Tier2`` = GET/SELECT/HEAD) so the rollup onto ``TableCost`` can still
    split into distinct ``get_requests_30d``/``put_requests_30d``/``list_requests_30d`` counts
    for the UI (§9.2) -- collapsing GET and LIST into one tier at ingestion time would lose that
    distinction permanently. Pricing still uses the AWS tier grouping: mapped from this column
    at cost-computation time, not stored redundantly here (see
    ``cryo/scanner/s3_request_cost_ingest.py``'s ``_PRICING_TIER``).
    """

    __tablename__ = "s3_request_cost_daily"
    __table_args__ = (
        # NOTE for whoever writes the P1 ingestion UPSERT: Postgres treats every NULL as
        # distinct for uniqueness purposes, so this constraint does *not* dedupe rows where
        # `table_id IS NULL` (log entries under this bucket/day that matched no known table
        # prefix -- a real case, not hypothetical, e.g. metadata paths or not-yet-scanned
        # tables). Re-aggregating the trailing window (§5 step 3) would append duplicate
        # unmatched-table rows each run rather than upserting them. The ingestion writer
        # should either exclude `table_id IS NULL` rows from the upsert (aggregate them
        # separately, not per-day) or delete-then-insert that day's unmatched rows instead of
        # relying on `ON CONFLICT` for them specifically.
        UniqueConstraint(
            "source_id",
            "bucket",
            "table_id",
            "day",
            "operation_tier",
            name="uq_s3_request_cost_daily_row",
        ),
        Index(
            "ix_s3_request_cost_daily_table_day",
            "table_id",
            "day",
        ),
        Index(
            "ix_s3_request_cost_daily_source_bucket_day",
            "source_id",
            "bucket",
            "day",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="SET NULL"),
        nullable=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    operation_tier: Mapped[str] = mapped_column(Text, nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    # Cost in USD for that day. Filled in at ingestion time using the live price
    # returned by ``fetch_s3_request_prices()``; falls back to a per-call default if
    # the live fetch returned None (which the design doc says must be logged + flagged
    # in the UI as approximate — see §6.4).
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    # Pricing source: ``"live"`` (from ``fetch_s3_request_prices``) or
    # ``"fallback_default"`` (settings constant). The UI uses this to render the
    # "approximate" label per §6.4 — never let a fallback-derived number look the
    # same as a live-fetched one.
    pricing_source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fallback_default'")
    )


class PartitionAccessDaily(TimestampMixin, Base):
    """Per-table, per-day, per-partition S3 GET request counts, keyed also by requester role.

    docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md -- inspired by Yelp's engineering blog
    post on the same idea (S3 access logs -> partition-key-vs-time scatter plot -> storage
    class/retention decisions). Deliberately a **separate** table from ``S3RequestCostDaily``
    rather than adding a ``partition_path`` column there: that table's whole read path
    (``recompute_table_cost_rollup``, ``TableCost``) is table-day granularity by design, and
    adding partition cardinality to it would blow up every existing cost query's row count for
    no benefit to cost reporting -- the same reasoning ``QueryCostAttributionDaily``'s own
    docstring uses to justify being its own table rather than extending an existing one.

    ``partition_path`` here is the raw Hive-style path fragment as it actually appears in the
    S3 key (e.g. ``"dt=2026-08-19/"``, slash- and ``=``-delimited, URL-decoded) -- NOT the same
    string as ``TablePartition.partition_path`` (that column is pipe-delimited
    ``"col=value|col2=value2"``, built by ``cryo.scanner.analyze.partition_key`` for a
    different purpose, planner-facing partition identity). ``cryo.scanner.
    s3_partition_match.match_partition_path`` is what reconciles the two: it parses this raw
    path into ``{column: value}`` pairs and matches them against ``TablePartition.
    partition_values`` (the structured JSONB dict, not the pipe-delimited string) for that
    table's latest scan. A key whose path doesn't match any currently-known partition still
    gets a row here with whatever raw path was parsed (partition scanned away since, or a
    scan that hasn't run yet) -- unmatched is a real, visible state, not a dropped row.

    ``operation`` is filtered to GET at ingestion time (see ``s3_request_cost_ingest.
    _operation_tier`` for the general tiering; this table only cares about the read side,
    same as Yelp's ``REST.GET.OBJECT`` filter) -- PUT/write events don't inform "is this
    partition still being read", which is the whole point of this table.

    Same bounded/retained-window shape as ``S3RequestCostDaily`` -- see
    ``cryo.scanner.s3_request_cost_ingest.prune_partition_access_daily``.
    """

    __tablename__ = "partition_access_daily"
    __table_args__ = (
        # Mirrors S3RequestCostDaily's own NOTE: Postgres treats NULL as distinct for
        # UNIQUE purposes, so requester_role IS NULL rows (log lines with no parseable
        # principal) don't dedupe via ON CONFLICT -- the ingestion writer uses the same
        # select-then-update-or-insert pattern ingest_bucket() already uses for that reason.
        UniqueConstraint(
            "table_id",
            "day",
            "partition_path",
            "requester_role",
            name="uq_partition_access_daily_row",
        ),
        Index("ix_partition_access_daily_table_day", "table_id", "day"),
        Index("ix_partition_access_daily_table_partition", "table_id", "partition_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    # Raw Hive-style path fragment, e.g. "dt=2026-08-19/" -- "" for an unpartitioned table's
    # data files, distinct from NULL (NULL isn't used here; see the UniqueConstraint note
    # above for why a sentinel beats NULL for this particular column).
    partition_path: Mapped[str] = mapped_column(Text, nullable=False)
    requester_role: Mapped[str | None] = mapped_column(Text)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class PartitionLastAccess(TimestampMixin, Base):
    """Durable last-read day per (table, partition path).

    ``PartitionAccessDaily`` is pruned at 35 days, so "no read in 90 days" cannot be proven from
    it. This keeps one row per partition and is only ever advanced by log ingestion
    (``cryo.scanner.s3_request_cost_ingest._upsert_partition_access``); it is not pruned by age.
    """

    __tablename__ = "partition_last_access"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    partition_path: Mapped[str] = mapped_column(Text, primary_key=True)
    first_read_day: Mapped[date] = mapped_column(Date, nullable=False)
    last_read_day: Mapped[date] = mapped_column(Date, nullable=False)


class PartitionStorageClass(Base):
    """Storage class Cryo last moved a partition's live data files to.

    Written only by archive and restore jobs (``cryo.tiering.archive_jobs``). Absence means
    Cryo never moved the partition, not that its files are in Standard.
    """

    __tablename__ = "partition_storage_classes"

    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    partition_key: Mapped[str] = mapped_column(Text, primary_key=True)
    storage_class: Mapped[str] = mapped_column(Text, nullable=False)
    objects: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ArchiveJob(Base):
    """One archive or restore request: moving partitions' data files between S3 classes."""

    __tablename__ = "archive_jobs"
    __table_args__ = (Index("ix_archive_jobs_table_created", "table_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_class: Mapped[str] = mapped_column(Text, nullable=False)
    partition_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    objects_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    objects_moved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    objects_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    objects_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    bytes_moved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class QueryCostAttributionDaily(Base):
    """Per-day, per-client, per-schema rollup of real Trino query scan bytes -- a "who/what is
    driving scan cost" report, built entirely from data Trino's own query-completion event
    already carries (``inputs_json``'s ``physicalInputBytes``, the ``source`` client string),
    read through the same path as ``cryo.telemetry.iceberg_source``'s selectivity extraction.

    Coarse by design: (day, client, schema_name), not per-table or per-query -- Trino's own
    query-log table (e.g. ``trino_logs.trino_queries``, wired per-source via
    ``Source.config["query_log"]``) already *is* the durable per-query audit trail; this table
    exists to answer the aggregate cost-attribution question quickly without re-scanning that
    log on every report request. Same bounded/retained-window shape as ``S3RequestCostDaily``
    -- see ``cryo.telemetry.cost_attribution.prune_query_cost_attribution``.
    """

    __tablename__ = "query_cost_attribution_daily"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "day",
            "client",
            "schema_name",
            "user_name",
            name="uq_query_cost_attribution_key",
        ),
        Index("ix_query_cost_attribution_day", "day"),
        Index("ix_query_cost_attribution_client", "client"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.sources.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    client: Mapped[str] = mapped_column(Text, nullable=False)
    schema_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Trino query-completion event's "user" field, read via IcebergQueryLogConfig.user_column
    # (default "user"; None/False disables). Rows ingested before 20260819_0045 default to
    # "unknown" -- same fallback _normalize_client uses -- since the (day, client, schema_name)
    # rollup that predates this column has no way to recover the true user split retroactively.
    user_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    query_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    failed_query_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    physical_bytes_scanned: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    estimated_scan_cost_usd: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class QPPObservation(Base):
    """Variance-aware QPP observation row (CLOUD 2026, arXiv:2606.03464).

    Holds the (features, runtime) pairs that the :class:`QPPModel` linear regression
    trainer consumes. ``features`` is a JSON blob of the :class:`QPPFeatures` fields; the
    dedicated float columns are the most-frequently-queried ones so we can index and
    aggregate them without re-parsing JSONB.
    """

    __tablename__ = "qpp_observations"
    __table_args__ = (Index("idx_qpp_table_time", "table_id", "observed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    observed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    table_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    features: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cotenant_load_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_cached: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Skillset(TimestampMixin, Base):
    """Versioned AI skillset row (paper arXiv:2606.01185).

    A *skillset* is a named bundle of prompt templates plus an ordered list of
    tool definitions. ``metrics`` is a JSONB blob carrying rolling outcome
    stats (acceptance rate, mean score, sample count) updated whenever a
    :class:`SkillsetOutcome` is recorded.
    """

    __tablename__ = "skillsets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_skillsets_name_version"),
        Index("ix_skillsets_name", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    prompts: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tools: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metrics: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class SkillsetOutcome(Base):
    """One observed outcome of a skillset recommendation."""

    __tablename__ = "skillset_outcomes"
    __table_args__ = (
        Index("ix_skillset_outcomes_skillset_recorded", "skillset_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skillset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.skillsets.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id")
    )
    prompt_name: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)


class LLMObservation(Base):
    """Durable telemetry for every LLM invocation."""

    __tablename__ = "llm_observations"
    __table_args__ = (
        Index("ix_llm_observations_recorded_at", "recorded_at"),
        Index("ix_llm_observations_provider_day", "provider_type", "recorded_at"),
        Index("ix_llm_observations_skillset", "skillset_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.ai_providers.id")
    )
    provider_type: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    feature: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_name: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    skillset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.skillsets.id")
    )
    skillset_name: Mapped[str | None] = mapped_column(Text)
    skillset_version: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cryo.runs.id"))
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id")
    )
    outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.skillset_outcomes.id")
    )
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'success'"))
    budget_denied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    error: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[JsonDict | None] = mapped_column(JSONB)
    principal_subject: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RunFailureDiagnosis(Base):
    """Persisted run-failure diagnosis result + operator feedback."""

    __tablename__ = "run_failure_diagnoses"
    __table_args__ = (
        Index("ix_run_failure_diagnoses_run", "run_id", "diagnosed_at"),
        Index("ix_run_failure_diagnoses_job", "job_id", "diagnosed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.runs.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cryo.jobs.id"))
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id")
    )
    skillset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.skillsets.id")
    )
    diagnosis_class: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    summary: Mapped[str | None] = mapped_column(Text)
    runbook_step: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    raw_response: Mapped[JsonDict | None] = mapped_column(JSONB)
    feedback: Mapped[str | None] = mapped_column(Text)
    feedback_note: Mapped[str | None] = mapped_column(Text)
    diagnosed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    feedback_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class TableSimilarityPair(Base):
    """One scored candidate pair from the table-deduplication pipeline
    (docs/TABLE_DEDUPLICATION_DESIGN.md). A row exists only for pairs that survived Stage 1
    blocking -- this table is never the full n^2 cross product, only candidates.

    ``table_a_id``/``table_b_id`` are stored with a < b ordering (by string comparison of
    the UUIDs) enforced at write time, not by a DB constraint, so a pair is never inserted
    twice in both directions -- see cryo.intelligence.dedup.pairing.canonical_pair_order.

    ``score_components`` is the full breakdown behind ``similarity_score`` (schema
    Jaccard, column-stat cosine similarity, MinHash row-Jaccard estimate, naming-pattern
    boost, file-overlap flag, etc) -- kept as JSON rather than separate columns because the
    signal set is still evolving per the design doc's staged-confidence approach, and
    because every recommendation surfaced to an operator must show its evidence (design
    doc SS7/SS9: never a bare numeric score for something that can end in a DROP TABLE).
    """

    __tablename__ = "table_similarity_pairs"
    __table_args__ = (
        UniqueConstraint("table_a_id", "table_b_id", name="uq_table_similarity_pair"),
        Index("ix_table_similarity_pairs_table_a", "table_a_id"),
        Index("ix_table_similarity_pairs_table_b", "table_b_id"),
        Index("ix_table_similarity_pairs_score", "similarity_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    table_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    table_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    # Stage reached: "blocked" (Stage 1 candidate only, no score yet), "scored" (Stage 2
    # statistical composite computed), "verified" (Stage 3 exact Trino comparison ran).
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'blocked'"))
    similarity_score: Mapped[float | None] = mapped_column(Float)
    schema_similarity: Mapped[float | None] = mapped_column(Float)
    stats_similarity: Mapped[float | None] = mapped_column(Float)
    row_jaccard_estimate: Mapped[float | None] = mapped_column(Float)
    naming_pattern_boost: Mapped[float | None] = mapped_column(Float)
    file_overlap_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Stage 3 outcome, when run: "exact_duplicate" | "near_duplicate" | "contains_a_in_b" |
    # "contains_b_in_a" | "unrelated" (score looked high but Stage 3 disconfirmed it) | NULL
    # (Stage 3 never ran). Distinguishing containment direction matters -- design doc SS9:
    # dropping the superset when only the subset is read is a real risk.
    verified_relationship: Mapped[str | None] = mapped_column(Text)
    exact_row_overlap_pct: Mapped[float | None] = mapped_column(Float)
    score_components: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table_a: Mapped[Table] = relationship(foreign_keys=[table_a_id])
    table_b: Mapped[Table] = relationship(foreign_keys=[table_b_id])


class TableSimilarityCluster(Base):
    """Connected-components grouping of TableSimilarityPair edges above threshold
    (design doc SS6). Recomputed on each pipeline run, not incrementally maintained --
    cluster membership can change (chaining, splitting) as new pairs are scored, so each
    run replaces the prior generation's rows for a clean, explainable snapshot rather than
    trying to diff/patch cluster assignments in place.
    """

    __tablename__ = "table_similarity_clusters"
    __table_args__ = (
        Index("ix_table_similarity_clusters_cluster", "cluster_id"),
        Index("ix_table_similarity_clusters_table", "table_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.tables.id", ondelete="CASCADE"), nullable=False
    )
    # Highest-scoring recommendation role within the cluster: "keep" (canonical member) or
    # "drop_candidate". Never "drop" -- design doc SS7: this pipeline recommends, an
    # operator confirms, nothing here auto-deletes.
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'drop_candidate'"))
    keep_score: Mapped[float | None] = mapped_column(Float)
    keep_reason: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    table: Mapped[Table] = relationship()


class SharedS3AnomalyReport(TimestampMixin, Base):
    """Latest (and historical) snapshots of shared/nested S3 warehouse path collisions.

    Harvest is expensive against production HMS, so the Anomalies page reads the latest
    row instead of listing catalogs on every page load. A new row is written on refresh
    and on the 10:00 IST scheduler tick.
    """

    __tablename__ = "shared_s3_anomaly_reports"
    __table_args__ = (Index("ix_shared_s3_anomaly_reports_generated_at", "generated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    group_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    overlapping_table_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sources_scanned: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_errors: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    groups: Mapped[JsonList] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class SavedExplorerView(Base):
    """Named catalog filter owned by one principal. Not a schedule."""

    __tablename__ = "saved_explorer_views"
    __table_args__ = (UniqueConstraint("owner_subject", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_subject: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sort: Mapped[str | None] = mapped_column(Text)
    group_by: Mapped[str | None] = mapped_column(Text)
    savings_view: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        Index("ix_assistant_conversations_owner_updated", "owner_subject", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    owner_subject: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    context: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    share_token_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_assistant_messages_client"
        ),
        UniqueConstraint("conversation_id", "seq", name="uq_assistant_messages_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    blocks: Mapped[JsonList | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_sa_text("'streaming'")
    )
    verified_handler: Mapped[str | None] = mapped_column(Text)
    grounding: Mapped[str | None] = mapped_column(Text)
    client_message_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class AssistantToolCall(Base):
    __tablename__ = "assistant_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.assistant_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[JsonDict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error_category: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantEvidence(Base):
    __tablename__ = "assistant_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.assistant_tool_calls.id", ondelete="SET NULL")
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    scope: Mapped[JsonDict | None] = mapped_column(JSONB)
    observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    coverage: Mapped[JsonDict | None] = mapped_column(JSONB)
    source_refs: Mapped[JsonList | None] = mapped_column(JSONB)
    result: Mapped[JsonDict | None] = mapped_column(JSONB)
    result_digest: Mapped[str | None] = mapped_column(Text)


class AssistantActionProposal(Base):
    __tablename__ = "assistant_action_proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.assistant_conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cryo.assistant_messages.id", ondelete="SET NULL")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    owner_subject: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    arguments: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    preview: Mapped[JsonDict | None] = mapped_column(JSONB)
    plan_hash: Mapped[str | None] = mapped_column(Text)
    target_digest: Mapped[str | None] = mapped_column(Text)
    selection: Mapped[JsonDict | None] = mapped_column(JSONB)
    required_role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cryo.runs.id"))
    job_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantAuthorization(Base):
    __tablename__ = "assistant_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.assistant_action_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_subject: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    authorized_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantFeedback(Base):
    __tablename__ = "assistant_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cryo.assistant_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_subject: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantSettingsRow(Base):
    __tablename__ = "assistant_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    allowed_min_role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'viewer'")
    )
    enabled_tiers: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[0]'::jsonb")
    )
    send_row_values: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("90"))
    team_instructions_skillset: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class LocalUser(Base):
    """A password or Google account that can sign in to the Cryo UI."""

    __tablename__ = "local_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'viewer'"))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AuthSettings(Base):
    """Singleton sign-in options edited from Settings. Secrets are not stored here."""

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    password_login_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    google_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    google_client_id: Mapped[str | None] = mapped_column(Text)
    google_hosted_domain: Mapped[str | None] = mapped_column(Text)
    google_auto_provision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class AssistantApiKey(Base):
    __tablename__ = "assistant_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scopes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[\"read\"]'::jsonb")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


__all__ = [
    "AIProvider",
    "AWSCostInsightConfig",
    "AWSCurLineItem",
    "AWSInventoryObject",
    "AnalysisTask",
    "AuditLog",
    "ColumnStatsSketch",
    "DeletionTracking",
    "Destination",
    "FleetImpactHistory",
    "GovernorBanditState",
    "IncidentCohort",
    "IncidentCohortMember",
    "LayoutRecommendation",
    "LLMObservation",
    "MaintenanceJob",
    "MaintenanceRun",
    "Preset",
    "PresetRule",
    "ProtectedSnapshot",
    "QPPObservation",
    "QualityConstraint",
    "QualityConstraintResult",
    "QueryLog",
    "QueryPattern",
    "RunFailureDiagnosis",
    "SavedExplorerView",
    "S3BucketLogConfig",
    "S3RequestCostDaily",
    "SilentDataLossEvent",
    "Skillset",
    "SkillsetOutcome",
    "SlackConfig",
    "SharedS3AnomalyReport",
    "Source",
    "Table",
    "TableAssignment",
    "TableColumn",
    "TableCost",
    "TableCostHistory",
    "TableEmbedding",
    "TablePartition",
    "TableSimilarityCluster",
    "TableSimilarityPair",
    "TableStats",
    "TableStatsHistory",
    "TierPolicyRecord",
    "AssistantConversation",
    "AssistantMessage",
    "AssistantToolCall",
    "AssistantEvidence",
    "AssistantActionProposal",
    "AssistantAuthorization",
    "AssistantFeedback",
    "AssistantSettingsRow",
    "AssistantApiKey",
    "AuthSettings",
    "LocalUser",
]
