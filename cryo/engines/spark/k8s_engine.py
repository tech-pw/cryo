from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import (
    TABLE_NOT_FOUND_MARKER,
    ExecCtx,
    ExecResult,
    RenderedStatement,
    is_table_not_found_error,
    truncate_error_message,
)
from cryo.engines.spark.k8s import (
    SUCCESS_STATES,
    SparkK8sClient,
    SparkK8sConfig,
)
from cryo.engines.spark.sizing import (
    cap_deadline_seconds_for_table,
    scale_executor_profile,
    spark_slot_ceiling,
)
from cryo.engines.spark.sql import render
from cryo.engines.spark.worker_guard import (
    footprint as spark_payload_footprint,
)
from cryo.engines.spark.worker_guard import (
    log_spark_payload,
    process_rss_mib,
    validate_spark_plan_scope,
)

logger = logging.getLogger(__name__)

#  Pattern 2: Spark-on-K8s gold-table failures that used to look like a truncated
# `BaseMetastoreTableOperations.current` stack are, after deepest_caused_by, NoSuchKey on
# the catalog's current metadata pointer -- typically a stale HMS/REST location after a
# concurrent expire, not a dropped table. A short retry with backoff covers that race.
# CommitFailedException / Iceberg ValidationException get the same treatment. Dropped-table
# signatures must *not* retry (worker deactivates those).
_SPARK_CATALOG_RETRY_ATTEMPTS = 3
_SPARK_CATALOG_RETRY_BACKOFF_SECONDS = 10
_SPARK_RETRYABLE_SIGNATURES = (
    "BaseMetastoreTableOperations",
    "CommitFailedException",
    "CommitStateUnknownException",
    "org.apache.iceberg.exceptions.ValidationException",
)
_SPARK_NON_RETRYABLE_SIGNATURES = (
    "NoSuchTableException",
    "TABLE_OR_VIEW_NOT_FOUND",
    "TableNotFoundException",
    TABLE_NOT_FOUND_MARKER,
)


