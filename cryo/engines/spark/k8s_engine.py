from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement, truncate_error_message
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
        catalog = self.config.get("catalog")
        self._catalog = str(catalog) if catalog else None
        self._slot_cap = int(self.config.get("slot_cap") or self.config.get("concurrent_jobs") or 3)
        destination_id = self.config.get("destination_id")
        self._destination_id = str(destination_id) if destination_id else None

    @property
    def k8s_client(self) -> SparkK8sClient:
        if self._client is None:
            self._client = SparkK8sClient(self._k8s_config)
        return self._client

    def with_source_config(self, source_config: Mapping[str, Any]) -> SparkK8sEngine:
        """Fall back to the table's source-configured S3 keys when this destination has none.

        The native engine has done this since day one (cryo/engines/native/payload.py's
        source_catalog_config) and the worker dispatch loop already calls
        engine.with_source_config(...) generically on any engine that defines it (see
        cryo.worker.worker._dispatch_job) -- Spark-on-K8s just never implemented the hook.
        Confirmed live, 2026-07-28: a table whose source was configured with static
        s3.access-key-id/s3.secret-access-key (the operator's deliberate "keys, not IAM role"
        choice) still had its Spark-K8s jobs fall through to the pod's ambient node IAM role,
        which lacked s3:GetObject on that table's bucket -- AccessDeniedException. An explicit
        s3_access_key/s3_secret_key already set on this destination's own config is an operator
        override and wins; this only fills the gap when the destination has none configured.
        """
        access_key = source_config.get("s3.access-key-id")
        secret_key = source_config.get("s3.secret-access-key")
        if not (isinstance(access_key, str) and access_key and isinstance(secret_key, str) and secret_key):
            return self
        if self._k8s_config.s3_access_key and self._k8s_config.s3_secret_key:
            return self
        new_engine = SparkK8sEngine(config=self.config, settings=self.settings)
        new_engine._k8s_config = replace(
            new_engine._k8s_config, s3_access_key=access_key, s3_secret_key=secret_key
        )
        return new_engine

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
        job_id: str | None = None

        # A destination's `catalog` is a static default -- correct only for tables whose own
        # catalog_name matches it. A shared SQL-fallback destination routes tables from
        # different catalogs (e.g. a REST-catalog "cdp" table and a Hive-Metastore "hive"
        # table both falling back to the same Spark destination), so the statement's own
        # catalog (set at render time from the table's real table_ref) must win when present
        # -- confirmed live, 2026-07-24: submitting with the destination's default "cdp" while
        # the SQL referenced "hive.system.rewrite_data_files" left no "hive" catalog plugin
        # registered in the Spark session, and Spark silently tried to resolve it against the
        # builtin spark_catalog instead, which doesn't support procedures at all.
        catalog = stmt.metadata.get("catalog") or self._catalog
        if not catalog:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=(
                    "No Spark catalog name available for this dispatch -- neither the "
                    "rendered statement nor this destination's config specifies one. Set "
                    "the table's source catalog_name or this destination's `catalog`."
                ),
                duration_ms=_elapsed_ms(start),
            )
        try:
            job_id = self.k8s_client.submit_spark_sql(
                stmt.sql,
                job_name=job_name,
                catalog=str(catalog),
                destination_id=self._destination_id,
            )
            if ctx is not None and ctx.on_engine_ref is not None:
                ctx.on_engine_ref(job_id)
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
                error=truncate_error_message(
                    f"Spark-on-K8s Job {result.state.value}: "
                    f"{result.state_details or 'no details'}"
                ),
                duration_ms=duration_ms,
            )
        except TimeoutError as exc:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=truncate_error_message(str(exc)),
                duration_ms=_elapsed_ms(start),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=truncate_error_message(f"Spark-on-K8s submission failed: {exc}"),
                duration_ms=_elapsed_ms(start),
            )
        finally:
            # Best-effort: don't leave per-job AWS credential Secrets lying around once
            # the run is terminal (or timed out/raised).
            if job_id is not None:
                self.k8s_client.cleanup_credentials_secret(job_id)


def _build_job_name(stmt: RenderedStatement, ctx: ExecCtx | None) -> str:
    parts = ["cryo", stmt.action.value.lower()]
    if ctx and ctx.job_id:
        parts.append(str(ctx.job_id)[:8])
    return "-".join(parts)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
