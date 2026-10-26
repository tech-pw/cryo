from __future__ import annotations

from itertools import product

from cryo.capabilities.models import (
    CapabilityEvidence,
    CapabilityManifest,
    CatalogCapability,
    EffectStrategy,
    EngineCapability,
    ExecutionMode,
    MaturityDefinition,
    MaturityTier,
    OperationCapability,
    OperationDefinition,
    SupportStatus,
)
from cryo.domain.enums import EngineType, OperationType


def _evidence(
    code_path: str,
    code_symbol: str,
    test_path: str,
    test_symbol: str,
) -> tuple[CapabilityEvidence, ...]:
    return (
        CapabilityEvidence(
            kind="code",
            path=code_path,
            symbol=code_symbol,
            description="Runtime implementation",
        ),
        CapabilityEvidence(
            kind="test",
            path=test_path,
            symbol=test_symbol,
            description="Executable contract",
        ),
    )


def _cell(
    engine: EngineType,
    operation: OperationType,
    support: SupportStatus,
    maturity: MaturityTier,
    execution_mode: ExecutionMode,
    *,
    runtime_conditions: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    fallback_engines: tuple[EngineType, ...] = (),
    owner: str,
    evidence: tuple[CapabilityEvidence, ...],
    effect_strategy: EffectStrategy | None = None,
) -> OperationCapability:
    return OperationCapability(
        engine=engine,
        operation=operation,
        support=support,
        maturity=maturity,
        execution_mode=execution_mode,
        effect_strategy=effect_strategy
        or _default_effect_strategy(engine, operation, execution_mode),
        runtime_conditions=runtime_conditions,
        limitations=limitations,
        fallback_engines=fallback_engines,
        owner=owner,
        evidence=evidence,
    )


def _default_effect_strategy(
    engine: EngineType,
    operation: OperationType,
    execution_mode: ExecutionMode,
) -> EffectStrategy:
    if execution_mode in {ExecutionMode.NONE, ExecutionMode.FLINK_CONFIGURATION}:
        return EffectStrategy.NO_EXTERNAL_EFFECT
    if execution_mode is ExecutionMode.SQL_FALLBACK:
        return EffectStrategy.STABLE_REFERENCE_PROBE
    if engine is not EngineType.NATIVE:
        return EffectStrategy.STABLE_REFERENCE_PROBE
    if operation in {OperationType.EXPIRE_SNAPSHOTS, OperationType.OPTIMIZE}:
        return EffectStrategy.CONDITIONAL_SNAPSHOT_COMMIT
    if operation is OperationType.REMOVE_ORPHAN_FILES:
        return EffectStrategy.MANUAL_RECONCILIATION
    return EffectStrategy.STABLE_REFERENCE_PROBE


_TRINO_EVIDENCE = _evidence(
    "cryo/engines/trino/sql.py",
    "_render_operation",
    "tests/test_trino_sql_golden.py",
    "test_trino_render_golden_operation_sql_and_session_setup",
)
_SPARK_EVIDENCE = _evidence(
    "cryo/engines/spark/sql.py",
    "_render_operation",
    "tests/test_spark_sql_golden.py",
    "test_spark_render_golden_operation_sql",
)
_NATIVE_EVIDENCE = _evidence(
    "cryo/engines/native/routing.py",
    "resolve_native_routing",
    "tests/test_native_routing.py",
    "test_resolve_native_routing_cow_includes_optimize",
)
_FLINK_EVIDENCE = _evidence(
    "cryo/engines/flink/engine.py",
    "def execute",
    "tests/test_flink_engine.py",
    "test_flink_engine_execute",
)

_OPERATIONS = (
    OperationDefinition(
        operation=OperationType.EXPIRE_DATA,
        label="Expire data",
        description="Delete partitions or rows older than a retention cutoff.",
        destructive=True,
    ),
    OperationDefinition(
        operation=OperationType.EXPIRE_SNAPSHOTS,
        label="Expire snapshots",
        description="Remove snapshots older than the protected retention boundary.",
        destructive=True,
    ),
    OperationDefinition(
        operation=OperationType.REMOVE_ORPHAN_FILES,
        label="Remove orphan files",
        description="Delete unreferenced files after the configured safety interval.",
        destructive=True,
    ),
    OperationDefinition(
        operation=OperationType.CONVERT_EQUALITY_DELETES,
        label="Convert equality deletes",
        description="Replace equality-delete files with position deletes. Data files stay put.",
        destructive=False,
    ),
    OperationDefinition(
        operation=OperationType.CONVERT_DELETES,
        label="Convert deletes",
        description="Rewrite data files to absorb accumulated delete files.",
        destructive=False,
    ),
    OperationDefinition(
        operation=OperationType.OPTIMIZE,
        label="Optimize data files",
        description="Compact small data files using bounded, partition-aware plans.",
        destructive=False,
    ),
    OperationDefinition(
        operation=OperationType.OPTIMIZE_MANIFESTS,
        label="Rewrite manifests",
        description="Rewrite manifest metadata to reduce planning overhead.",
        destructive=False,
    ),
    OperationDefinition(
        operation=OperationType.COMPUTE_STATISTICS,
        label="Compute statistics",
        description="Refresh table and column statistics used by query optimizers.",
        destructive=False,
    ),
    OperationDefinition(
        operation=OperationType.REWRITE_POSITION_DELETES,
        label="Rewrite position deletes",
        description="Run a minor rewrite of position-delete files without full data compaction.",
        destructive=False,
    ),
)

