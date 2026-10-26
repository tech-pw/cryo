from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from cryo.api.redact import redact_sensitive_dict
from cryo.domain.enums import CostPrecision, EngineType, Mode, OperationType
from cryo.engines.spark.k8s_keys import is_spark_k8s_destination, validate_spark_k8s_config
from cryo.engines.trino.catalog_bindings import parse_catalog_bindings, validate_catalog_bindings

JsonDict = dict[str, Any]

_NATIVE_ENGINE_IMPLS = frozenset({"go", "rust"})


def validate_native_engine_config(engine: EngineType | str, config: Mapping[str, Any]) -> None:
    if engine != EngineType.NATIVE:
        return
    impl = config.get("engine_impl")
    if impl is None:
        return
    if not isinstance(impl, str):
        raise ValueError("config.engine_impl must be a string for native engines")
    normalized = impl.strip().lower()
    if normalized not in _NATIVE_ENGINE_IMPLS:
        raise ValueError("config.engine_impl must be 'go' or 'rust' for native engines")


def validate_destination_config(engine: EngineType | str, config: Mapping[str, Any]) -> None:
    """Validate engine-specific destination config (native impl, Trino catalog bindings,
    Spark-on-Kubernetes settings)."""
    validate_native_engine_config(engine, config)
    validate_catalog_bindings(engine, config)
    if is_spark_k8s_destination(engine, config):
        validate_spark_k8s_config(config)


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceCreate(BaseModel):
    name: str
    type: str
    config: JsonDict = Field(default_factory=dict)
    secret_ref: str | None = None
    enabled: bool = True
    # Empty = all namespaces in the catalog are eligible for scan + maintenance.
    maintenance_schema_names: list[str] = Field(default_factory=list)


class SourceRead(OrmModel):
    """Source read schema with sensitive data redacted."""

    id: uuid.UUID
    name: str
    type: str
    config: JsonDict = Field(default_factory=dict)
    # NEVER return secret_ref in API responses
    enabled: bool
    maintenance_schema_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_serializer("config")
    def redact_config(self, config: JsonDict) -> JsonDict:
        """Redact sensitive keys in config before serialization."""
        if config is None:
            return {}
        try:
            return redact_sensitive_dict(config)
        except Exception:
            return {}


class SourcePatch(BaseModel):
    name: str | None = None
    type: str | None = None
    config: JsonDict | None = None
    secret_ref: str | None = None
    enabled: bool | None = None
    maintenance_schema_names: list[str] | None = None


class SourceTestRequest(BaseModel):
    """A not-yet-saved source config, so the UI can verify a connection before creating it."""

    type: str
    config: JsonDict = Field(default_factory=dict)
    # Set when testing an *edit* of an existing source -- the client only ever sees a redacted
    # config (see cryo/api/redact.py), so any field still holding the "***REDACTED***" placeholder
    # gets swapped back in for the real stored value before testing.
    source_id: uuid.UUID | None = None


class SourceTestResponse(BaseModel):
    ok: bool
    namespaces: list[str] = Field(default_factory=list)
    # No longer populated by test_source: enumerating every table in every namespace is an
    # O(namespaces) round-trip and can take minutes against a real Hive Metastore -- unsuitable
    # for a "Test connection" click. Table discovery happens via the async Scanner instead.
    # Kept (always empty) rather than removed, so older frontend builds don't break on the
    # response shape.
    table_count: int = 0
    # Warehouse root inferred from where the catalog's databases actually live, so the UI
    # can fill the field instead of letting an operator invent a value. A connection test
    # passes without a warehouse (listing namespaces doesn't need one) but every scan then
    # fails with "source config missing warehouse", so a wrong-but-present value is the
    # worst outcome -- see routes.test_source.
    suggested_warehouse: str | None = None
    sample_tables: list[str] = Field(default_factory=list)
    error: str | None = None
    note: str | None = None


class SourceNamespacesResponse(BaseModel):
    """Live namespace list for an already-saved source -- same cheap list_namespaces() call
    test_source uses, so the scan-scope picker can offer real schema names to target even before
    a first full scan has ever run."""

    ok: bool
    namespaces: list[str] = Field(default_factory=list)
    error: str | None = None


class SourceStorageDiscoveryRequest(BaseModel):
    """One slice of a bucket-discovery walk over a not-yet-saved catalog config.

    Paged because the walk reads table locations (see ``discover_source_storage`` for why
    database-level is not accurate enough), which is minutes on a large metastore. The
    client advances ``offset`` by the returned ``scanned`` until it reaches ``total``.
    """

    type: str
    config: JsonDict = Field(default_factory=dict)
    source_id: uuid.UUID | None = None
    offset: int = 0
    # Databases per request. Small enough that first buckets appear within a couple of
    # seconds, large enough not to spend the whole walk on HTTP round trips.
    limit: int = 25


class DiscoveredBucket(BaseModel):
    """One object-store bucket the catalog's tables live in, with how much is in it.

    ``tables``/``schemas`` are what make the list usable: a real catalog's buckets are not
    equal partners. Measured on a 251-schema metastore, four buckets held 99.5% of tables
    while eight were long-tail noise (dev leakage, ``-audit`` siblings, a POC bucket with
    one table). Counts let the UI rank them instead of presenting twelve identical chips.

    ``access`` is the result of a read probe:
      - ``readable``      -- a list call succeeded
      - ``denied``        -- credentials reached S3 but aren't allowed to read it
      - ``missing``       -- the bucket does not exist (stale catalog location)
      - ``unknown``       -- not probed, or the probe itself failed for another reason
    Surfacing this during onboarding turns a silent first-scan failure into an answer the
    operator gets while they still have the credentials dialog in front of them.
    """

    name: str
    tables: int = 0
    schemas: int = 0
    access: str = "unknown"
    access_detail: str | None = None


class SourceStorageDiscoveryResponse(BaseModel):
    """Buckets the catalog's tables live in, for one slice of the database walk.

    Deliberately NOT part of test_source: locations are read per table (a database's own
    location is only a default most of its tables don't follow), which is far more round
    trips than the single call test_source is held to.

    ``scanned``/``total`` report how far the walk got, so a partial result -- a timeout, or
    a slice the catalog refused -- can say so instead of presenting an incomplete bucket
    list as if it were the whole picture.
    """

    ok: bool
    buckets: list[DiscoveredBucket] = Field(default_factory=list)
    scanned: int = 0
    total: int = 0
    error: str | None = None


class SourceNamespaceTablesResponse(BaseModel):
    """Live table names within one already-known namespace -- one cheap call
    (list_tables_in_namespace), not the O(all namespaces) walk a full scan does. Lets the
    scan-scope "Specific tables" picker offer real table names once a schema is chosen, even
    before this lakehouse has ever been scanned."""

    ok: bool
    tables: list[str] = Field(default_factory=list)
    error: str | None = None


