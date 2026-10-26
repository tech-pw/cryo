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
    METASTORE_UNREACHABLE_MARKER,
    METASTORE_UNREACHABLE_SIGNATURES,
    ROOT_CAUSE_PREFIX,
    TABLE_NOT_FOUND_MARKER,
    ExecCtx,
    ExecResult,
    RenderedStatement,
    capacity_refusal_error,
    is_capacity_refusal,
    is_table_not_found_error,
    truncate_error_message,
)
from cryo.engines.spark.k8s import (
    EXECUTOR_OOM_PREFIX,
    SUCCESS_STATES,
    SparkK8sClient,
    SparkK8sConfig,
)
from cryo.engines.spark.procedure_metrics import rewrite_file_counts
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
# One larger driver after a real OOMKilled. A second kill stops. Exit 137 is not a
# heap sample, and a third pod would just burn another slot on the same anti-join.
_SPARK_OOM_RETRY_ATTEMPTS = 1
# One retry on a doubled executor heap after a kubelet-recorded executor OOMKilled.
_SPARK_EXECUTOR_OOM_RETRY_ATTEMPTS = 1
_SPARK_RETRYABLE_SIGNATURES = (
    "BaseMetastoreTableOperations",
    "CommitFailedException",
    "CommitStateUnknownException",
    "org.apache.iceberg.exceptions.ValidationException",
)
# The driver could not reach the Hive Metastore: congestion or a restart, not the table. 66 of
# 218 failed Spark jobs in one week were this, in bursts of 6-8 an hour (#257). They take the
# catalog retry with a longer backoff, and a job that still fails is marked with
# METASTORE_UNREACHABLE_MARKER so reports can count it apart from table failures.
_SPARK_TRANSIENT_METASTORE_SIGNATURES = METASTORE_UNREACHABLE_SIGNATURES
_SPARK_METASTORE_RETRY_BACKOFF_SECONDS = 30
_SPARK_NON_RETRYABLE_SIGNATURES = (
    "NoSuchTableException",
    "TABLE_OR_VIEW_NOT_FOUND",
    "TableNotFoundException",
    TABLE_NOT_FOUND_MARKER,
)


def is_spark_driver_oom(message: str | None) -> bool:
    """True only for the kernel kill. A JVM OutOfMemoryError in a SQL stack is not this.

    The driver log of an OOMKilled pod is INFO. ``_container_termination_reason`` puts
    ``OOMKilled exit 137`` at the front. Matching the bare words anywhere in a 2000-line
    tail would step up a SQL error that happened to mention the watcher line.
    """
    if not message:
        return False
    head = message.split("\n", 1)[0]
    return head.startswith("OOMKilled exit ")


def is_spark_transient_metastore_error(message: str | None) -> bool:
    """True when the driver failed to reach the Hive Metastore (connect/read timeout)."""
    if not message:
        return False
    if any(sig in message for sig in _SPARK_NON_RETRYABLE_SIGNATURES):
        return False
    # A killed driver or executor leaves connection errors from its peers in the log; the
    # kill is the cause, and the OOM retries own it.
    if is_spark_driver_oom(message) or is_spark_executor_oom(message):
        return False
    return any(sig in message for sig in _SPARK_TRANSIENT_METASTORE_SIGNATURES)


def is_spark_executor_oom(message: str | None) -> bool:
    """True when get_job_run front-loaded a kubelet-recorded executor OOMKilled (issue #166)."""
    if not message:
        return False
    return message.split("\n", 1)[0].startswith(EXECUTOR_OOM_PREFIX)


def is_spark_retryable_catalog_error(message: str | None) -> bool:
    """True when a Spark driver failure is worth one more attempt against the catalog."""
    if not message:
        return False
    if any(sig in message for sig in _SPARK_NON_RETRYABLE_SIGNATURES):
        return False
    return any(sig in message for sig in _SPARK_RETRYABLE_SIGNATURES) or (
        is_spark_transient_metastore_error(message)
    )