_ENGINES = (
    EngineCapability(
        engine=EngineType.TRINO,
        label="Trino",
        maturity=MaturityTier.GA,
        execution_available=True,
        summary="Direct Iceberg maintenance through Trino SQL procedures.",
        limitations=(
            "No native rewrite_position_delete_files procedure.",
            "convert_deletes uses OPTIMIZE and cannot apply Spark delete-threshold options.",
        ),
        owner="engines/trino",
        evidence=_TRINO_EVIDENCE,
    ),
    EngineCapability(
        engine=EngineType.SPARK_EMR,
        label="Spark / EMR",
        maturity=MaturityTier.GA,
        execution_available=True,
        summary="Full maintenance surface through Iceberg Spark procedures.",
        limitations=("Execution requires a working Spark or Spark-on-Kubernetes destination.",),
        owner="engines/spark",
        evidence=_SPARK_EVIDENCE,
    ),
    EngineCapability(
        engine=EngineType.NATIVE,
        label="Native",
        maturity=MaturityTier.BETA,
        execution_available=True,
        summary="Go subprocess for low-overhead maintenance with explicit SQL fallbacks.",
        limitations=(
            "Native data-file optimization is Go-only and table-shape gated.",
            "Manifest, statistics, and delete rewrites require a configured SQL fallback.",
        ),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    EngineCapability(
        engine=EngineType.FLINK,
        label="Flink",
        maturity=MaturityTier.EXPERIMENTAL,
        execution_available=False,
        summary="Generates Flink maintenance configuration; Cryo does not submit the job.",
        limitations=(
            "execute() reports configuration generation only and performs no external effect.",
        ),
        owner="engines/flink",
        evidence=_FLINK_EVIDENCE,
    ),
)

_CATALOGS = (
    CatalogCapability(
        catalog_type="rest",
        label="Iceberg REST",
        aliases=("iceberg-rest", "iceberg_rest"),
        protocol="Iceberg REST Catalog",
        maturity=MaturityTier.GA,
        capabilities=("namespace discovery", "table discovery", "metadata reads"),
        owner="sources/catalogs",
        evidence=_evidence(
            "cryo/sources/factory.py",
            "catalog_from_config",
            "tests/test_sources_factory.py",
            "test_factory_supports_rest",
        ),
    ),
    CatalogCapability(
        catalog_type="polaris",
        label="Apache Polaris",
        aliases=(),
        protocol="Iceberg REST Catalog",
        maturity=MaturityTier.GA,
        capabilities=("namespace discovery", "table discovery", "metadata reads"),
        limitations=("Uses the shared REST adapter; no Polaris-only API surface.",),
        owner="sources/catalogs",
        evidence=_evidence(
            "cryo/sources/factory.py",
            "REST_SOURCE_TYPES",
            "tests/test_sources_factory.py",
            "test_factory_supports_polaris_and_nessie_as_rest",
        ),
    ),
    CatalogCapability(
        catalog_type="nessie",
        label="Project Nessie",
        aliases=(),
        protocol="Iceberg REST Catalog",
        maturity=MaturityTier.GA,
        capabilities=("namespace discovery", "table discovery", "metadata reads"),
        limitations=("Uses the shared REST adapter; branch operations are not exposed.",),
        owner="sources/catalogs",
        evidence=_evidence(
            "cryo/sources/factory.py",
            "REST_SOURCE_TYPES",
            "tests/test_sources_factory.py",
            "test_factory_supports_polaris_and_nessie_as_rest",
        ),
    ),
    CatalogCapability(
        catalog_type="hive",
        label="Hive Metastore",
        aliases=("hms", "hive-metastore", "hive_metastore"),
        protocol="Hive Metastore Thrift",
        maturity=MaturityTier.GA,
        capabilities=("namespace discovery", "table discovery", "metadata reads"),
        owner="sources/catalogs",
        evidence=_evidence(
            "cryo/sources/factory.py",
            "HIVE_SOURCE_TYPES",
            "tests/test_sources_factory.py",
            "test_factory_supports_hive_aliases",
        ),
    ),
    CatalogCapability(
        catalog_type="glue",
        label="AWS Glue",
        aliases=("aws-glue", "aws_glue"),
        protocol="AWS Glue Data Catalog API",
        maturity=MaturityTier.GA,
        capabilities=("namespace discovery", "table discovery", "metadata reads"),
        limitations=("AWS credentials or an IAM/IRSA role must be available at runtime.",),
        owner="sources/catalogs",
        evidence=_evidence(
            "cryo/sources/factory.py",
            "GLUE_SOURCE_TYPES",
            "tests/test_sources_factory.py",
            "test_factory_supports_glue_aliases",
        ),
    ),
)

_TRINO_CELLS = (
    _cell(
        EngineType.TRINO,
        OperationType.EXPIRE_SNAPSHOTS,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.TRINO_SQL,
        limitations=(
            "Skipped, with a plan note, on tables with snapshots excluded from expiry: Trino "
            "cannot keep a specific snapshot.",
        ),
        fallback_engines=(EngineType.SPARK_EMR,),
        owner="engines/trino",
        evidence=_TRINO_EVIDENCE,
    ),
    _cell(
        EngineType.TRINO,
        OperationType.REMOVE_ORPHAN_FILES,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.TRINO_SQL,
        runtime_conditions=("The configured retention threshold must satisfy safety policy.",),
        owner="engines/trino",
        evidence=_TRINO_EVIDENCE,
    ),
    _cell(
        EngineType.TRINO,
        OperationType.CONVERT_EQUALITY_DELETES,
        SupportStatus.UNSUPPORTED,
        MaturityTier.BLOCKED,
        ExecutionMode.NONE,
        limitations=(
            "Trino has no equality-delete to position-delete procedure. "
            "OPTIMIZE rewrites data files and is not a substitute.",
        ),
        owner="engines/trino",
        evidence=_evidence(
            "cryo/engines/trino/sql.py",
            "_render_operation",
            "tests/test_convert_equality_deletes.py",
            "test_trino_refuses_to_rewrite_data_files",
        ),
    ),
    _cell(
        EngineType.TRINO,
        OperationType.CONVERT_DELETES,
        SupportStatus.CONDITIONAL,
        MaturityTier.BETA,
        ExecutionMode.TRINO_SQL,
        limitations=(
            "Planned, run and recorded as OPTIMIZE, a data-file rewrite; Spark-specific "
            "delete-file thresholds are unavailable.",
        ),
        fallback_engines=(EngineType.SPARK_EMR,),
        owner="engines/trino",
        evidence=_evidence(
            "cryo/engines/trino/sql.py",
            "_render_operation",
            "tests/test_trino_sql_golden.py",
            "test_trino_convert_deletes_reuses_optimize_sql",
        ),
    ),
    _cell(
        EngineType.TRINO,
        OperationType.OPTIMIZE,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.TRINO_SQL,
        runtime_conditions=("Plans remain subject to partition and size budgets.",),
        owner="engines/trino",
        evidence=_TRINO_EVIDENCE,
    ),
    _cell(
        EngineType.TRINO,
        OperationType.OPTIMIZE_MANIFESTS,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.TRINO_SQL,
        owner="engines/trino",
        evidence=_TRINO_EVIDENCE,
    ),
    _cell(
        EngineType.TRINO,
        OperationType.COMPUTE_STATISTICS,
        SupportStatus.SUPPORTED,
        MaturityTier.BETA,
        ExecutionMode.TRINO_SQL,
        limitations=("Bloom-filter table properties are configured only by the Spark renderer.",),
        owner="engines/trino",
        evidence=_evidence(
            "cryo/engines/trino/sql.py",
            "_render_compute_statistics",
            "tests/test_trino_sql_golden.py",
            "test_trino_renders_compute_statistics_specific_columns",
        ),
    ),
    _cell(
        EngineType.TRINO,
        OperationType.EXPIRE_DATA,
        SupportStatus.SUPPORTED,
        MaturityTier.BETA,
        ExecutionMode.TRINO_SQL,
        runtime_conditions=(
            "expire_data is opt-in and requires an approval before it deletes rows.",
            "Partition level only runs when the field is the partition column.",
        ),
        owner="engines/trino",
        evidence=_evidence(
            "cryo/engines/trino/sql.py",
            "_render_operation",
            "tests/test_expire_data.py",
            "test_trino_renders_partition_expire_data",
        ),
    ),
    _cell(
        EngineType.TRINO,
        OperationType.REWRITE_POSITION_DELETES,
        SupportStatus.UNSUPPORTED,
        MaturityTier.BLOCKED,
        ExecutionMode.NONE,
        limitations=("Trino exposes no rewrite_position_delete_files procedure.",),
        fallback_engines=(EngineType.SPARK_EMR,),
        owner="engines/trino",
        evidence=_evidence(
            "cryo/engines/trino/sql.py",
            "_render_operation",
            "tests/test_trino_sql_golden.py",
            "test_trino_rewrite_position_deletes_raises_unsupported",
        ),
    ),
)


def _spark_cell(operation: OperationType) -> OperationCapability:
    if operation is OperationType.CONVERT_EQUALITY_DELETES:
        return _cell(
            EngineType.SPARK_EMR,
            operation,
            SupportStatus.UNSUPPORTED,
            MaturityTier.BLOCKED,
            ExecutionMode.NONE,
            limitations=(
                "Iceberg Spark has no equality-delete to position-delete procedure. "
                "rewrite_data_files rewrites data files and is not a substitute.",
            ),
            owner="engines/spark",
            evidence=_evidence(
                "cryo/engines/spark/sql.py",
                "_render_operation",
                "tests/test_convert_equality_deletes.py",
                "test_spark_refuses_to_rewrite_data_files",
            ),
        )
    return _cell(
        EngineType.SPARK_EMR,
        operation,
        SupportStatus.SUPPORTED,
        (
            MaturityTier.GA
            if operation
            in {
                OperationType.EXPIRE_SNAPSHOTS,
                OperationType.REMOVE_ORPHAN_FILES,
                OperationType.OPTIMIZE,
                OperationType.OPTIMIZE_MANIFESTS,
            }
            else MaturityTier.BETA
        ),
        ExecutionMode.SPARK_JOB,
        runtime_conditions=("A reachable Spark execution destination must be configured.",),
        owner="engines/spark",
        evidence=(
            _evidence(
                "cryo/engines/spark/sql.py",
                "_render_operation",
                "tests/test_spark_sql_golden.py",
                "test_spark_renders_rewrite_position_deletes_operation",
            )
            if operation is OperationType.REWRITE_POSITION_DELETES
            else _SPARK_EVIDENCE
        ),
    )


_SPARK_CELLS = tuple(_spark_cell(operation) for operation in OperationType)

_NATIVE_CELLS = (
    _cell(
        EngineType.NATIVE,
        OperationType.EXPIRE_SNAPSHOTS,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.NATIVE_SUBPROCESS,
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.REMOVE_ORPHAN_FILES,
        SupportStatus.SUPPORTED,
        MaturityTier.GA,
        ExecutionMode.NATIVE_SUBPROCESS,
        runtime_conditions=("The orphan minimum-age guard remains enforced.",),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.CONVERT_EQUALITY_DELETES,
        SupportStatus.UNSUPPORTED,
        MaturityTier.BLOCKED,
        ExecutionMode.NONE,
        limitations=("cryo-engine-go does not yet write the position-delete replacement.",),
        owner="engines/native",
        evidence=_evidence(
            "cryo/planner/equality_deletes.py",
            "plan_convert_equality_deletes",
            "tests/test_convert_equality_deletes.py",
            "test_planner_emits_conversion_before_optimize",
        ),
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.CONVERT_DELETES,
        SupportStatus.FALLBACK_REQUIRED,
        MaturityTier.BETA,
        ExecutionMode.SQL_FALLBACK,
        runtime_conditions=("A compatible SQL fallback destination must be configured.",),
        limitations=("cryo-engine-go does not implement delete conversion.",),
        fallback_engines=(EngineType.TRINO, EngineType.SPARK_EMR),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.OPTIMIZE,
        SupportStatus.CONDITIONAL,
        MaturityTier.BETA,
        ExecutionMode.NATIVE_SUBPROCESS,
        runtime_conditions=(
            "engine_impl must be go.",
            "Scanner stats must report zero delete files.",
            "Partitioned Iceberg v3 tables must not contain deletion vectors.",
            "The plan must not request z-order compaction.",
        ),
        limitations=("Rust native compaction remains blocked upstream.",),
        fallback_engines=(EngineType.TRINO, EngineType.SPARK_EMR),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.OPTIMIZE_MANIFESTS,
        SupportStatus.FALLBACK_REQUIRED,
        MaturityTier.BETA,
        ExecutionMode.SQL_FALLBACK,
        runtime_conditions=("A compatible SQL fallback destination must be configured.",),
        limitations=("cryo-engine-go does not rewrite manifests.",),
        fallback_engines=(EngineType.TRINO, EngineType.SPARK_EMR),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.COMPUTE_STATISTICS,
        SupportStatus.FALLBACK_REQUIRED,
        MaturityTier.BETA,
        ExecutionMode.SQL_FALLBACK,
        runtime_conditions=("A compatible SQL fallback destination must be configured.",),
        limitations=("cryo-engine-go does not write Puffin statistics.",),
        fallback_engines=(EngineType.TRINO, EngineType.SPARK_EMR),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.EXPIRE_DATA,
        SupportStatus.FALLBACK_REQUIRED,
        MaturityTier.BETA,
        ExecutionMode.SQL_FALLBACK,
        runtime_conditions=("A Trino or Spark fallback destination must be configured.",),
        limitations=("cryo-engine-go does not delete expired data files.",),
        fallback_engines=(EngineType.TRINO, EngineType.SPARK_EMR),
        owner="engines/native",
        evidence=_evidence(
            "cryo/engines/expire_data_sql.py",
            "render_expire_data_delete",
            "tests/test_expire_data.py",
            "test_spark_renders_partition_expire_data",
        ),
    ),
    _cell(
        EngineType.NATIVE,
        OperationType.REWRITE_POSITION_DELETES,
        SupportStatus.FALLBACK_REQUIRED,
        MaturityTier.EXPERIMENTAL,
        ExecutionMode.SQL_FALLBACK,
        runtime_conditions=("A Spark fallback destination must be configured.",),
        limitations=("Neither cryo-engine-go nor Trino implements the minor delete rewrite.",),
        fallback_engines=(EngineType.SPARK_EMR,),
        owner="engines/native",
        evidence=_NATIVE_EVIDENCE,
    ),
)

_FLINK_CELLS = tuple(
    _cell(
        EngineType.FLINK,
        operation,
        SupportStatus.CONFIGURATION_ONLY,
        MaturityTier.EXPERIMENTAL,
        ExecutionMode.FLINK_CONFIGURATION,
        runtime_conditions=("An external system must submit and observe the generated Flink job.",),
        limitations=("Cryo performs no maintenance effect and receives no authoritative result.",),
        owner="engines/flink",
        evidence=_FLINK_EVIDENCE,
    )
    for operation in OperationType
)

_MATRIX = _TRINO_CELLS + _SPARK_CELLS + _NATIVE_CELLS + _FLINK_CELLS


def _validate_matrix(matrix: tuple[OperationCapability, ...]) -> None:
    expected = set(product(EngineType, OperationType))
    actual = {(cell.engine, cell.operation) for cell in matrix}
    if len(actual) != len(matrix):
        raise RuntimeError("Capability manifest contains duplicate engine/operation cells")
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise RuntimeError(
            "Capability manifest is not exhaustive; "
            f"missing={sorted(missing)!r}, extra={sorted(extra)!r}"
        )


_validate_matrix(_MATRIX)

CAPABILITY_MANIFEST = CapabilityManifest(
    schema_version="1.1.0",
    source="cryo.capabilities.manifest",
    maturity_definitions=(
        MaturityDefinition(
            tier=MaturityTier.GA,
            meaning="Supported for production use and protected by executable contracts.",
        ),
        MaturityDefinition(
            tier=MaturityTier.BETA,
            meaning="Operational but still gathering production proof or narrowing limitations.",
        ),
        MaturityDefinition(
            tier=MaturityTier.EXPERIMENTAL,
            meaning="Opt-in surface without a production-readiness claim.",
        ),
        MaturityDefinition(
            tier=MaturityTier.BLOCKED,
            meaning="Unavailable because the required engine primitive does not exist.",
        ),
    ),
    operations=_OPERATIONS,
    engines=_ENGINES,
    catalogs=_CATALOGS,
    matrix=_MATRIX,
)

_CAPABILITY_INDEX = {(cell.engine, cell.operation): cell for cell in CAPABILITY_MANIFEST.matrix}


def capability_for(engine: EngineType, operation: OperationType) -> OperationCapability:
    return _CAPABILITY_INDEX[(engine, operation)]


def capabilities_for_engine(engine: EngineType) -> tuple[OperationCapability, ...]:
    return tuple(cell for cell in CAPABILITY_MANIFEST.matrix if cell.engine is engine)
