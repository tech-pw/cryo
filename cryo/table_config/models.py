"""API shapes for the table configuration inventory."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from cryo.table_config.context import ValueSource
from cryo.table_config.risk import Risk
from cryo.table_config.settings import MinRole, SettingGroup, SettingKind, ValueType


class EngineNoteRead(BaseModel):
    engine: Literal["trino", "spark", "flink", "native", "cryo"]
    note: str


class SettingEditorRead(BaseModel):
    value_type: ValueType
    options: list[str] = Field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None
    unit: str | None = None
    allow_unset: bool = True


class RiskDirectionRead(BaseModel):
    label: str
    risk: Risk
    detail: str | None = None
    blocked: str | None = None


class TableSettingRead(BaseModel):
    key: str
    label: str
    group: SettingGroup
    kind: SettingKind
    summary: str
    takes_effect: str
    reversible: str
    doc_url: str
    current_value: str | None = None
    current_source: ValueSource
    is_set: bool
    default_value: str | None = None
    default_label: str | None = None
    recommended_value: str | None = None
    recommendation_reason: str | None = None
    note: str | None = None
    matches_recommendation: bool | None = None
    change_risk: Risk
    recommendation_risk: Risk | None = None
    risk_directions: list[RiskDirectionRead] = Field(default_factory=list)
    editable: bool
    read_only_reason: str | None = None
    min_role: MinRole
    editor: SettingEditorRead
    engine_notes: list[EngineNoteRead] = Field(default_factory=list)


class SettingGroupRead(BaseModel):
    id: SettingGroup
    label: str
    description: str


class PartitionFieldRead(BaseModel):
    name: str
    source_column: str | None = None
    transform: str


class SortFieldRead(BaseModel):
    column: str | None = None
    transform: str
    direction: str
    null_order: str


class RefRead(BaseModel):
    name: str
    type: Literal["branch", "tag"]
    snapshot_id: str | None = None
    max_ref_age_ms: int | None = None
    max_snapshot_age_ms: int | None = None
    min_snapshots_to_keep: int | None = None
    retained_forever: bool


class TableMetadataRead(BaseModel):
    format_version: int | None = None
    table_uuid: str | None = None
    location: str | None = None
    metadata_location: str | None = None
    last_updated_ms: int | None = None
    current_snapshot_id: str | None = None
    snapshot_count: int = 0
    metadata_log_count: int = 0
    schema_id: int | None = None
    schema_count: int = 0
    column_count: int = 0
    identifier_fields: list[str] = Field(default_factory=list)
    partition_fields: list[PartitionFieldRead] = Field(default_factory=list)
    spec_id: int | None = None
    spec_count: int = 0
    sort_fields: list[SortFieldRead] = Field(default_factory=list)
    sort_order_id: int | None = None
    refs: list[RefRead] = Field(default_factory=list)
    statistics_files: int = 0
    partition_statistics_files: int = 0


class TableConfigResponse(BaseModel):
    table_id: uuid.UUID
    read_from: Literal["catalog", "scan"]
    read_at: str
    catalog_error: str | None = None
    can_edit: bool
    preset_name: str | None = None
    preset_engine: str | None = None
    groups: list[SettingGroupRead]
    settings: list[TableSettingRead]
    metadata: TableMetadataRead | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)


class ConfigChange(BaseModel):
    key: str = Field(min_length=1, max_length=300)
    value: str | None = Field(default=None, max_length=2_000)
    expected_value: str | None = Field(
        default=None,
        max_length=2_000,
        description="Value the client last saw; a mismatch means someone else changed it.",
    )
    check_expected: bool = False


class ConfigPreviewRequest(BaseModel):
    changes: list[ConfigChange] = Field(min_length=1, max_length=50)


class ConfigChangePreview(BaseModel):
    key: str
    label: str
    kind: SettingKind
    old_value: str | None = None
    new_value: str | None = None
    risk: Risk
    warnings: list[str] = Field(default_factory=list)
    blocked: str | None = None
    min_role: MinRole
    no_op: bool = False


class ConfigPreviewResponse(BaseModel):
    changes: list[ConfigChangePreview]
    overall_risk: Risk
    requires_confirmation: bool
    blocked: bool


class ConfigApplyRequest(ConfigPreviewRequest):
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class ConfigApplyResponse(BaseModel):
    applied: list[ConfigChangePreview]
    audit_log_ids: list[int]
    message: str
    config: TableConfigResponse
