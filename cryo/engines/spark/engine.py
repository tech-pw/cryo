from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.spark.emr import (
    SUCCESS_STATES,
    EMRServerlessClient,
    EMRServerlessConfig,
)
from cryo.engines.spark.sql import render


@dataclass
class SparkEMREngine:
    """Spark on EMR Serverless implementation of MaintenanceEngine.

    Submits Spark SQL jobs to AWS EMR Serverless and polls for completion.
    """

    config: dict[str, Any]
    settings: Settings | None = None

    type: EngineType = EngineType.SPARK_EMR

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._emr_config = EMRServerlessConfig.from_config(self.config)
        self._client: EMRServerlessClient | None = None
        self._catalog = str(self.config.get("catalog") or "glue_catalog")
        self._slot_cap = int(
            self.config.get("slot_cap")
            or self.config.get("concurrent_jobs")
            or self.settings.default_emr_slot_cap
        )

    @property
    def emr_client(self) -> EMRServerlessClient:
        if self._client is None:
            self._client = EMRServerlessClient(self._emr_config)
        return self._client

    def probe_capacity(self) -> CapacitySnapshot:
        """Probe EMR Serverless capacity.

        For EMR Serverless, we use a configured slot cap since the service
        auto-scales. The 'used' count would require listing running jobs,
        which we can implement later for more accurate budgeting.
        """
        return CapacitySnapshot(capacity=self._slot_cap, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        """Render maintenance plan to Spark SQL CALL statements."""
        return render(plan)

    def session_setup(self) -> list[str]:
        """No session setup needed for EMR Serverless - config is in spark-submit."""
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        """Submit SQL to EMR Serverless and wait for completion."""
        job_name = _build_job_name(stmt, ctx)
        start = time.monotonic()

        try:
            job_run_id = self.emr_client.submit_spark_sql(
                stmt.sql,
                job_name=job_name,
                catalog=self._catalog,
            )
            result = self.emr_client.wait_for_completion(job_run_id)

            duration_ms = _elapsed_ms(start)
            if result.state in SUCCESS_STATES:
                return ExecResult(
                    ok=True,
                    engine_ref=job_run_id,
                    detail=stmt.sql,
                    duration_ms=duration_ms,
                )
            return ExecResult(
                ok=False,
                engine_ref=job_run_id,
                detail=stmt.sql,
                error=f"EMR job {result.state.value}: {result.state_details or 'no details'}",
                duration_ms=duration_ms,
            )
        except TimeoutError as exc:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=str(exc),
                duration_ms=_elapsed_ms(start),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=f"EMR submission failed: {exc}",
                duration_ms=_elapsed_ms(start),
            )


def _build_job_name(stmt: RenderedStatement, ctx: ExecCtx | None) -> str:
    parts = ["cryo", stmt.action.value.lower()]
    if ctx and ctx.job_id:
        parts.append(str(ctx.job_id)[:8])
    return "-".join(parts)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
