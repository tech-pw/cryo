from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cryo.domain.enums import CostPrecision, EngineType, Mode, OperationType

JsonDict = dict[str, Any]


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceCreate(BaseModel):
    name: str
    type: str
    config: JsonDict = Field(default_factory=dict)
    secret_ref: str | None = None
    enabled: bool = True


class SourceRead(SourceCreate, OrmModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class SourcePatch(BaseModel):
    name: str | None = None
    type: str | None = None
    config: JsonDict | None = None
    secret_ref: str | None = None
    enabled: bool | None = None


class SourceTestRequest(BaseModel):
    """A not-yet-saved source config, so the UI can verify a connection before creating it."""

    type: str
    config: JsonDict = Field(default_factory=dict)


class SourceTestResponse(BaseModel):
    ok: bool
    namespaces: list[str] = Field(default_factory=list)
    table_count: int = 0
    sample_tables: list[str] = Field(default_factory=list)
    error: str | None = None


class DestinationCreate(BaseModel):
    name: str
    engine: EngineType
    config: JsonDict = Field(default_factory=dict)
    secret_ref: str | None = None
    offpeak_start: int | None = None
    offpeak_end: int | None = None
    enabled: bool = True


class DestinationRead(DestinationCreate, OrmModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DestinationPatch(BaseModel):
    name: str | None = None
    engine: EngineType | None = None
    config: JsonDict | None = None
    secret_ref: str | None = None
    offpeak_start: int | None = None
    offpeak_end: int | None = None
    enabled: bool | None = None


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
    provenance: Literal["assignment", "rule", "none"]
    rule_id: uuid.UUID | None = None


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
    delete_file_pct: float | None = None
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
    first_seen_at: datetime
    is_active: bool
    stats: TableStatsRead | None = None


class PaginatedTables(BaseModel):
    items: list[TableRead]
    total: int
    limit: int
    offset: int


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


class TableStatsHistoryRead(OrmModel):
    id: int
    table_id: uuid.UUID
    scan_at: datetime
    small_files_count: int | None = None
    total_files: int | None = None
    total_size_gb: float | None = None
    grade: str | None = None
    overall_score: int | None = None


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


class TableAuditResponse(BaseModel):
    table_id: uuid.UUID
    items: list[AuditLogRead]
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
    reclaimable_bytes: int = 0
    query_count_30d: int


class GradeDistributionItem(BaseModel):
    grade: str
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


class DashboardDriftPoint(BaseModel):
    scan_at: datetime
    total_size_gb: float
    total_files: int
    small_files_count: int
    avg_overall_score: float | None = None


class DatalakeDashboardResponse(BaseModel):
    totals: DatalakeTotals
    grade_distribution: list[GradeDistributionItem]
    top_offenders: list[DashboardTopOffender]
    drift: list[DashboardDriftPoint]


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
    reclaimable_bytes: int
    precision_coverage: dict[str, int] = Field(default_factory=dict)
    orphan_scan_stale: bool = False
    last_full_cost_scan_at: datetime | None = None


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


class TablePreviewRead(BaseModel):
    table_ref: str
    notes: list[str]
    statements: list[RenderedStatementRead]
    savings: SavingsProjectionRead | None = None


class MaintenancePreviewResponse(BaseModel):
    items: list[TablePreviewRead]


class MaintenanceRunRequest(BaseModel):
    table_ids: list[uuid.UUID] = Field(default_factory=list)
    selection: AssignmentSelection | None = None
    destination_id: uuid.UUID
    preset_id: uuid.UUID | None = None
    mode: Mode = Mode.STANDARD
    trigger: str | None = "api"


class MaintenanceRunResponse(BaseModel):
    run_id: uuid.UUID
    job_ids: list[uuid.UUID]
    status: str


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


class ScanTriggerRequest(BaseModel):
    scope: Literal["all", "source", "tables", "schemas"] = "all"
    source_id: uuid.UUID | None = None
    table_ids: list[uuid.UUID] = Field(default_factory=list)
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
    created_at: datetime
    finished_at: datetime | None = None
    summary: JsonDict | None = None


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


class RunJobsResponse(BaseModel):
    run: RunRead
    jobs: list[JobRead]


class RunSnapshotPayload(RunJobsResponse):
    type: Literal["run_snapshot"] = "run_snapshot"