def is_spark_retryable_catalog_error(message: str | None) -> bool:
    """True when a Spark driver failure is worth one more attempt against the catalog."""
    if not message:
        return False
    if any(sig in message for sig in _SPARK_NON_RETRYABLE_SIGNATURES):
        return False
    return any(sig in message for sig in _SPARK_RETRYABLE_SIGNATURES)


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
        self._slot_cap = spark_slot_ceiling(
            int(self.config.get("slot_cap") or self.config.get("concurrent_jobs") or 3)
        )
        destination_id = self.config.get("destination_id")
        self._destination_id = str(destination_id) if destination_id else None
        self._table_stats: Mapping[str, Any] | None = None

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
        if not (
            isinstance(access_key, str)
            and access_key
            and isinstance(secret_key, str)
            and secret_key
        ):
            return self
        if self._k8s_config.s3_access_key and self._k8s_config.s3_secret_key:
            return self
        new_engine = SparkK8sEngine(config=self.config, settings=self.settings)
        new_engine._k8s_config = replace(
            new_engine._k8s_config, s3_access_key=access_key, s3_secret_key=secret_key
        )
        new_engine._table_stats = self._table_stats
        return new_engine

    def with_table_stats(self, table_stats: Mapping[str, Any] | None) -> SparkK8sEngine:
        """Scale this destination's configured executor profile down for small tables.

        Confirmed live, 2026-08-03: the destination's configured executor_instances/cpu/mem
        is one static profile applied to every job regardless of table size -- a table with
        a dozen files reserved the same 76 vCPU / 160 GiB as a concurrently-running 24TB
        table, which is what actually needs that scale. 86% of the cryo namespace's cost
        showed as "unused" (reserved, not utilized) in Grafana for exactly this reason.
        Opt out per-destination with spark_k8s.dynamic_sizing=false if a workload needs the
        full configured profile regardless of table size.
        """
        if table_stats is None:
            return self
        new_engine = SparkK8sEngine(config=self.config, settings=self.settings)
        new_engine._table_stats = dict(table_stats)
        if self.config.get("spark_k8s", {}).get("dynamic_sizing") is False:
            new_engine._k8s_config = self._k8s_config
            return new_engine
        total_size_gb = table_stats.get("total_size_gb")
        profile = scale_executor_profile(
            total_size_gb=float(total_size_gb) if total_size_gb is not None else None,
            max_executor_instances=self._k8s_config.executor_instances,
            max_executor_cpu_milli=self._k8s_config.executor_cpu_milli,
            max_executor_mem_mib=self._k8s_config.executor_mem_mib,
            max_driver_cpu_milli=self._k8s_config.driver_cpu_milli,
            max_driver_mem_mib=self._k8s_config.driver_mem_mib,
        )
        new_engine._k8s_config = replace(
            self._k8s_config,
            executor_instances=profile.executor_instances,
            executor_cpu_milli=profile.executor_cpu_milli,
            executor_mem_mib=profile.executor_mem_mib,
            driver_cpu_milli=profile.driver_cpu_milli,
            driver_mem_mib=profile.driver_mem_mib,
        )
        return new_engine

    def probe_capacity(self) -> CapacitySnapshot:
        """Static slot cap -- big-table fallback jobs are rare/occasional, not a hot path."""
        return CapacitySnapshot(capacity=self._slot_cap, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return render(plan)

    def session_setup(self) -> list[str]:
        return []

    def execute_plan(self, plan: MaintenancePlan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        snap = spark_payload_footprint(plan=plan, stats=self._table_stats)
        validate_spark_plan_scope(plan, stats=self._table_stats)
        log_spark_payload(
            stage="execute_plan:before",
            table_ref=plan.table_ref,
            footprint_snap=snap,
            extra="scope-preserved" if snap.outlier_reason else "",
        )
        results: list[ExecResult] = []
        for stmt in self.render(plan):
            result = self.execute(stmt, ctx)
            results.append(result)
            if not result.ok:
                break
        log_spark_payload(
            stage="execute_plan:after",
            table_ref=plan.table_ref,
            footprint_snap=spark_payload_footprint(plan=plan, stats=self._table_stats),
            extra=f"ok={sum(1 for r in results if r.ok)}/{len(results)} rss={process_rss_mib()}",
        )
        return results

    def submit(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        """Fire-and-forget: submit the K8s Job and return immediately, without waiting for
        completion or cleaning up its credentials Secret (that Secret is still in use while
        the job runs -- only a caller who eventually reaches the job's terminal state should
        clean it up, see get_job_run()/cleanup_credentials_secret()).

        Built for : run_wave()'s fallback dispatch used to call execute() (submit +
        block on wait_for_completion()) inline for every native-unsupported operation --
        confirmed live, 2026-08-11, this made the fallback destination's own slot_cap (e.g. 6)
        the effective ceiling on the whole Governor's per-wave throughput, independent of the
        native engine's own much higher slot_cap (e.g. 80): 7,855 deep-mode jobs queued and
        barely draining. Returns ok=True with outcome_known=False on a clean submission --
        the caller must track engine_ref and resolve the real outcome later (see
        cryo.governor.fallback_sweep), not treat a submitted-but-unresolved dispatch as
        either success or failure.
        """
        job_name = _build_job_name(stmt, ctx)
        start = time.monotonic()
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
            table_size_gb = (self._table_stats or {}).get("total_size_gb")
            job_id = self.k8s_client.submit_spark_sql(
                stmt.sql,
                job_name=job_name,
                catalog=str(catalog),
                destination_id=self._destination_id,
                operation=stmt.action.value,
                total_size_gb=float(table_size_gb) if table_size_gb is not None else None,
            )
            if ctx is not None and ctx.on_engine_ref is not None:
                ctx.on_engine_ref(job_id)
            return ExecResult(
                ok=True,
                outcome_known=False,
                engine_ref=job_id,
                detail=stmt.sql,
                duration_ms=_elapsed_ms(start),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                outcome_known=False,
                detail=stmt.sql,
                error=truncate_error_message(f"Spark-on-K8s submission failed: {exc}"),
                duration_ms=_elapsed_ms(start),
            )

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        job_name = _build_job_name(stmt, ctx)
        start = time.monotonic()
        job_id: str | None = None

        # A destination's `catalog` is a static default -- correct only for tables whose own
        # catalog_name matches it. A shared SQL-fallback destination routes tables from
        # different catalogs (e.g. a REST-catalog "rest_catalog" table and a Hive-Metastore "hive"
        # table both falling back to the same Spark destination), so the statement's own
        # catalog (set at render time from the table's real table_ref) must win when present
        # -- confirmed live, 2026-07-24: submitting with the destination's default
        # "rest_catalog" while
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
            for attempt in range(_SPARK_CATALOG_RETRY_ATTEMPTS):
                attempt_name = job_name if attempt == 0 else f"{job_name}-r{attempt}"
                logger.info(
                    "spark-k8s execute submit action=%s sql_bytes=%d rss=%s MiB attempt=%d",
                    stmt.action.value,
                    len(stmt.sql),
                    process_rss_mib(),
                    attempt + 1,
                )
                raw_table_size_gb = (self._table_stats or {}).get("total_size_gb")
                table_size_gb = float(raw_table_size_gb) if raw_table_size_gb is not None else None
                job_id = self.k8s_client.submit_spark_sql(
                    stmt.sql,
                    job_name=attempt_name,
                    catalog=str(catalog),
                    destination_id=self._destination_id,
                    operation=stmt.action.value,
                    total_size_gb=table_size_gb,
                )
                if ctx is not None and ctx.on_engine_ref is not None:
                    ctx.on_engine_ref(job_id)
                # Same cap submit_spark_sql applied to the Job's own activeDeadlineSeconds --
                # must match, or this poll loop times out at a different wall-clock point than
                # Kubernetes will actually kill the Job at. See wait_for_completion's docstring.
                deadline_seconds = cap_deadline_seconds_for_table(
                    self._k8s_config.active_deadline_seconds, total_size_gb=table_size_gb
                )
                result = self.k8s_client.wait_for_completion(
                    job_id, deadline_seconds=deadline_seconds
                )

                duration_ms = _elapsed_ms(start)
                if result.state in SUCCESS_STATES:
                    return ExecResult(
                        ok=True,
                        engine_ref=job_id,
                        detail=stmt.sql,
                        duration_ms=duration_ms,
                        actual_bytes_freed=result.bytes_removed,
                    )
                state_details = result.state_details or "no details"
                # A driver's raw log tail is a full Java/Scala stack trace easily over 4000 chars,
                # with "Caused by: NoSuchTableException" typically buried well past the head+tail
                # window truncate_error_message keeps -- confirmed live, 2026-07-31: the signature
                # was in the untruncated log but never reached the stored error, so nothing could
                # detect "this table was dropped" downstream. Front-load the signature before
                # truncating so head-first truncation always keeps it, regardless of where in the
                # original text it actually occurred.
                if is_table_not_found_error(state_details):
                    state_details = f"{TABLE_NOT_FOUND_MARKER} {state_details}"
                can_retry = (
                    attempt < _SPARK_CATALOG_RETRY_ATTEMPTS - 1
                    and is_spark_retryable_catalog_error(state_details)
                )
                if can_retry:
                    self.k8s_client.cleanup_after_run(job_id)
                    job_id = None
                    time.sleep(_SPARK_CATALOG_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                return ExecResult(
                    ok=False,
                    engine_ref=job_id,
                    detail=stmt.sql,
                    error=truncate_error_message(
                        f"Spark-on-K8s Job {result.state.value}: {state_details}"
                    ),
                    duration_ms=duration_ms,
                )
            raise RuntimeError("spark-k8s execute retry loop exited without a result")
        except TimeoutError as exc:
            return ExecResult(
                ok=False,
                outcome_known=False,
                engine_ref=job_id,
                detail=stmt.sql,
                error=truncate_error_message(str(exc)),
                duration_ms=_elapsed_ms(start),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                outcome_known=False,
                engine_ref=job_id,
                detail=stmt.sql,
                error=truncate_error_message(f"Spark-on-K8s submission failed: {exc}"),
                duration_ms=_elapsed_ms(start),
            )
        finally:
            # Best-effort: Spark leaves completed driver pods (and our AWS credential
            # Secrets) behind -- Job TTL does not cascade to the driver. Delete both
            # once the run is terminal (or timed out/raised).
            if job_id is not None:
                self.k8s_client.cleanup_after_run(job_id)


def _build_job_name(stmt: RenderedStatement, ctx: ExecCtx | None) -> str:
    parts = ["cryo", stmt.action.value.lower()]
    if ctx and ctx.job_id:
        parts.append(str(ctx.job_id)[:8])
    return "-".join(parts)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
