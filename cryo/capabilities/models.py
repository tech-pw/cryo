from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cryo.domain.enums import EngineType, OperationType


class MaturityTier(StrEnum):
    GA = "ga"
    BETA = "beta"
    EXPERIMENTAL = "experimental"
    BLOCKED = "blocked"


class SupportStatus(StrEnum):
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    FALLBACK_REQUIRED = "fallback_required"
    CONFIGURATION_ONLY = "configuration_only"
    UNSUPPORTED = "unsupported"


class ExecutionMode(StrEnum):
    TRINO_SQL = "trino_sql"
    SPARK_JOB = "spark_job"
    NATIVE_SUBPROCESS = "native_subprocess"
    SQL_FALLBACK = "sql_fallback"
    FLINK_CONFIGURATION = "flink_configuration"
    NONE = "none"


class EffectStrategy(StrEnum):
    DESTINATION_IDEMPOTENCY_TOKEN = "destination_idempotency_token"
    CONDITIONAL_SNAPSHOT_COMMIT = "conditional_snapshot_commit"
    STABLE_REFERENCE_PROBE = "stable_reference_probe"
    MANUAL_RECONCILIATION = "manual_reconciliation"
    NO_EXTERNAL_EFFECT = "no_external_effect"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class CapabilityEvidence(FrozenModel):
    kind: str
    path: str
    symbol: str
    description: str


class MaturityDefinition(FrozenModel):
    tier: MaturityTier
    meaning: str


class OperationDefinition(FrozenModel):
    operation: OperationType
    label: str
    description: str
    destructive: bool


class EngineCapability(FrozenModel):
    engine: EngineType
    label: str
    maturity: MaturityTier
    execution_available: bool
    summary: str
    limitations: tuple[str, ...] = ()
    owner: str
    evidence: tuple[CapabilityEvidence, ...]


class CatalogCapability(FrozenModel):
    catalog_type: str
    label: str
    aliases: tuple[str, ...]
    protocol: str
    maturity: MaturityTier
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    owner: str
    evidence: tuple[CapabilityEvidence, ...]


class OperationCapability(FrozenModel):
    engine: EngineType
    operation: OperationType
    support: SupportStatus
    maturity: MaturityTier
    execution_mode: ExecutionMode
    effect_strategy: EffectStrategy
    runtime_conditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    fallback_engines: tuple[EngineType, ...] = ()
    owner: str
    evidence: tuple[CapabilityEvidence, ...]


class CapabilityManifest(FrozenModel):
    schema_version: str
    source: str
    maturity_definitions: tuple[MaturityDefinition, ...]
    operations: tuple[OperationDefinition, ...]
    engines: tuple[EngineCapability, ...]
    catalogs: tuple[CatalogCapability, ...]
    matrix: tuple[OperationCapability, ...]
