from __future__ import annotations

import uuid
from datetime import datetime
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
    func,
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

    source: Mapped[Source] = relationship(back_populates="tables")
    stats: Mapped[TableStats | None] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    partitions: Mapped[list[TablePartition]] = relationship(
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
    delete_file_pct: Mapped[float | None] = mapped_column(Float)
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
    last_compacted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    input_files_last_compaction: Mapped[int | None] = mapped_column(
        Integer
    )  # effectiveness ratio tracking
    output_files_last_compaction: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[JsonDict | None] = mapped_column(JSONB)

    table: Mapped[Table] = relationship(back_populates="partitions")


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
    verifier_version: Mapped[str | None] = mapped_column(Text)
    verifier_violations: Mapped[JsonList | None] = mapped_column(JSONB)

    run: Mapped[MaintenanceRun] = relationship(back_populates="jobs")
    table: Mapped[Table] = relationship(back_populates="jobs")
    destination: Mapped[Destination] = relationship(back_populates="jobs")
    audit_entries: Mapped[list[AuditLog]] = relationship(back_populates="job")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_table_ts", "table_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
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
        UniqueConstraint("name", name="uq_skillsets_name"),
        Index("ix_skillsets_name", "name", unique=True),
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


__all__ = [
    "AIProvider",
    "AWSCostInsightConfig",
    "AWSCurLineItem",
    "AWSInventoryObject",
    "AuditLog",
    "ColumnStatsSketch",
    "Destination",
    "GovernorBanditState",
    "LayoutRecommendation",
    "MaintenanceJob",
    "MaintenanceRun",
    "Preset",
    "PresetRule",
    "ProtectedSnapshot",
    "QPPObservation",
    "QueryLog",
    "QueryPattern",
    "SilentDataLossEvent",
    "Skillset",
    "SkillsetOutcome",
    "SlackConfig",
    "Source",
    "Table",
    "TableAssignment",
    "TableCost",
    "TableCostHistory",
    "TableEmbedding",
    "TablePartition",
    "TableStats",
    "TableStatsHistory",
    "TierPolicyRecord",
]