class AWSIntegrationCreate(BaseModel):
    """Free-tier-only AWS S3 cost/usage integration for a lakehouse's bucket.

    Only ever calls AWS APIs that cost nothing: S3's default daily CloudWatch storage metrics
    (read via GetMetricStatistics, not the always-billed GetMetricData) and the free Price List
    API. See cryo/integrations/aws_s3_metrics.py.
    """

    bucket: str
    region: str
    access_key_id: str | None = None
    secret_access_key: str | None = None
    enabled: bool = True


class AWSIntegrationPatch(BaseModel):
    bucket: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    enabled: bool | None = None


class AWSIntegrationRead(OrmModel):
    id: uuid.UUID
    source_id: uuid.UUID
    bucket: str
    region: str
    access_key_id: str | None = None
    # secret_access_key is never returned, same as Source/Destination secret_ref
    enabled: bool
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    created_at: datetime
    updated_at: datetime


class AWSConnectionTestResponse(BaseModel):
    ok: bool
    account_id: str | None = None
    arn: str | None = None
    error: str | None = None


class AWSUsageResponse(BaseModel):
    bucket: str
    region: str
    measured_at: datetime | None = None
    size_bytes_by_storage_class: JsonDict = Field(default_factory=dict)
    total_size_bytes: int = 0
    object_count: int | None = None
    price_per_gb_month: float | None = None
    price_source: str | None = None


class AWSCostInsightConfigCreate(BaseModel):
    """Opt-in config for real CUR + S3 Inventory-based cost attribution.

    Separated from ``AWSIntegration`` because CUR and Inventory require billing-admin setup on
    the AWS side and the data comes from delivered report files, not live API calls. The
    existing ``AWSIntegration`` is the free-tier CloudWatch + Pricing List path; this is the
    CE-grade upgrade for organizations willing to pay one-time setup cost.
    """

    cur_bucket: str | None = None
    cur_prefix: str | None = None
    cur_report_name: str | None = None
    fixture_path: str | None = None
    inventory_bucket: str | None = None
    inventory_prefix: str | None = None
    inventory_format: str = "CSV"
    access_key_id: str | None = None
    secret_access_key: str | None = None
    enabled: bool = True


class AWSCostInsightConfigPatch(BaseModel):
    cur_bucket: str | None = None
    cur_prefix: str | None = None
    cur_report_name: str | None = None
    fixture_path: str | None = None
    inventory_bucket: str | None = None
    inventory_prefix: str | None = None
    inventory_format: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    enabled: bool | None = None


class AWSCostInsightConfigRead(OrmModel):
    id: uuid.UUID
    source_id: uuid.UUID
    cur_bucket: str | None = None
    cur_prefix: str | None = None
    cur_report_name: str | None = None
    fixture_path: str | None = None
    inventory_bucket: str | None = None
    inventory_prefix: str | None = None
    inventory_format: str = "CSV"
    access_key_id: str | None = None
    enabled: bool
    last_ingested_at: datetime | None = None
    last_ingested_through: datetime | None = None
    last_ingest_ok: bool | None = None
    last_ingest_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AWSCostIngestResponse(BaseModel):
    """Result of a CUR / Inventory ingest run."""

    rows_ingested: int
    files_processed: int
    through_date: date | None = None
    inventory_date: date | None = None
    error: str | None = None


class CostByBucketItem(BaseModel):
    bucket: str
    total_cost_usd: float
    line_count: int
    last_usage_date: str | None = None


class CostByBucketResponse(BaseModel):
    source_id: uuid.UUID
    start: date | None = None
    end: date | None = None
    total_cost_usd: float = 0
    items: list[CostByBucketItem] = Field(default_factory=list)


class CostDailySeriesItem(BaseModel):
    usage_date: date
    total_cost_usd: float


class CostDailySeriesResponse(BaseModel):
    source_id: uuid.UUID
    items: list[CostDailySeriesItem] = Field(default_factory=list)


class SizeByPrefixItem(BaseModel):
    table_prefix: str
    size_bytes: int
    object_count: int
    last_modified: str | None = None


class SizeByPrefixResponse(BaseModel):
    source_id: uuid.UUID
    inventory_date: date | None = None
    items: list[SizeByPrefixItem] = Field(default_factory=list)


class S3LogConfigRead(OrmModel):
    id: uuid.UUID
    source_id: uuid.UUID
    bucket: str
    region: str | None = None
    access_key_id: str | None = None
    logging_status: str
    log_target_bucket: str | None = None
    log_target_prefix: str | None = None
    manual_override: bool
    enabled: bool
    last_checked_at: datetime | None = None
    last_ingested_at: datetime | None = None
    last_ingested_date: date | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class S3LogConfigPatch(BaseModel):
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    log_target_bucket: str | None = None
    log_target_prefix: str | None = None
    manual_override: bool | None = None
    enabled: bool | None = None


class S3LogConfigCheckResponse(BaseModel):
    logging_status: str
    log_target_bucket: str | None = None
    log_target_prefix: str | None = None
    error: str | None = None


class S3LogConfigProbeResponse(BaseModel):
    ok: bool
    files_found: int
    truncated: bool
    error: str | None = None


class S3LogIngestResponse(BaseModel):
    bucket: str
    rows_ingested: int
    tables_matched: int
    unmatched_requests: int
    through_date: date | None = None
    error: str | None = None


class S3LogProvisionRequest(BaseModel):
    target_bucket: str | None = None


class S3LogProvisionResponse(BaseModel):
    ok: bool
    log_target_bucket: str | None = None
    log_target_prefix: str | None = None
    error: str | None = None


class S3LogTeardownRequest(BaseModel):
    revert_policy: bool = True


class S3LogTeardownResponse(BaseModel):
    ok: bool
    policy_reverted: bool = False
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class S3RequestCostByTierRead(BaseModel):
    tier1_count: int
    tier2_count: int
    tier1_cost_usd: float
    tier2_cost_usd: float


class S3RequestCostByDayRead(BaseModel):
    day: date
    count: int
    cost_usd: float


class S3RequestCostByBucketRead(BaseModel):
    bucket: str
    region: str | None = None
    enabled: bool
    logging_status: str
    last_ingested_at: datetime | None = None
    total_request_count_30d: int
    total_cost_usd_month: float
    by_tier: S3RequestCostByTierRead


class S3RequestCostRollupRead(BaseModel):
    bucket: str
    source_id: uuid.UUID
    source_name: str
    region: str | None = None
    enabled: bool
    logging_status: str
    last_ingested_at: datetime | None = None
    total_request_count_30d: int
    total_cost_usd_month: float
    by_tier: S3RequestCostByTierRead
    by_day: list[S3RequestCostByDayRead]
    by_bucket: list[S3RequestCostByBucketRead] = Field(default_factory=list)
    approximate: bool


