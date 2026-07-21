from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cryo.domain.enums import EngineType

if TYPE_CHECKING:
    from cryo.engines.base import MaintenanceEngine


def create_engine(engine_type: EngineType | str, config: Mapping[str, Any]) -> MaintenanceEngine:
    """Factory function to create the appropriate maintenance engine.

    Args:
        engine_type: The type of engine to create (TRINO or SPARK_EMR)
        config: Engine-specific configuration dict

    Returns:
        A MaintenanceEngine implementation

    Raises:
        ValueError: If the engine type is not supported
    """
    engine_type = EngineType(engine_type) if isinstance(engine_type, str) else engine_type

    if engine_type is EngineType.TRINO:
        from cryo.engines.trino.engine import TrinoEngine

        return TrinoEngine(config=dict(config))

    if engine_type is EngineType.SPARK_EMR:
        if config.get("local") or config.get("engine_mode") == "local":
            from cryo.engines.spark.local import SparkLocalEngine

            return SparkLocalEngine(config=dict(config))
        if str(config.get("submit_mode") or "").strip().lower() == "k8s":
            from cryo.engines.spark.k8s_engine import SparkK8sEngine

            return SparkK8sEngine(config=dict(config))
        from cryo.engines.spark.engine import SparkEMREngine

        return SparkEMREngine(config=dict(config))

    if engine_type is EngineType.NATIVE:
        from cryo.config import get_settings
        from cryo.engines.native.engine import NativeEngine, is_native_available
        from cryo.engines.native.runner import verify_cryo_engine_spawnable

        settings = get_settings()
        if not settings.native_engine_enabled:
            raise ValueError(
                "Native engine is disabled. Set CRYO_NATIVE_ENGINE_ENABLED=true to enable."
            )
        if not is_native_available(config):
            raise ValueError(
                "cryo-engine binary not found. "
                "Build cryo-engine or set destination.config.binary_path."
            )
        spawn_error = verify_cryo_engine_spawnable(config)
        if spawn_error is not None:
            raise ValueError(spawn_error)
        return NativeEngine(config=dict(config))

    if engine_type is EngineType.FLINK:
        from cryo.config import get_settings
        from cryo.engines.flink.engine import FlinkEngine

        settings = get_settings()
        if not settings.flink_engine_enabled:
            raise ValueError(
                "Flink engine is disabled by default — it generates deploy configs only. "
                "Set CRYO_FLINK_ENGINE_ENABLED=true to select it."
            )
        return FlinkEngine(config=dict(config))

    raise ValueError(f"Unsupported engine type: {engine_type}")


def engine_from_destination(
    destination_id: str, engine: EngineType | str, config: Mapping[str, Any]
) -> MaintenanceEngine:
    """Create an engine from destination configuration.

    This is the primary factory function used by the worker.
    """
    return create_engine(engine, config)
