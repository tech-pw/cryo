"""Flink engine for Cryo.

**EXPERIMENTAL — CONFIG GENERATION ONLY.** Does not submit jobs to a Flink cluster.
Generates Flink ``TableMaintenance`` configurations that you must deploy yourself
as a Flink streaming job alongside an Iceberg Sink V2 pipeline. Not part of the v1
launch surface; see ``docs/OSS_READINESS.md`` §2.2.

Opt-in gated by ``Settings.flink_engine_enabled`` (defaults to ``False``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.flink.config import FlinkMaintenanceConfig


@dataclass
class FlinkEngine:
    """Flink engine — generates TableMaintenance configurations.

    Unlike Trino/Spark engines that execute SQL directly, the Flink engine
    generates Java API code and Flink SQL configuration that teams deploy
    alongside their streaming pipelines. This allows Cryo to manage the
    maintenance policy while Flink executes it natively.

    Ideal for teams with CDC/streaming Iceberg writes who want maintenance
    co-located with their Flink jobs, without a separate Spark cluster.
    """

    config: dict[str, Any]
    settings: Settings | None = None

    type: EngineType = EngineType.FLINK

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._catalog = str(self.config.get("catalog", "iceberg"))
        self._catalog_type = str(self.config.get("catalog_type", "rest"))
        self._catalog_uri = self.config.get("catalog_uri")
        self._jdbc_url = self.config.get("jdbc_url")

    def probe_capacity(self) -> CapacitySnapshot:
        """Flink engine doesn't consume Cryo slots — it runs as a Flink job."""
        return CapacitySnapshot(capacity=999, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        """Render a maintenance plan as a Flink TableMaintenance configuration."""
        flink_config = FlinkMaintenanceConfig.from_cryo_params(
            table_ref=self._table_name_only(plan.table_ref),
            catalog_name=self._catalog,
            catalog_type=self._catalog_type,
            catalog_uri=self._catalog_uri,
            params=self._extract_params(plan),
        )

        if self._jdbc_url:
            flink_config.jdbc_url = self._jdbc_url

        # Determine which operations are in the plan
        op_types = {op.type for op in plan.operations}
        flink_config.rewrite_enabled = OperationType.OPTIMIZE in op_types
        flink_config.expire_enabled = OperationType.EXPIRE_SNAPSHOTS in op_types
        flink_config.orphan_enabled = OperationType.REMOVE_ORPHAN_FILES in op_types

        # Generate both Java API and Flink SQL configurations
        java_code = flink_config.to_java_api()
        sql_config = flink_config.to_flink_sql()

        combined = (
            "=== FLINK JAVA API (deploy as standalone Flink job) ===\n"
            f"{java_code}\n\n"
            "=== FLINK SQL (for post-commit topology) ===\n"
            f"{sql_config}"
        )

        return [
            RenderedStatement(
                action=OperationType.OPTIMIZE,  # Represents the full maintenance plan
                sql=combined,
                engine=EngineType.FLINK,
            )
        ]

    def session_setup(self) -> list[str]:
        """No session setup for Flink engine."""
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        """Flink engine doesn't execute directly — it generates configuration.

        In practice, the generated config is written to a file or shown to
        the operator who then deploys the Flink job.
        """
        start = time.monotonic()

        # Log the generated configuration
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Flink maintenance config generated:\n{stmt.sql[:500]}...")

        return ExecResult(
            ok=False,
            detail="Flink is config-only. Cryo does not run maintenance on this destination.",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def execute_plan(self, plan: MaintenancePlan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        """Execute a plan by running each rendered statement.

        Flink generates config rather than executing, so this delegates to
        :meth:`execute` for each statement and returns the collected results.
        """
        return [self.execute(stmt, ctx) for stmt in self.render(plan)]

    def get_config(self, plan: MaintenancePlan) -> FlinkMaintenanceConfig:
        """Get the FlinkMaintenanceConfig for a plan (for direct inspection)."""
        return FlinkMaintenanceConfig.from_cryo_params(
            table_ref=self._table_name_only(plan.table_ref),
            catalog_name=self._catalog,
            catalog_type=self._catalog_type,
            catalog_uri=self._catalog_uri,
            params=self._extract_params(plan),
        )

    def _table_name_only(self, table_ref: str) -> str:
        """Strip catalog prefix from table_ref."""
        parts = table_ref.split(".", 1)
        if len(parts) == 3:
            return ".".join(parts[1:])
        if len(parts) >= 2:
            return table_ref.split(".", 1)[1]
        return table_ref

    def _extract_params(self, plan: MaintenancePlan) -> dict[str, Any]:
        """Extract parameters from the first OPTIMIZE operation in the plan."""
        for op in plan.operations:
            if op.type is OperationType.OPTIMIZE:
                return dict(op.params)
            if op.type is OperationType.EXPIRE_SNAPSHOTS:
                return dict(op.params)
        return {}