class DestinationCreate(BaseModel):
    name: str
    engine: EngineType
    config: JsonDict = Field(default_factory=dict)
    secret_ref: str | None = None
    offpeak_start: int | None = None
    offpeak_end: int | None = None
    enabled: bool = True
    query_log_config: JsonDict | None = None
    query_log_retention_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def _validate_destination_config(self) -> DestinationCreate:
        validate_destination_config(self.engine, self.config)
        return self


class DestinationTestRequest(BaseModel):
    """A not-yet-saved engine config, so the UI can verify it before creating/updating it."""

    engine: EngineType
    config: JsonDict = Field(default_factory=dict)
    # Same purpose as SourceTestRequest.source_id -- swap "***REDACTED***" placeholders back to
    # the real stored value when testing an edit.
    destination_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate_destination_config(self) -> DestinationTestRequest:
        validate_destination_config(self.engine, self.config)
        return self


class DestinationTestResponse(BaseModel):
    ok: bool
    capacity: int | None = None
    used: int | None = None
    available: int | None = None
    message: str | None = None


class DestinationRead(OrmModel):
    """Destination read schema with sensitive data redacted."""

    id: uuid.UUID
    name: str
    engine: EngineType
    config: JsonDict = Field(default_factory=dict)
    # NEVER return secret_ref in API responses
    offpeak_start: int | None = None
    offpeak_end: int | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    query_log_config: JsonDict | None = None
    query_log_retention_days: int | None = None

    @field_serializer("config")
    def redact_config(self, config: JsonDict) -> JsonDict:
        """Redact sensitive keys in config before serialization."""
        return redact_sensitive_dict(config)


class DestinationPatch(BaseModel):
    name: str | None = None
    engine: EngineType | None = None
    config: JsonDict | None = None
    secret_ref: str | None = None
    offpeak_start: int | None = None
    offpeak_end: int | None = None
    enabled: bool | None = None
    query_log_config: JsonDict | None = None
    query_log_retention_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def _validate_destination_config(self) -> DestinationPatch:
        # Full validation (engine + config together) runs in the route after merge.
        if self.engine is not None and self.config is not None:
            validate_destination_config(self.engine, self.config)
        elif self.config is not None and "catalog_bindings" in self.config:
            # Shape-check only; route rejects bindings on non-Trino after merge.
            parse_catalog_bindings(self.config)
        return self


class PresetCreate(BaseModel):
    name: str
    destination_id: uuid.UUID
    description: str | None = None
    mode: Mode = Mode.STANDARD
    operations: list[str] = Field(default_factory=list)
    params: JsonDict = Field(default_factory=dict)
    schedule_cron: str | None = None
    priority: int = 100
    enabled: bool = True
    guardrails: JsonDict = Field(default_factory=dict)
    is_default: bool = False


