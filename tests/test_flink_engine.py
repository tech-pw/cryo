"""Tests for Flink engine configuration generation."""

from __future__ import annotations

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.flink.config import FlinkMaintenanceConfig
from cryo.engines.flink.engine import FlinkEngine


def test_flink_engine_creation():
    """Test creating a Flink engine instance."""
    config = {
        "catalog": "iceberg",
        "catalog_type": "rest",
        "catalog_uri": "http://localhost:8181",
    }
    engine = FlinkEngine(config=config)

    assert engine.type == EngineType.FLINK
    assert engine._catalog == "iceberg"
    assert engine._catalog_type == "rest"
    assert engine._catalog_uri == "http://localhost:8181"


def test_flink_engine_probe_capacity():
    """Test capacity probe returns unlimited slots."""
    engine = FlinkEngine(config={})
    capacity = engine.probe_capacity()

    assert capacity.capacity == 999
    assert capacity.used == 0


def test_flink_config_from_cryo_params():
    """Test converting Cryo parameters to Flink config."""
    params = {
        "target_file_size_bytes": 536870912,
        "min_small_files": 10,
        "retention_days": 14,
    }

    config = FlinkMaintenanceConfig.from_cryo_params(
        table_ref="bronze.events",
        catalog_name="iceberg",
        catalog_type="rest",
        catalog_uri="http://localhost:8181",
        params=params,
    )

    assert config.table_ref == "bronze.events"
    assert config.catalog_name == "iceberg"
    assert config.catalog_type == "rest"
    assert config.rewrite_target_file_size_bytes == 536870912
    assert config.rewrite_min_input_files == 10
    assert config.expire_max_snapshot_age_ms == 14 * 24 * 3600 * 1000


def test_flink_java_api_generation():
    """Test generating Java API code for TableMaintenance."""
    config = FlinkMaintenanceConfig(
        catalog_name="iceberg",
        catalog_type="rest",
        catalog_uri="http://localhost:8181",
        table_ref="bronze.events",
        rewrite_enabled=True,
        expire_enabled=True,
        orphan_enabled=False,
        jdbc_url="jdbc:postgresql://localhost:5432/cryo",
    )

    java_code = config.to_java_api()

    # Verify key components are present
    assert "import org.apache.iceberg.flink.maintenance.api" in java_code
    assert "CatalogLoader.rest" in java_code
    assert "iceberg" in java_code
    assert "http://localhost:8181" in java_code
    assert "TableIdentifier.of" in java_code
    assert "bronze" in java_code
    assert "events" in java_code
    assert "JdbcLockFactory.of" in java_code
    assert "jdbc:postgresql://localhost:5432/cryo" in java_code
    assert "RewriteDataFiles.builder()" in java_code
    assert "ExpireSnapshots.builder()" in java_code
    assert "DeleteOrphanFiles" not in java_code  # Orphan cleanup disabled
    assert "env.execute" in java_code


def test_flink_sql_generation():
    """Test generating Flink SQL configuration."""
    config = FlinkMaintenanceConfig(
        catalog_name="iceberg",
        catalog_type="rest",
        catalog_uri="http://localhost:8181",
        table_ref="bronze.events",
        rewrite_enabled=True,
        expire_enabled=True,
        orphan_enabled=False,
    )

    sql_config = config.to_flink_sql()

    # Verify key settings are present
    assert "SET 'table.exec.iceberg.use.v2.sink' = 'true'" in sql_config
    assert "SET 'compaction-enabled' = 'true'" in sql_config
    assert "SET 'flink-maintenance.rewrite.target-file-size-bytes'" in sql_config
    assert "SET 'flink-maintenance.expire-snapshots.enabled' = 'true'" in sql_config
    assert "delete-orphan-files" not in sql_config  # Orphan cleanup disabled
    assert "INSERT INTO iceberg.bronze.events" in sql_config


def test_flink_engine_render():
    """Test rendering a maintenance plan to Flink configuration."""
    engine = FlinkEngine(
        config={
            "catalog": "iceberg",
            "catalog_type": "rest",
            "catalog_uri": "http://localhost:8181",
            "jdbc_url": "jdbc:postgresql://localhost:5432/cryo",
        }
    )

    plan = MaintenancePlan(
        table_ref="iceberg.bronze.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.OPTIMIZE, {"target_file_size_bytes": 536870912}),
        ),
    )

    statements = engine.render(plan)

    assert len(statements) == 1
    stmt = statements[0]
    assert stmt.action == OperationType.OPTIMIZE
    assert stmt.engine == EngineType.FLINK
    assert "FLINK JAVA API" in stmt.sql
    assert "FLINK SQL" in stmt.sql
    assert "bronze.events" in stmt.sql


def test_flink_engine_execute():
    """Test executing (logging) a Flink configuration."""
    engine = FlinkEngine(config={})

    plan = MaintenancePlan(
        table_ref="iceberg.bronze.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"target_file_size_bytes": 536870912}),),
    )

    statements = engine.render(plan)
    result = engine.execute(statements[0])

    assert result.ok is True
    assert "Flink TableMaintenance config generated" in result.detail
    assert result.duration_ms >= 0


def test_flink_config_glue_catalog():
    """Test Glue catalog configuration."""
    config = FlinkMaintenanceConfig(
        catalog_name="glue_catalog",
        catalog_type="glue",
        catalog_uri=None,
        table_ref="bronze.events",
    )

    java_code = config.to_java_api()

    assert "CatalogLoader.custom" in java_code
    assert "GlueCatalog" in java_code
    assert "glue_catalog" in java_code


def test_flink_config_no_lock_warning():
    """Test warning when no lock factory configured."""
    config = FlinkMaintenanceConfig(
        catalog_name="iceberg",
        catalog_type="rest",
        catalog_uri="http://localhost:8181",
        table_ref="bronze.events",
        jdbc_url=None,
    )

    java_code = config.to_java_api()

    assert "WARNING: No lock factory configured" in java_code
    assert "TriggerLockFactory lockFactory = null" in java_code


def test_flink_config_with_all_operations():
    """Test config with all maintenance operations enabled."""
    config = FlinkMaintenanceConfig(
        catalog_name="iceberg",
        catalog_type="rest",
        catalog_uri="http://localhost:8181",
        table_ref="bronze.events",
        rewrite_enabled=True,
        expire_enabled=True,
        orphan_enabled=True,
        jdbc_url="jdbc:postgresql://localhost:5432/cryo",
    )

    java_code = config.to_java_api()

    assert "RewriteDataFiles.builder()" in java_code
    assert "ExpireSnapshots.builder()" in java_code
    assert "DeleteOrphanFiles.builder()" in java_code