def _catalog_retry_backoff_seconds(message: str, attempt: int) -> int:
    """Linear backoff; longer for a metastore that is refusing or timing out connections."""
    base = (
        _SPARK_METASTORE_RETRY_BACKOFF_SECONDS
        if is_spark_transient_metastore_error(message)
        else _SPARK_CATALOG_RETRY_BACKOFF_SECONDS
    )
    return base * (attempt + 1)


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
        overrides: dict[str, str] = {}

        access_key = source_config.get("s3.access-key-id")
        secret_key = source_config.get("s3.secret-access-key")
        if (
            isinstance(access_key, str)
            and access_key
            and isinstance(secret_key, str)
            and secret_key
            and not (self._k8s_config.s3_access_key and self._k8s_config.s3_secret_key)
        ):
            overrides["s3_access_key"] = access_key
            overrides["s3_secret_key"] = secret_key

        # A REST catalog with no URI falls back to spark_catalog and the driver's HTTP
        # client dies with "Invalid uri for http client: null" (2026-10-21, issue #143).
        # The source's own URI/warehouse always win: the destination's are a static default
        # shared across every catalog routed to it, and they go stale when a metastore moves
        # (2026-10-22: a leftover destination catalog_uri kept every Spark job on a retired
        # HMS after the source was repointed). Matches native_admit._fallback_engine_config.
        source_uri = source_config.get("uri") or source_config.get("catalog_uri")
        if isinstance(source_uri, str) and source_uri:
            overrides["catalog_uri"] = source_uri
        source_warehouse = source_config.get("warehouse")
        if isinstance(source_warehouse, str) and source_warehouse:
            overrides["catalog_warehouse"] = source_warehouse

        if not overrides:
            return self
        new_engine = SparkK8sEngine(config=self.config, settings=self.settings)
        new_engine._k8s_config = replace(
            new_engine._k8s_config,
            catalog_uri=overrides.get("catalog_uri", new_engine._k8s_config.catalog_uri),
            catalog_warehouse=overrides.get(
                "catalog_warehouse", new_engine._k8s_config.catalog_warehouse
            ),
            s3_access_key=overrides.get("s3_access_key", new_engine._k8s_config.s3_access_key),
            s3_secret_key=overrides.get("s3_secret_key", new_engine._k8s_config.s3_secret_key),
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
            table_size_gb, total_files = _table_size_and_files(self._table_stats)
            job_id = self.k8s_client.submit_spark_sql(
                stmt.sql,
                job_name=job_name,
                catalog=str(catalog),
                destination_id=self._destination_id,
                operation=stmt.action.value,
                total_size_gb=table_size_gb,
                total_files=total_files,
                oom_steps=0,
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
            if is_capacity_refusal(str(exc)):
                # Kubernetes refused the Job itself, so nothing ran.
                return ExecResult(
                    ok=False,
                    detail=stmt.sql,
                    error=truncate_error_message(capacity_refusal_error(str(exc))),
                    duration_ms=_elapsed_ms(start),
                )
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
            oom_steps = 0
            executor_oom_steps = 0
            catalog_attempts = 0
            max_attempts = (
                _SPARK_CATALOG_RETRY_ATTEMPTS
                + _SPARK_OOM_RETRY_ATTEMPTS
                + _SPARK_EXECUTOR_OOM_RETRY_ATTEMPTS
            )
            for attempt in range(max_attempts):
                attempt_name = job_name if attempt == 0 else f"{job_name}-r{attempt}"
                logger.info(
                    "spark-k8s execute submit action=%s sql_bytes=%d rss=%s MiB "
                    "attempt=%d oom_steps=%d executor_oom_steps=%d",
                    stmt.action.value,
                    len(stmt.sql),
                    process_rss_mib(),
                    attempt + 1,
                    oom_steps,
                    executor_oom_steps,
                )
                table_size_gb, total_files = _table_size_and_files(self._table_stats)
                job_id = self.k8s_client.submit_spark_sql(
                    stmt.sql,
                    job_name=attempt_name,
                    catalog=str(catalog),
                    destination_id=self._destination_id,
                    operation=stmt.action.value,
                    total_size_gb=table_size_gb,
                    total_files=total_files,
                    oom_steps=oom_steps,
                    executor_oom_steps=executor_oom_steps,
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
                    input_files, output_files = rewrite_file_counts(
                        stmt.action.value, result.metrics
                    )
                    return ExecResult(
                        ok=True,
                        engine_ref=job_id,
                        detail=stmt.sql,
                        duration_ms=duration_ms,
                        input_files_count=input_files,
                        output_files_count=output_files,
                        actual_bytes_freed=result.bytes_removed,
                    )
                state_details = result.state_details or "no details"
                if is_capacity_refusal(state_details):
                    # The quota refused the driver pod or its Service: no SQL ran. Not a
                    # table failure and not worth a same-slot retry; the worker defers it.
                    return ExecResult(
                        ok=False,
                        engine_ref=job_id,
                        detail=stmt.sql,
                        error=truncate_error_message(capacity_refusal_error(state_details)),
                        duration_ms=duration_ms,
                    )
                # A driver's raw log tail is a full Java/Scala stack trace easily over 4000 chars,
                # with "Caused by: NoSuchTableException" typically buried well past the head+tail
                # window truncate_error_message keeps -- confirmed live, 2026-07-31: the signature
                # was in the untruncated log but never reached the stored error, so nothing could
                # detect "this table was dropped" downstream. Front-load the signature before
                # truncating so head-first truncation always keeps it, regardless of where in the
                # original text it actually occurred.
                if is_table_not_found_error(state_details):
                    state_details = f"{TABLE_NOT_FOUND_MARKER} {state_details}"
                # An OOM is not a catalog race. Counting it here used to burn a catalog
                # retry and, on a later catalog failure, leave room for a fifth pod.
                catalog_retry = (
                    is_spark_retryable_catalog_error(state_details)
                    and catalog_attempts < _SPARK_CATALOG_RETRY_ATTEMPTS - 1
                )
                if is_spark_retryable_catalog_error(state_details):
                    catalog_attempts += 1
                # File-count sizing already ran on the first submit. One more heap step
                # only when the kernel killed the driver and the ceiling has not been hit.
                # A SQL error, a timeout, or a second kill stops.
                oom_retry = (
                    oom_steps < _SPARK_OOM_RETRY_ATTEMPTS
                    and is_spark_driver_oom(state_details)
                    and _oom_step_would_grow(stmt.action.value, total_files, oom_steps)
                )
                # An OOM-killed executor is the same kernel kill on the other pod. One retry
                # on a larger executor heap for the rewrite operations that size executors.
                executor_oom = is_spark_executor_oom(state_details)
                executor_oom_retry = (
                    executor_oom
                    and executor_oom_steps < _SPARK_EXECUTOR_OOM_RETRY_ATTEMPTS
                    and _executor_step_would_grow(
                        self._k8s_config, stmt.action.value, table_size_gb, executor_oom_steps
                    )
                )
                if catalog_retry or oom_retry or executor_oom_retry:
                    if oom_retry:
                        oom_steps += 1
                    if executor_oom_retry:
                        executor_oom_steps += 1
                    self.k8s_client.cleanup_after_run(job_id)
                    job_id = None
                    if catalog_retry:
                        time.sleep(_catalog_retry_backoff_seconds(state_details, attempt))
                    continue
                if is_spark_driver_oom(state_details) and _driver_heap_at_ceiling(
                    # oom_steps counts retries launched, so the pod that just died
                    # ran the previous step. A 3072MiB kill must not read as 4096.
                    stmt.action.value,
                    total_files,
                    oom_steps - 1,
                ):
                    state_details = (
                        f"{state_details.split(chr(10), 1)[0]} at driver heap ceiling; "
                        "not splitting the CALL — Iceberg still anti-joins every retained "
                        f"file, and scan stats have no snapshot ids (total_files={total_files})\n"
                        f"{state_details}"
                    )
                if is_spark_transient_metastore_error(state_details):
                    state_details = (
                        f"{METASTORE_UNREACHABLE_MARKER} Hive Metastore unreachable after "
                        f"{catalog_attempts} attempt(s)\n{state_details}"
                    )
                error = f"Spark-on-K8s Job {result.state.value}: {state_details}"
                if executor_oom:
                    # Name the kill as the root cause, or truncation front-loads Spark's
                    # follow-on NullPointerException from the lost executor instead.
                    lead = state_details.split("\n", 1)[0]
                    retried = " after one retry on a larger executor heap" * executor_oom_steps
                    error = f"{ROOT_CAUSE_PREFIX}{lead}{retried}\n{error}"
                return ExecResult(
                    ok=False,
                    engine_ref=job_id,
                    detail=stmt.sql,
                    error=truncate_error_message(error),
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
            if job_id is None and is_capacity_refusal(str(exc)):
                # Kubernetes refused the dispatch Job itself, so nothing ran.
                return ExecResult(
                    ok=False,
                    detail=stmt.sql,
                    error=truncate_error_message(capacity_refusal_error(str(exc))),
                    duration_ms=_elapsed_ms(start),
                )
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


def _table_size_and_files(
    stats: Mapping[str, Any] | None,
) -> tuple[float | None, int | None]:
    """Size and the file count expire actually anti-joins.

    ``total_files`` is data files only (scanner, 2026-07-28). A MOR expire also holds
    delete files, so the driver is sized from both. Missing either side is not zero:
    an unscanned count must stay unknown and take the small pod, not a fake 0.
    """
    raw_size = (stats or {}).get("total_size_gb")
    size = float(raw_size) if raw_size is not None else None
    files = _file_count(stats)
    return size, files


def _file_count(stats: Mapping[str, Any] | None) -> int | None:
    if not stats:
        return None
    data = stats.get("total_files")
    deletes = stats.get("total_delete_files")
    if data is None and deletes is None:
        return None
    return int(data or 0) + int(deletes or 0)


def _oom_step_would_grow(operation: str, total_files: int | None, oom_steps: int) -> bool:
    from cryo.engines.spark.sizing import lightweight_driver_heap_mib

    if operation not in {"expire_snapshots", "remove_orphan_files"}:
        return False
    current = lightweight_driver_heap_mib(total_files=total_files, oom_steps=oom_steps)
    nxt = lightweight_driver_heap_mib(total_files=total_files, oom_steps=oom_steps + 1)
    return nxt > current


def _executor_step_would_grow(
    config: SparkK8sConfig,
    operation: str,
    total_size_gb: float | None,
    executor_oom_steps: int,
) -> bool:
    """True when one more executor OOM step yields a bigger executor than the pod that died."""
    from cryo.engines.spark.sizing import ExecutorProfile, cap_dispatch_profile

    base = ExecutorProfile(
        executor_instances=config.executor_instances,
        executor_cpu_milli=config.executor_cpu_milli,
        executor_mem_mib=config.executor_mem_mib,
        driver_cpu_milli=config.driver_cpu_milli,
        driver_mem_mib=config.driver_mem_mib,
    )

    def _mem(steps: int) -> int:
        return cap_dispatch_profile(
            base,
            operation=operation,
            total_size_gb=total_size_gb,
            executor_oom_steps=steps,
        ).executor_mem_mib

    return _mem(executor_oom_steps + 1) > _mem(executor_oom_steps)


def _driver_heap_at_ceiling(operation: str, total_files: int | None, oom_steps: int) -> bool:
    """True only when the pod that just died was already at 4096MiB.

    A second kill of a 3072MiB driver is still short of the ceiling. Saying "at
    ceiling" there would hide that the step-up itself did not run.
    """
    from cryo.engines.spark.sizing import _DRIVER_HEAP_CEILING_MIB, lightweight_driver_heap_mib

    if operation not in {"expire_snapshots", "remove_orphan_files"}:
        return False
    heap = lightweight_driver_heap_mib(total_files=total_files, oom_steps=oom_steps)
    return heap >= _DRIVER_HEAP_CEILING_MIB


def _build_job_name(stmt: RenderedStatement, ctx: ExecCtx | None) -> str:
    parts = ["cryo", stmt.action.value.lower()]
    if ctx and ctx.job_id:
        parts.append(str(ctx.job_id)[:8])
    return "-".join(parts)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