class PresetRead(PresetCreate, OrmModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PresetPatch(BaseModel):
    name: str | None = None
    destination_id: uuid.UUID | None = None
    description: str | None = None
    mode: Mode | None = None
    operations: list[str] | None = None
    params: JsonDict | None = None
    schedule_cron: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    guardrails: JsonDict | None = None
    is_default: bool | None = None


class PresetRuleCreate(BaseModel):
    predicate: JsonDict
    enabled: bool = True


class PresetRuleRead(PresetRuleCreate, OrmModel):
    id: uuid.UUID
    preset_id: uuid.UUID


class PresetSampleTable(BaseModel):
    id: uuid.UUID
    schema_name: str
    table_name: str
    total_size_gb: float = 0
    small_files_count: int = 0
    grade: str | None = None


class PresetSimulationResponse(BaseModel):
    preset_id: uuid.UUID
    matched_count: int
    total_size_gb: float
    small_files_count: int
    sample: list[PresetSampleTable]


class EffectivePresetResponse(BaseModel):
    table_id: uuid.UUID
    preset_id: uuid.UUID | None = None
    preset_name: str | None = None
    # "rule_after_stale_pin": cryo.scheduler.rules.resolve_effective_preset falls through to
    # the rule engine when a table's pinned assignment points at a preset that no longer
    # exists/is disabled (found live in prod: 21 tables pinned to retired presets, including a
    # 24TB table, silently never rescheduled) -- this response model's literal never included
    # that provenance value, so any table actually hitting that fallback path (e.g.
    # user_experience.silver_results) 500'd on every /effective-preset call instead of
    # reporting the (correct, safer-than-None) rule match it resolved to.
    provenance: Literal["assignment", "rule", "rule_after_stale_pin", "default", "none"]
    rule_id: uuid.UUID | None = None


class TableParamOverridePatch(BaseModel):
    param_overrides: JsonDict


class EffectiveParamsResponse(BaseModel):
    table_id: uuid.UUID
    preset_id: uuid.UUID | None = None
    preset_name: str | None = None
    preset_params: JsonDict
    param_overrides: JsonDict
    effective_params: JsonDict


class TableStatsRead(OrmModel):
    table_id: uuid.UUID | None = None
    scan_at: datetime | None = None
    needs_compaction: bool
    compaction_priority: str
    grade: str
    overall_score: int
    total_files: int
    small_files_count: int
    total_partitions: int | None = None
    max_files_per_partition: int | None = None
    avg_files_per_partition: float | None = None
    partition_skew_ratio: float | None = None
    total_records: int | None = None
    total_size_gb: float
    avg_file_size_mb: float | None = None
    total_snapshots: int | None = None
    total_delete_files: int | None = None
    equality_delete_files: int | None = None
    position_delete_files: int | None = None
    delete_file_pct: float | None = None
    detection_tier: str | None = None
    snapshot_watermark: int | None = None
    last_snapshot_committed_at: datetime | None = None
    last_full_reconcile_at: datetime | None = None
    manifest_count: int | None = None
    partition_col: str | None = None
    partition_col_type: str | None = None
    write_distribution_mode: str | None = None
    has_sort_order: bool | None = None
    target_file_size_bytes: int | None = None
    estimated_monthly_s3_cost_usd: float | None = None
    projected_monthly_s3_cost_usd: float | None = None
    projected_savings_usd_month: float | None = None
    reclaimable_bytes: int | None = None
    cost_precision: CostPrecision | None = None
    cost_last_full_scan_at: datetime | None = None
    query_count_30d: int = 0
    raw: JsonDict | None = None


class TableRead(OrmModel):
    id: uuid.UUID
    source_id: uuid.UUID
    schema_name: str
    table_name: str
    catalog_name: str | None = None
    source_name: str | None = None
    qualified_table_name: str | None = None
    # Live S3 location of the table's root (e.g. "s3://bucket/warehouse/db/table"), populated
    # in persist_table_analysis() at scan time. Null until the table's first scan under this
    # field (docs/s3-request-cost-tracking.md §6.1) -- also the join key S3 request-cost
    # bucket discovery uses.
    location: str | None = None
    first_seen_at: datetime
    is_active: bool
    # Set when the scanner found the table gone from the catalog (#120). The row is kept
    # (soft delete) so history survives and a re-listed table is restored automatically.
    removed_from_catalog_at: datetime | None = None
    removed_reason: str | None = None
    consecutive_scan_failures: int = 0
    last_scan_error: str | None = None
    last_scan_error_at: datetime | None = None
    # "failed" or "unreadable" -- the retryability call the scanner itself made when it
    # recorded the error, stored (unlike failure_category below) because it's what the run's
    # counters were derived from, and because filtering the fleet for unreadable tables needs
    # a real column.
    last_scan_error_kind: str | None = None
    # Derived from last_scan_error at serialization time (cryo/scanner/failure_classifier.py),
    # not stored -- lets the taxonomy evolve without a migration/backfill.
    failure_category: str | None = None
    failure_category_label: str | None = None
    failure_category_guidance: str | None = None
    failure_category_actionable_from_cryo: bool = False
    owner_email: str | None = None
    stats: TableStatsRead | None = None
    # Populated by GET /tables only (bounded to the page); detail views leave it null.
    maintenance: TableMaintenanceRead | None = None


TableMaintenanceState = Literal[
    "running",
    "queued",
    "failed",
    "held",
    "excluded",
    "scan_failing",
    "not_scanned",
    "skipped",
    "needs_work",
    "healthy",
    "inactive",
]


class TableJobRef(BaseModel):
    run_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    trigger: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TableSelectionRef(BaseModel):
    """The table's outcome in the latest nightly selection (skip_evaluations)."""

    stage: str
    reason: str | None = None
    evaluated_at: datetime


class TableMaintenanceRead(BaseModel):
    state: TableMaintenanceState
    reason: str | None = None
    workflow_name: str | None = None
    # False when the workflow came from a preset rule rather than an operator pin.
    workflow_pinned: bool = False
    active_run: TableJobRef | None = None
    last_run: TableJobRef | None = None
    last_selection: TableSelectionRef | None = None
    score_delta_7d: int | None = None


class FunnelNode(BaseModel):
    id: str
    label: str
    count: int
    # Explorer view that lists the tables currently in this state, when one exists.
    view: str | None = None


class FunnelLink(BaseModel):
    source: str
    target: str
    value: int


class TablesFunnelResponse(BaseModel):
    """Where every active table landed in the latest nightly batch, from scan to outcome."""

    run_id: uuid.UUID | None = None
    run_status: str | None = None
    run_started_at: datetime | None = None
    run_finished_at: datetime | None = None
    total_active: int
    nodes: list[FunnelNode]
    links: list[FunnelLink]


class TablePatch(BaseModel):
    owner_email: str | None = None


class TableDeactivateBatchRequest(BaseModel):
    """Explicit, caller-verified table_ids to deactivate -- not a filter or heuristic, so a
    typo or over-broad selection can't silently expand scope. See
    deactivate_tables_by_id's docstring (cryo/db/repositories/sources.py)."""

    table_ids: list[uuid.UUID]


class TableDeactivateBatchResponse(BaseModel):
    requested: int
    deactivated: int
    deactivated_ids: list[uuid.UUID]


class PaginatedTables(BaseModel):
    items: list[TableRead]
    total: int
    limit: int
    offset: int
    # Opaque server-side cursor for fetching the next page; absent when the
    # current page is the last. Decoded via cryo.api.pagination.PageCursor.
    cursor: str | None = None
    has_more: bool = False
    # Null-side rows excluded from a breach filter. None when that filter is off.
    # Computed in the same aggregate as ``total``, not a second table scan.
    unknown_count: int | None = None
    telemetry_window_days: int | None = None
    telemetry_source: str | None = None
    telemetry_coverage: float | None = None
    telemetry_gap_tables: int | None = None


class TableGroupByRow(BaseModel):
    # None for a group whose tables have no TableStats row yet (not scanned) or, for
    # "catalog", a table whose source was removed but the row survives an orphan FK.
    group_value: str | None
    count: int
    total_size_gb: float
    total_small_files: int
    total_savings_usd_month: float


class TableGroupByResponse(BaseModel):
    field: str
    rows: list[TableGroupByRow]


class PaginatedRuns(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
    cursor: str | None = None
    has_more: bool = False


class TableAlertSubscriptionRead(OrmModel):
    id: uuid.UUID
    table_id: uuid.UUID
    email: str
    created_at: datetime


class TableAlertSubscriptionCreate(BaseModel):
    email: str


class TablePartitionRead(OrmModel):
    id: int
    table_id: uuid.UUID
    scan_at: datetime
    partition_value: str | None = None
    partition_values: JsonDict
    partition_fields: list[JsonDict]
    partition_spec_id: int | None = None
    partition_path: str | None = None
    partition_key: str | None = None
    base_column: str | None = None
    base_column_type: str | None = None
    transform: str | None = None
    files_per_partition: int | None = None
    small_files_in_partition_count: int | None = None
    partition_size_gb: float | None = None
    compaction_status: str | None = None
    raw: JsonDict | None = None


class TablePartitionsResponse(BaseModel):
    table_id: uuid.UUID
    scan_at: datetime | None = None
    items: list[TablePartitionRead]
    total: int
    limit: int
    offset: int


_HISTORY_RAW_FIELDS = ("total_delete_files", "delete_file_pct", "manifest_count", "total_snapshots")


class TableStatsHistoryRead(OrmModel):
    id: int
    table_id: uuid.UUID
    scan_at: datetime
    small_files_count: int | None = None
    total_files: int | None = None
    total_size_gb: float | None = None
    grade: str | None = None
    overall_score: int | None = None
    total_delete_files: int | None = None
    delete_file_pct: float | None = None
    manifest_count: int | None = None
    total_snapshots: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_raw(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            values = dict(data)
            raw = values.get("raw")
        else:
            values = {name: getattr(data, name, None) for name in cls.model_fields}
            raw = getattr(data, "raw", None)
        if isinstance(raw, Mapping):
            for name in _HISTORY_RAW_FIELDS:
                if values.get(name) is None:
                    values[name] = raw.get(name)
        return values


class TableStatsHistoryResponse(BaseModel):
    table_id: uuid.UUID
    items: list[TableStatsHistoryRead]
    limit: int


class AuditLogRead(OrmModel):
    id: int
    ts: datetime
    table_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    action: str
    status: str
    engine: str | None = None
    detail: str | None = None
    duration_ms: int | None = None
    actual_bytes_freed: int | None = None
    input_files_count: int | None = None
    output_files_count: int | None = None
    actor: str | None = None
    committed_snapshot_id: str | None = None
    effect_status: Literal["failed", "files_reported", "bytes_reported", "unverified"] = (
        "unverified"
    )

    @model_validator(mode="after")
    def classify_effect(self) -> AuditLogRead:
        """A successful process is not a verified Iceberg commit."""
        status = (self.status or "").lower()
        if status in {"failed", "error"}:
            self.effect_status = "failed"
        elif self.input_files_count is not None and self.output_files_count is not None:
            self.effect_status = "files_reported"
        elif self.actual_bytes_freed is not None:
            self.effect_status = "bytes_reported"
        else:
            self.effect_status = "unverified"
        return self


class TableAuditResponse(BaseModel):
    table_id: uuid.UUID
    items: list[AuditLogRead]
    total: int
    limit: int
    offset: int


class RunStepRead(BaseModel):
    step_index: int
    operation_type: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TableStatsSnapshotRead(BaseModel):
    """A TableStatsHistory row, trimmed to the fields a before/after run delta needs."""

    scan_at: datetime
    grade: str | None = None
    overall_score: int | None = None
    total_files: int | None = None
    small_files_count: int | None = None
    total_size_gb: float | None = None
    total_delete_files: int | None = None
    delete_file_pct: float | None = None


class RunResourceEstimateRead(BaseModel):
    """Reserved-capacity estimate (config x active wall time), not measured utilization --
    see cli.py-adjacent notes in table_runs() for why this can't be exact without a real
    metrics pipeline."""

    engine: str | None = None
    vcpu_hours: float | None = None
    memory_gib_hours: float | None = None
    active_duration_seconds: float | None = None


class RunEffectivenessRead(BaseModel):
    """File-count and byte-size reduction reported as separate, independently-signed
    percentages -- not one blended number. S3 request cost scales with file *count*, not
    bytes, so a run that cuts file count 90% while barely moving total bytes (confirmed live,
    2026-08-10: mview.gold_dbt_drip_marketing_flow, 2,549 -> 268 files, size -4%) is a large
    real win that a size-only summary would undersell. See docs/open-items-2026-08-10.md."""

    file_count_reduction_pct: float | None = None
    small_file_reduction_pct: float | None = None
    byte_reduction_pct: float | None = None
    headline: str | None = None


class TableRunRead(BaseModel):
    job_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    trigger: str | None = None
    mode: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    error: str | None = None
    steps: list[RunStepRead] = Field(default_factory=list)
    before_stats: TableStatsSnapshotRead | None = None
    after_stats: TableStatsSnapshotRead | None = None
    resource_estimate: RunResourceEstimateRead | None = None
    effectiveness: RunEffectivenessRead | None = None


class TableRunsResponse(BaseModel):
    table_id: uuid.UUID
    items: list[TableRunRead]
    total: int
    limit: int
    offset: int


class DatalakeTotals(BaseModel):
    total_tables: int
    active_tables: int
    total_size_gb: float
    total_files: int
    small_files_count: int
    needs_compaction_count: int
    estimated_monthly_s3_cost_usd: float
    projected_monthly_s3_cost_usd: float = 0
    projected_savings_usd_month: float = 0
    reclaimable_bytes: int | None = None
    query_count_30d: int
    running_jobs: int = 0


class GradeDistributionItem(BaseModel):
    grade: str
    count: int


class FreshnessDistributionItem(BaseModel):
    key: str
    label: str
    count: int


class DashboardTopOffender(BaseModel):
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    grade: str | None = None
    overall_score: int | None = None
    total_size_gb: float = 0
    total_files: int = 0
    small_files_count: int = 0
    delete_file_pct: float | None = None
    estimated_monthly_s3_cost_usd: float | None = None
    query_count_30d: int = 0


class DashboardImprovedTable(BaseModel):
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    previous_grade: str | None = None
    previous_score: int | None = None
    grade: str | None = None
    overall_score: int | None = None
    improved_at: datetime


class DashboardDriftPoint(BaseModel):
    scan_at: datetime
    total_size_gb: float
    total_files: int
    small_files_count: int
    avg_overall_score: float | None = None


class DatalakeDashboardResponse(BaseModel):
    totals: DatalakeTotals
    grade_distribution: list[GradeDistributionItem]
    freshness_distribution: list[FreshnessDistributionItem] = Field(default_factory=list)
    freshness_as_of: datetime | None = None
    top_offenders: list[DashboardTopOffender]
    hot_at_risk: list[DashboardTopOffender] = Field(default_factory=list)
    hot_at_risk_count: int = 0
    recently_improved: list[DashboardImprovedTable] = Field(default_factory=list)
    recently_improved_count: int = 0
    drift: list[DashboardDriftPoint]
    cost_by_schema: list[CostSchemaItem] = Field(default_factory=list)


class SchemaCostItem(BaseModel):
    schema_name: str
    table_count: int
    total_size_gb: float
    estimated_monthly_s3_cost_usd: float
    small_files_count: int
    query_count_30d: int


class SchemaCostDashboardResponse(BaseModel):
    items: list[SchemaCostItem]
    limit: int


class CostByOperationRead(BaseModel):
    operation: OperationType
    savings_usd_month: float
    reclaimable_bytes: int


class CostTrendPoint(BaseModel):
    scan_at: datetime
    current_monthly_cost_usd: float | None = None
    projected_savings_usd_month: float | None = None
    realized_usd_month: float | None = None


class CostDashboardTotals(BaseModel):
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    current_storage_bytes: int
    # None when no active table has a measured reclaimable figure (not a measured zero).
    reclaimable_bytes: int | None = None
    reclaimable_tables_measured: int = 0
    precision_coverage: dict[str, int] = Field(default_factory=dict)
    orphan_scan_stale: bool = False
    last_full_cost_scan_at: datetime | None = None
    # Monthly storage value of all bytes successful reclaim actions have freed ($/month at
    # the configured storage price), not lifetime dollars. None when nothing was measured.
    total_realized_savings_usd: float | None = None
    total_realized_bytes_freed: int | None = None
    # GET-cost reduction from compaction, summed over tables that have a TableCost row.
    # None means no table has one, so the UI must not render $0. The table count says how
    # much of the fleet the sum covers.
    query_request_savings_usd_month: float | None = None
    query_request_savings_tables: int = 0


class CostSchemaItem(BaseModel):
    schema_name: str
    current_usd: float
    projected_usd: float
    savings_usd: float


class CostDashboardResponse(BaseModel):
    totals: CostDashboardTotals
    savings_by_operation: list[CostByOperationRead]
    cost_by_schema: list[CostSchemaItem]
    top_opportunities: list[TableRead]
    trend: list[CostTrendPoint] = Field(default_factory=list)


class TableCostRead(BaseModel):
    table_id: uuid.UUID
    precision: CostPrecision
    price_per_gb_month: float
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    query_request_savings_usd_month: float
    live_bytes: int
    dead_bytes: int
    metadata_bytes: int
    orphan_bytes: int | None = None
    listed_bytes: int | None = None
    reclaimable_bytes: int
    by_operation: list[CostByOperationRead]
    last_full_cost_scan_at: datetime | None = None
    scan_at: datetime | None = None
    get_requests_30d: int | None = None
    put_requests_30d: int | None = None
    list_requests_30d: int | None = None
    # 2026-09-08 (docs/s3-total-cost-optimization.md P0#2/#3): previously HEAD was silently
    # folded into get_requests_30d and DELETE wasn't tracked at all -- see TableCost's own
    # field docstrings in cryo/db/models.py.
    head_requests_30d: int | None = None
    delete_requests_30d: int | None = None
    request_cost_usd_month: float | None = None
    request_cost_pricing_source: str | None = None


class AssignmentSelection(BaseModel):
    mode: Literal["ids", "filter"]
    ids: list[uuid.UUID] = Field(default_factory=list)
    filter: JsonDict | None = None


class AssignmentRequest(BaseModel):
    selection: AssignmentSelection
    preset_id: uuid.UUID
    assigned_by: str | None = None


class AssignmentDeleteRequest(BaseModel):
    selection: AssignmentSelection


class AssignmentResponse(BaseModel):
    affected: int


class PreviewTable(BaseModel):
    schema_name: str
    table_name: str
    catalog: str = "iceberg"
    stats: JsonDict = Field(default_factory=dict)
    partitions: list[JsonDict] = Field(default_factory=list)


class MaintenancePreviewRequest(BaseModel):
    engine: EngineType = EngineType.TRINO
    mode: Mode = Mode.STANDARD
    params: JsonDict = Field(default_factory=dict)
    tables: list[PreviewTable]


class RenderedStatementRead(BaseModel):
    action: str
    engine: EngineType
    sql: str
    payload: JsonDict = Field(default_factory=dict)


class SavingsProjectionRead(BaseModel):
    precision: str
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    reclaimable_bytes: int
    query_request_savings_usd_month: float = 0.0
    by_operation: JsonDict = Field(default_factory=dict)
    assumptions: JsonDict = Field(default_factory=dict)


class OrphanScanCostRead(BaseModel):
    estimated_list_requests: int
    estimated_cost_usd: float
    estimated_files_to_scan: int
    partition_depth: int
    warning: str | None = None


class TablePreviewRead(BaseModel):
    table_ref: str
    notes: list[str]
    statements: list[RenderedStatementRead]
    savings: SavingsProjectionRead | None = None
    stats_as_of: datetime | None = None
    orphan_scan_cost: OrphanScanCostRead | None = None


class MaintenancePreviewResponse(BaseModel):
    items: list[TablePreviewRead]


class OffPeakWindowResponse(BaseModel):
    """The governor-wide off-peak window heavy maintenance is restricted to.

    Deliberately read-only and not table-scoped: ``offpeak_start_ist`` /
    ``offpeak_end_ist`` are process settings on the Governor, so a per-table
    editor here would imply an override the admission path cannot honour
    (see cryo/governor/admission.py:is_offpeak).
    """

    start_hour_ist: int
    end_hour_ist: int
    # Equal bounds disable the gate entirely -- surfaced explicitly so the UI
    # can say "any hour" instead of rendering a misleading 00:00-00:00 window.
    enabled: bool


class MaintenanceRunRequest(BaseModel):
    table_ids: list[uuid.UUID] = Field(default_factory=list)
    selection: AssignmentSelection | None = None
    destination_id: uuid.UUID
    preset_id: uuid.UUID
    mode: Mode = Mode.STANDARD
    trigger: str | None = "api"
    # One-off future run time, distinct from a Preset's recurring schedule_cron. When set, the
    # run is created with status="scheduled" and its jobs are not enqueued until this time
    # passes (see cryo/scheduler/service.py's dispatch tick). Must be in the future.
    scheduled_at: datetime | None = None
    operations: list[str] | None = None
    # Create the jobs even when a queued/running job already covers the same operations on the
    # table. Without it those steps are skipped and reported in the response and run summary.
    force: bool = False


class SkippedTableOps(BaseModel):
    """Operations a new run did not queue because an active job already covers them."""

    table_id: uuid.UUID
    operations: list[str]
    covering_job_ids: list[str]
    fully_skipped: bool


class MaintenanceRunResponse(BaseModel):
    run_id: uuid.UUID
    job_ids: list[uuid.UUID]
    status: str
    skipped: list[SkippedTableOps] = Field(default_factory=list)


class ScannerConfigRead(OrmModel):
    id: int
    enabled: bool
    schedule_cron: str
    timezone: str
    num_chunks: int
    full_scan_default: bool
    cost_full_scan_default: bool
    max_concurrent_runs: int
    created_at: datetime
    updated_at: datetime


class ScannerConfigUpdate(BaseModel):
    enabled: bool | None = None
    schedule_cron: str | None = None
    num_chunks: int | None = Field(default=None, ge=1)
    full_scan_default: bool | None = None
    cost_full_scan_default: bool | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1)


class TableNameRef(BaseModel):
    """A table identified by name rather than by Cryo's own id -- lets scope="tables" target a
    table that's never been scanned before (no Table row/id exists for it yet). Requires
    source_id on the request, since the same schema.table name could exist in more than one
    lakehouse."""

    schema_name: str
    table_name: str


class ScanTriggerRequest(BaseModel):
    scope: Literal["all", "source", "tables", "schemas"] = "all"
    source_id: uuid.UUID | None = None
    table_ids: list[uuid.UUID] = Field(default_factory=list)
    table_names: list[TableNameRef] = Field(default_factory=list)
    schema_names: list[str] = Field(default_factory=list)
    selection: AssignmentSelection | None = None
    trigger: str = "api"
    requested_by: str | None = None
    full_scan: bool = True
    cost_full_scan: bool = False
    num_chunks: int | None = Field(default=None, ge=1)


class ScanTriggerResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    scope: Literal["all", "source", "tables", "schemas"]
    queued: bool
    message: str
    cost_full_scan: bool = False
    full_scan: bool = True
    num_chunks: int | None = None
    source_id: uuid.UUID | None = None
    table_ids: list[uuid.UUID] = Field(default_factory=list)
    schema_names: list[str] = Field(default_factory=list)


class RunRead(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    preset_id: uuid.UUID | None = None
    trigger: str | None = None
    deadline_at: datetime | None = None
    scheduled_at: datetime | None = None
    created_at: datetime
    finished_at: datetime | None = None
    summary: JsonDict | None = None
    # Detail endpoints only. None means the list path did not compute them.
    # realized_bytes_freed is None when no successful action reported bytes —
    # that is missing evidence, not a measured zero.
    realized_bytes_freed: int | None = None
    realized_actions_measured: int | None = None
    realized_actions_succeeded: int | None = None


class ScannerStatusRead(BaseModel):
    config: ScannerConfigRead
    queue_depth: int | None = None
    latest_run: RunRead | None = None
    active_run: RunRead | None = None
    last_successful_run: RunRead | None = None
    next_scan_at: datetime | None = None
    total_tables: int = 0
    tables_with_stats: int = 0
    latest_table_scan_at: datetime | None = None


class JobRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    table_id: uuid.UUID
    destination_id: uuid.UUID
    schema_name: str | None = None
    table_name: str | None = None
    destination_name: str | None = None
    destination_engine: str | None = None
    mode: str
    status: str
    engine_ref: str | None = None
    replica: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    plan: JsonDict | None = None
    force_admit: bool = False


class RunJobsResponse(BaseModel):
    run: RunRead
    jobs: list[JobRead]


class RunSnapshotPayload(RunJobsResponse):
    type: Literal["run_snapshot"] = "run_snapshot"


class RunLogEventPayload(BaseModel):
    type: Literal["log_line"] = "log_line"
    ts: datetime | str
    run_id: uuid.UUID
    level: str = "info"
    message: str
    job_id: uuid.UUID | None = None
    table_id: uuid.UUID | None = None
    table_ref: str | None = None
    chunk_id: int | None = None
    operation: str | None = None
    engine: str | None = None


class RunJobStatusEventPayload(BaseModel):
    type: Literal["job_status"] = "job_status"
    ts: datetime | str
    run_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    table_id: uuid.UUID | None = None
    table_ref: str | None = None
    message: str | None = None


class RunScanProgressEventPayload(BaseModel):
    type: Literal["scan_progress"] = "scan_progress"
    ts: datetime | str
    run_id: uuid.UUID
    event: str
    level: str = "info"
    message: str
    chunk_id: int | None = None
    table_ref: str | None = None


# ==================== Advanced: Workload-aware compaction candidates ====================


class CompactionCandidateRead(BaseModel):
    partition_path: str
    partition_size_gb: float
    small_files: int
    query_freq_per_day: float
    p95_latency_ms: float
    score: float
    priority: str
    savings_estimate_usd: float
    reasons: list[str] = Field(default_factory=list)


class CompactionCandidatesResponse(BaseModel):
    table_id: uuid.UUID
    candidates: list[CompactionCandidateRead]
    generated_at: datetime


# ==================== Advanced: Unionable tables (EasyTUS) ====================


class UnionScoreRead(BaseModel):
    target_table_id: str
    candidate_table_id: str
    score: float
    overlap_columns: list[str] = Field(default_factory=list)
    union_sql: str
    rationale: str
    candidate_schema: str
    candidate_table_name: str = ""
    candidate_schema_name: str = ""


class UnionableResponse(BaseModel):
    target_table_id: uuid.UUID
    results: list[UnionScoreRead]
    generated_at: datetime


# ==================== Advanced: AI skillset versioning ====================


class SkillsetRead(BaseModel):
    id: str
    name: str
    version: str
    prompts: JsonDict = Field(default_factory=dict)
    tools: list[JsonDict] = Field(default_factory=list)
    created_at: datetime
    is_active: bool
    metrics: JsonDict = Field(default_factory=dict)


class SkillsetListResponse(BaseModel):
    skillsets: list[SkillsetRead]
    generated_at: datetime


class SkillsetWrite(BaseModel):
    name: str
    version: str
    prompts: JsonDict = Field(default_factory=dict)
    tools: list[JsonDict] = Field(default_factory=list)
    activate: bool = False


class SkillsetRollbackRequest(BaseModel):
    version: str


class SkillsetActivateRequest(BaseModel):
    version: str


class SkillsetPromoteRequest(BaseModel):
    source_version: str
    target_version: str
    activate: bool = True


class SkillsetOutcomeWrite(BaseModel):
    prompt_name: str
    recommendation: str
    decision: Literal["accepted", "rejected", "pending"]
    score: float | None = None
    table_id: uuid.UUID | None = None


class SkillsetOutcomeRead(BaseModel):
    id: uuid.UUID
    skillset_id: uuid.UUID
    recorded_at: datetime
    table_id: uuid.UUID | None
    prompt_name: str
    recommendation: str
    decision: str
    score: float | None


class LLMObservationRead(BaseModel):
    id: uuid.UUID
    recorded_at: datetime
    provider_type: str
    model_id: str
    feature: str
    prompt_name: str | None = None
    prompt_hash: str
    skillset_id: uuid.UUID | None = None
    skillset_name: str | None = None
    skillset_version: str | None = None
    run_id: uuid.UUID | None = None
    table_id: uuid.UUID | None = None
    outcome_id: uuid.UUID | None = None
    latency_ms: int | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None = None
    status: str
    budget_denied: bool
    error: str | None = None
    extra: JsonDict = Field(default_factory=dict)


class LLMObservationListResponse(BaseModel):
    items: list[LLMObservationRead]
    total: int


class RunFailureDiagnosisRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID | None = None
    table_id: uuid.UUID | None = None
    diagnosis_class: str
    confidence: float
    summary: str | None = None
    runbook_step: str | None = None
    evidence: list[str] = Field(default_factory=list)
    feedback: str | None = None
    feedback_note: str | None = None
    diagnosed_at: datetime
    skillset_id: uuid.UUID | None = None
    observation_id: uuid.UUID | None = None


class RunFailureDiagnosisFeedbackWrite(BaseModel):
    feedback: Literal["up", "down"]
    note: str | None = None


class TableFilterTranslationRequest(BaseModel):
    query: str
    use_llm: bool = True


class TableFilterTranslationResponse(BaseModel):
    filter: JsonDict = Field(default_factory=dict)
    chips: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    used_llm: bool = False
    provider: str | None = None
    model: str | None = None
    observation_id: uuid.UUID | None = None


class PlanNarrationRequest(BaseModel):
    table_ref: str
    notes: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    sql_statements: list[str] = Field(default_factory=list)


class PlanNarrationResponse(BaseModel):
    deterministic_summary: str
    llm_narration: str | None = None
    used_llm: bool = False
    observation_id: uuid.UUID | None = None


# ============================================================================
# Plan Verifier (arXiv:2510.09567, proof-carrying plans)
# ============================================================================


class PartitionPredicateTermRead(BaseModel):
    base_column: str
    transform: str
    value: object | None = None
    col_type: str = ""
    transform_param: int | None = None
    partition_field: str | None = None
    unsupported_reason: str | None = None


class PartitionPredicateRead(BaseModel):
    terms: list[PartitionPredicateTermRead]
    unsupported_reason: str | None = None


class OperationRead(BaseModel):
    type: OperationType
    params: dict[str, Any] = Field(default_factory=dict)
    predicates: list[PartitionPredicateRead] | None = None


class MaintenancePlanRead(BaseModel):
    table_ref: str
    mode: Mode
    operations: list[OperationRead]
    notes: list[str] = Field(default_factory=list)


class PlanVerifyRequest(BaseModel):
    plan: MaintenancePlanRead
    signature: str | None = None


class PlanViolationRead(BaseModel):
    invariant: str
    severity: Literal["critical", "warning"]
    message: str
    location: str


class PlanVerdictRead(BaseModel):
    verified: bool
    plan_hash: str
    verifier_version: str
    invariants_checked: list[str] = Field(default_factory=list)
    violations: list[PlanViolationRead] = Field(default_factory=list)


# ============================================================================
# Observation Lakehouse (SANER 2026, arXiv:2512.02795)
# ============================================================================


class ObservationEventRead(BaseModel):
    id: uuid.UUID
    recorded_at: datetime
    run_id: str | None = None
    event_type: str
    level: str
    table_id: str | None = None
    subject: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    actor: str | None = None


class ObservationSearchResponse(BaseModel):
    items: list[ObservationEventRead]
    total: int


class ObservationStatsResponse(BaseModel):
    total_files: int
    total_bytes: int
    oldest_event: datetime | None = None
    newest_event: datetime | None = None


class ObservationCompactResponse(BaseModel):
    files_merged: int
    rows: int
    bytes_in: int
    bytes_out: int


# ---------------------------------------------------------------------------
# Table deduplication / similarity (docs/TABLE_DEDUPLICATION_DESIGN.md)
# ---------------------------------------------------------------------------


class SimilarityPairRead(BaseModel):
    id: uuid.UUID
    table_a_id: uuid.UUID
    table_a_name: str
    table_b_id: uuid.UUID
    table_b_name: str
    stage: str
    similarity_score: float | None = None
    schema_similarity: float | None = None
    stats_similarity: float | None = None
    row_jaccard_estimate: float | None = None
    naming_pattern_boost: float | None = None
    file_overlap_detected: bool = False
    verified_relationship: str | None = None
    exact_row_overlap_pct: float | None = None
    score_components: JsonDict = Field(default_factory=dict)
    computed_at: datetime


class SimilarityClusterMemberRead(BaseModel):
    table_id: uuid.UUID
    table_name: str
    schema_name: str
    total_size_gb: float
    role: Literal["keep", "drop_candidate"]
    keep_score: float | None = None
    keep_reason: str | None = None
    query_count_30d: int = 0


class SimilarityClusterRead(BaseModel):
    cluster_id: uuid.UUID
    members: list[SimilarityClusterMemberRead]
    total_size_gb: float
    reclaimable_gb: float
    max_pair_similarity: float | None = None
    generation: int
    computed_at: datetime


class SimilarityClustersResponse(BaseModel):
    clusters: list[SimilarityClusterRead]
    generation: int | None = None
    total_clusters: int = 0
    total_tables_in_clusters: int = 0
    total_reclaimable_gb: float = 0


class SimilarityPipelineRunResponse(BaseModel):
    tables_considered: int
    candidate_pairs: int
    pairs_sampled: int
    pairs_persisted: int
    clusters_found: int
    cluster_generation: int
    errors: list[str] = Field(default_factory=list)


class SimilarityVerifyRequest(BaseModel):
    table_a_id: uuid.UUID
    table_b_id: uuid.UUID
    row_limit: int = 500_000


class SimilarityVerifyResponse(BaseModel):
    table_a_id: uuid.UUID
    table_b_id: uuid.UUID
    relationship: str
    a_rows_found_in_b_pct: float
    b_rows_found_in_a_pct: float
    rows_compared_a: int
    rows_compared_b: int


class DarkTableCandidateRead(BaseModel):
    id: uuid.UUID
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    consecutive_zero_query_windows: int
    first_zero_query_at: datetime | None
    last_checked_at: datetime
    total_size_gb: float
    estimated_monthly_cost_usd: float | None
    status: str


class DarkTableCandidatesResponse(BaseModel):
    candidates: list[DarkTableCandidateRead]
    total_candidates: int = 0
    total_size_gb: float = 0
    total_estimated_monthly_cost_usd: float = 0


class DarkTableDismissRequest(BaseModel):
    reason: str


class DarkTableDismissResponse(BaseModel):
    table_id: uuid.UUID
    status: str
    dismissed_by: str
    dismissed_at: datetime
    dismissed_reason: str


class CostAttributionRow(BaseModel):
    client: str
    schema_name: str
    query_count: int
    failed_query_count: int
    physical_bytes_scanned: int
    physical_gb_scanned: float
    # Derived only when every day in the window had both namespace spend and bytes
    # scanned. None means at least one day was not priced — callers must not treat it as $0,
    # and must not show a priced subset as the price of the bytes beside it.
    estimated_scan_cost_usd: float | None = None


class CostAttributionByClient(BaseModel):
    client: str
    query_count: int
    physical_bytes_scanned: int
    physical_gb_scanned: float
    estimated_scan_cost_usd: float | None = None


class CostAttributionResponse(BaseModel):
    since: date
    rows: list[CostAttributionRow]
    by_client: list[CostAttributionByClient]
    total_query_count: int = 0
    total_physical_gb_scanned: float = 0
    # None when no day in the window was priced. A stored 0 would be a real zero.
    total_estimated_scan_cost_usd: float | None = None
    scan_cost_priced_days: int = 0


class PartitionAccessPoint(BaseModel):
    """One (day, partition) cell for the scatter chart -- docs/
    PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md. ``partition_path`` is the raw Hive-style
    fragment (e.g. "dt=2026-08-19/"), "" for an unpartitioned table's data files."""

    day: date
    partition_path: str
    request_count: int


class PartitionAccessSummary(BaseModel):
    partition_path: str
    total_requests: int
    first_seen: date
    last_seen: date
    days_since_last_access: int


class PartitionAccessResponse(BaseModel):
    table_id: uuid.UUID
    since: date
    points: list[PartitionAccessPoint]
    partitions: list[PartitionAccessSummary]
