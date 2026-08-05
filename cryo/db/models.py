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
    __table_args__ = (Index("ix_jobs_run_status", "run_id", "status"),)

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
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    engine_ref: Mapped[str | None] = mapped_column(Text)
    replica: Mapped[str | None] = mapped_column(Text)
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
    detail: Mapped[JsonDict | None] = mapped_column(JSONB)  # Additional analysis details
    bloom_filter_columns: Mapped[JsonList | None] = mapped_column(
        JSONB
    )  # Bloom filter recommendations

    table: Mapped[Table] = relationship()


class GovernorBanditState(TimestampMixin, Base):
    __tablename__ = "governor_bandit_state"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))


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


__all__ = [
    "AIProvider",
    "AWSCostInsightConfig",
    "AWSCurLineItem",
    "AWSInventoryObject",
    "AuditLog",
    "ColumnStatsSketch",
    "DeletionTracking",
    "Destination",
    "GovernorBanditState",
    "LayoutRecommendation",
    "LLMObservation",
    "MaintenanceJob",
    "MaintenanceRun",
    "Preset",
    "PresetRule",
    "ProtectedSnapshot",
    "QPPObservation",
    "QueryLog",
    "QueryPattern",
    "RunFailureDiagnosis",
    "S3BucketLogConfig",
    "S3RequestCostDaily",
    "SilentDataLossEvent",
    "Skillset",
    "SkillsetOutcome",
    "SlackConfig",
    "Source",
    "Table",
    "TableAssignment",
    "TableColumn",
    "TableCost",
    "TableCostHistory",
    "TableEmbedding",
    "TablePartition",
    "TableStats",
    "TableStatsHistory",
    "TierPolicyRecord",
]
