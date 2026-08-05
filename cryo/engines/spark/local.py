from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.spark.sql import render


@dataclass
class SparkLocalEngine:
    """Execute Spark SQL via the local docker-compose spark container."""

    config: dict[str, Any]
    settings: Settings | None = None

    type: EngineType = EngineType.SPARK_EMR

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._container = str(self.config.get("container") or "cryo-spark")
        self._catalog = str(self.config.get("catalog") or "iceberg")
        self._slot_cap = int(self.config.get("slot_cap") or 3)

    def probe_capacity(self) -> CapacitySnapshot:
        return CapacitySnapshot(capacity=self._slot_cap, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return render(plan)

    def session_setup(self) -> list[str]:
        return []

    def execute_plan(self, plan: MaintenancePlan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        """Execute each rendered statement in order, short-circuiting on failure."""
        results: list[ExecResult] = []
        for stmt in self.render(plan):
            result = self.execute(stmt, ctx)
            results.append(result)
            if not result.ok:
                break
        return results

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        start = time.monotonic()
        command = [
            "docker",
            "compose",
            "-f",
            "docker-compose.dev-datalake.yml",
            "--profile",
            "tools",
            "exec",
            "-T",
            "spark",
            "python3",
            "/opt/cryo/cryo_spark_runner.py",
            "--sql",
            stmt.sql,
            "--catalog",
            self._catalog,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=int(self.config.get("timeout_seconds") or 3600),
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            if completed.returncode == 0:
                return ExecResult(ok=True, detail=stmt.sql, duration_ms=duration_ms)
            error = completed.stderr.strip() or completed.stdout.strip() or "spark local failed"
            return ExecResult(ok=False, detail=stmt.sql, error=error, duration_ms=duration_ms)
        except FileNotFoundError:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="docker compose not available for local Spark engine",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                ok=False,
                outcome_known=False,
                detail=stmt.sql,
                error="local Spark execution timed out",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
