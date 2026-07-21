from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.spark.k8s import (
    SUCCESS_STATES,
    SparkK8sClient,
    SparkK8sConfig,
)
from cryo.engines.spark.sql import render


@dataclass
class SparkK8sEngine:
    """Cryo-managed Spark on Kubernetes (spark-submit, cluster mode).

    No cloud-vendor Spark service required -- runs on any Kubernetes cluster via
    Cryo's own image and RBAC (deploy/helm/cryo/templates/rbac-spark.yaml). Intended
    as the SQL fallback destination for tables the native engine can't safely
    handle (e.g. delete-heavy MOR tables -- see cryo.engines.native.routing).
    """

    config: dict[str, Any]
    settings: Settings | None = None

    type: EngineType = EngineType.SPARK_EMR

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._k8s_config = SparkK8sConfig.from_config(self.config)
        self._client: SparkK8sClient | None = None
        self._catalog = str(self.config.get("catalog") or "cdp")
        self._slot_cap = int(self.config.get("slot_cap") or self.config.get("concurrent_jobs") or 3)

    @property
    def k8s_client(self) -> SparkK8sClient:
        if self._client is None:
            self._client = SparkK8sClient(self._k8s_config)
        return self._client

    def probe_capacity(self) -> CapacitySnapshot:
        """Static slot cap -- big-table fallback jobs are rare/occasional, not a hot path."""
        return CapacitySnapshot(capacity=self._slot_cap, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return render(plan)

    def session_setup(self) -> list[str]:
        return []

    def execute_plan(
        self, plan: MaintenancePlan, ctx: ExecCtx | None = None
    ) -> list[ExecResult]:
        results: list[ExecResult] = []
        for stmt in self.render(plan):
            result = self.execute(stmt, ctx)
            results.append(result)
            if not result.ok:
                break
        return results

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        job_name = _build_job_name(stmt, ctx)
        start = time.monotonic()

        try:
            job_id = self.k8s_client.submit_spark_sql(
                stmt.sql,
                job_name=job_name,
                catalog=self._catalog,
            )
            result = self.k8s_client.wait_for_completion(job_id)

            duration_ms = _elapsed_ms(start)
            if result.state in SUCCESS_STATES:
                return ExecResult(
                    ok=True,
                    engine_ref=job_id,
                    detail=stmt.sql,
                    duration_ms=duration_ms,
                )
            return ExecResult(
                ok=False,
                engine_ref=job_id,
                detail=stmt.sql,
                error=f"Spark-on-K8s Job {result.state.value}: {result.state_details or 'no details'}",
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
                error=f"Spark-on-K8s submission failed: {exc}",
                duration_ms=_elapsed_ms(start),
            )


def _build_job_name(stmt: RenderedStatement, ctx: ExecCtx | None) -> str:
    parts = ["cryo", stmt.action.value.lower()]
    if ctx and ctx.job_id:
        parts.append(str(ctx.job_id)[:8])
    return "-".join(parts)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
