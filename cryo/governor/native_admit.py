"""Governor-side native K8s Job creation on admit (pack + size + create).

Sizing order: estimate pod from stats → pack → derive chunk_size_gb and
max_bytes_per_group from pod mem → rebuild plan so rewrite utilizes the Job.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryo.db.repositories.native_dispatch import (
    create_dispatch_record,
    get_dispatch,
    mark_dispatch_running,
)
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.domain.models import CapacitySnapshot, MaintenancePlan, Operation
from cryo.engines.native.batch import build_batch_payload
from cryo.engines.native.job_dispatch import (
    create_native_job,
    k8s_dispatch_available,
    resolve_k8s_namespace,
)
from cryo.engines.native.packing import (
    DispatchSpec,
    PackableJob,
    build_packable_job,
    pack_jobs,
)
from cryo.engines.native.payload import (
    build_execute_request_for_operations,
)
from cryo.engines.native.routing import resolve_native_routing
from cryo.engines.native.resources import (
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    compaction_memory_budget_bytes,
    derive_chunk_params,
    group_concurrency_for_table,
    max_bytes_per_group_for_pod_mem,
)
from cryo.planner.planner import build_plan
from cryo.worker.worker import (
    DestinationConfig,
    JobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    plan_to_dict,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NativeAdmitResult:
    admitted: int = 0
    deferred: int = 0
    fallback_to_worker: int = 0
    dispatches: tuple[str, ...] = ()


@dataclass
class _PreparedJob:
    job: MaintenanceJobRecord
    table_data: TableMaintenanceData
    stats: dict[str, Any]
    packable: PackableJob
    mode: Mode


def destination_uses_k8s_dispatch(destination: DestinationConfig) -> bool:
    engine = destination.engine
    if isinstance(engine, EngineType):
        is_native = engine is EngineType.NATIVE
    else:
        is_native = str(engine).lower() in {"native", "enginetype.native"}
    if not is_native:
        return False
    cfg = destination.config or {}
    flag = cfg.get("k8s_dispatch")
    if flag is None:
        return False
    return flag in (True, "true", "1", 1)


@dataclass(frozen=True)
class NativeRequestResult:
    request: dict[str, Any]
    # Operations native (iceberg-go) can't run, paired with the reason -- e.g.
    # optimize_manifests, or optimize itself on a MOR table. Dispatched separately
    # to sql_fallback_destination_id when configured (see _dispatch_fallback_operations).
    fallback_operations: tuple[tuple[Operation, str], ...] = ()


def build_native_request(
    *,
    plan: MaintenancePlan,
    stats: Mapping[str, Any] | None,
    source_config: Mapping[str, Any],
    destination_config: Mapping[str, Any],
    job_id: str,
    table_id: str,
    mem_mib: int,
    derived_chunk: float,
    budget: int,
) -> NativeRequestResult:
    """Build one table's execute request, filtered to native-supported operations.

    Not every planned operation is executable by the native (iceberg-go) engine
    (e.g. optimize_manifests, compute_statistics) -- filter to what it actually
    supports rather than dispatching ops that fail on arrival. Matches the Worker
    legacy path (engine.py::_supported_operations). Found live: a real deep-preset
    run against a MOR table dispatched optimize_manifests straight to the Go engine
    and failed the whole batch, because this filter wasn't wired into the
    Governor's create-on-admit path the way it is into the Worker's legacy path.

    Operations routing sends to a SQL fallback are returned separately rather than
    dropped, so the caller can dispatch them to destination_config's
    sql_fallback_destination_id (e.g. a Spark-on-K8s engine) instead of silently
    skipping real requested work. Native-unsupported ops are only classified as
    fallback -- not executed -- when this function's caller cannot resolve or
    dispatch to that fallback destination; see admit_native_k8s_jobs.
    """
    routing = resolve_native_routing(plan, stats, destination_config)
    native_ops = routing.native_operations
    if routing.fallback_operations or routing.skipped_operations:
        dropped = {op.type for op, _ in (*routing.fallback_operations, *routing.skipped_operations)}
        logger.info(
            "Native-unsupported operations for table %s: %s",
            table_id,
            ", ".join(sorted(str(t) for t in dropped)),
        )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=native_ops,
        source_config=source_config,
        destination_config=destination_config,
    )
    request["job_id"] = job_id
    request["table_id"] = table_id
    for op in request.get("operations") or []:
        if isinstance(op, dict) and op.get("type") == "rewrite_data_files":
            params = dict(op.get("params") or {})
            params["compaction_memory_budget_bytes"] = budget
            params["chunk_size_gb"] = derived_chunk
            # Prefer plan-derived max_bytes; fall back to dispatch sizing.
            if "max_bytes_per_group" not in params:
                params["max_bytes_per_group"] = max_bytes_per_group_for_pod_mem(
                    mem_mib, params, chunk_size_gb=derived_chunk
                )
            # normalize_native_operation_params defaults group_concurrency to a flat 2,
            # validated only on a 24-48-file fixture (docs/native-go-compaction-throughput.md).
            # Scale it toward the table's real partition count here, where stats are
            # actually available -- but only when the preset hasn't explicitly picked a
            # different value (2 is indistinguishable from "left at the default", so an
            # explicit 2 gets upgraded too; anything else is respected as intentional).
            if params.get("group_concurrency") in (None, 2):
                params["group_concurrency"] = group_concurrency_for_table(stats)
            op["params"] = params
    return NativeRequestResult(request=request, fallback_operations=routing.fallback_operations)


def _fallback_engine_config(
    fallback_config: Mapping[str, Any], source_config: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge the source's own catalog connection info and S3 credentials into a copy of the
    fallback destination's config.

    The native (Go) engine gets S3 credentials from source_config.s3.access-key-id/secret-access-key
    (cryo.engines.native.payload.source_catalog_config) rather than the node's ambient IAM
    role -- confirmed live (2026-07-21): a Spark driver pod using only the node's instance
    role got AccessDeniedException on s3:GetObject against the same bucket the native
    engine reads fine. The Spark engine needs the same explicit credentials.

    A single SQL-fallback destination is shared across every table routed to it regardless
    of which catalog that table actually lives in, so its spark_k8s.catalog_uri/catalog_warehouse
    and top-level `catalog` are just a static default -- correct only for tables whose source
    happens to match it. Confirmed live, 2026-07-24: lms.silver_adminactions (source catalog_name
    "hive", metastore thrift://trino-metastore-v2.example.com:9083, warehouse
    s3://example-lake-dev-bronze-layer/) fell back to this same destination as
    lake.analytics.load_test_basefact, whose static config points at a completely different
    metastore/warehouse (thrift://trino-prod-metastore.example.com:9083,
    s3://example-lake-prod-gold-layer/) under catalog name "cdp". Spark registered its Iceberg
    catalog plugin under the wrong name entirely, so `CALL hive.system.rewrite_data_files(...)`
    silently resolved against the builtin spark_catalog instead and failed with
    MISSING_CATALOG_ABILITY.PROCEDURES. The source's own uri/warehouse/catalog_name -- always
    correct for its own tables -- must win whenever present.

    This merge is in-memory only for this dispatch call, never persisted back to the
    destination row.
    """
    merged = dict(fallback_config)
    spark_k8s = dict(merged.get("spark_k8s") or {})

    access_key = source_config.get("s3.access-key-id")
    secret_key = source_config.get("s3.secret-access-key")
    if isinstance(access_key, str) and access_key and isinstance(secret_key, str) and secret_key:
        spark_k8s.setdefault("s3_access_key", access_key)
        spark_k8s.setdefault("s3_secret_key", secret_key)

    source_uri = source_config.get("uri")
    source_warehouse = source_config.get("warehouse")
    source_catalog_name = source_config.get("catalog_name")
    if isinstance(source_uri, str) and source_uri:
        spark_k8s["catalog_uri"] = source_uri
    if isinstance(source_warehouse, str) and source_warehouse:
        spark_k8s["catalog_warehouse"] = source_warehouse
    if isinstance(source_catalog_name, str) and source_catalog_name:
        merged["catalog"] = source_catalog_name

    if spark_k8s:
        merged["spark_k8s"] = spark_k8s
    return merged


def _dispatch_fallback_operations(
    *,
    store: JobStore,
    fallback_operations: tuple[tuple[Operation, str], ...],
    plan: MaintenancePlan,
    destination_config: Mapping[str, Any],
    source_config: Mapping[str, Any],
    table_ref: str,
) -> None:
    """Best-effort synchronous dispatch of native-unsupported ops to a SQL fallback.

    Runs inline in the admission wave (unlike native Jobs, which are fire-and-forget
    and watched separately) -- acceptable because fallback-eligible tables are the
    rare/occasional exception, not the routine hot path. Exceptions are caught and
    logged, never raised: a fallback failure must not affect the native Job this
    table's native-supported operations are about to run in.
    """
    if not fallback_operations:
        return
    fallback_id = destination_config.get("sql_fallback_destination_id") or destination_config.get(
        "fallback_destination_id"
    )
    if not isinstance(fallback_id, str) or not fallback_id.strip():
        logger.info(
            "%s: %d operation(s) need a SQL fallback but no sql_fallback_destination_id is "
            "configured; skipped: %s",
            table_ref,
            len(fallback_operations),
            ", ".join(sorted({op.type.value for op, _ in fallback_operations})),
        )
        return

    try:
        from cryo.engines.factory import create_engine

        try:
            fallback_destination = store.get_destination(fallback_id.strip())
        except KeyError:
            logger.error("%s: sql_fallback_destination_id %r not found", table_ref, fallback_id)
            return

        fallback_engine = create_engine(
            fallback_destination.engine,
            _fallback_engine_config(fallback_destination.config, source_config),
        )
        for operation, reason in fallback_operations:
            fallback_plan = MaintenancePlan(
                table_ref=plan.table_ref, mode=plan.mode, operations=(operation,)
            )
            logger.info(
                "%s: routing %s to %s (%s)",
                table_ref,
                operation.type.value,
                fallback_engine.type,
                reason,
            )
            for statement in fallback_engine.render(fallback_plan):
                result = fallback_engine.execute(statement)
                if result.ok:
                    logger.info(
                        "%s: fallback %s succeeded (engine_ref=%s, %sms)",
                        table_ref,
                        operation.type.value,
                        result.engine_ref,
                        result.duration_ms,
                    )
                else:
                    logger.error(
                        "%s: fallback %s failed: %s",
                        table_ref,
                        operation.type.value,
                        result.error,
                    )
    except Exception:
        logger.exception("%s: SQL fallback dispatch raised unexpectedly", table_ref)


def cluster_headroom(
    live_capacity: Mapping[str, CapacitySnapshot | int],
    *,
    config: Mapping[str, Any],
) -> tuple[int, int, int]:
    """Return (slots_available, cpu_headroom_milli, mem_headroom_mib)."""
    max_cpu = int(config.get("max_cluster_cpu_milli") or config.get("slot_cap", 5) * 4000)
    max_mem = int(config.get("max_cluster_mem_mib") or config.get("slot_cap", 5) * MAX_POD_MEM_MIB)
    used_slots = 0
    used_cpu = 0
    used_mem = 0
    capacity_slots = int(config.get("slot_cap") or 5)
    for snapshot in live_capacity.values():
        if isinstance(snapshot, CapacitySnapshot):
            used_slots = max(used_slots, snapshot.used)
            capacity_slots = max(capacity_slots, snapshot.capacity)
            used_cpu += snapshot.cpu_used_milli or 0
            used_mem += snapshot.mem_used_mib or 0
        else:
            used_slots = max(used_slots, int(snapshot))
    return (
        max(0, capacity_slots - used_slots),
        max(0, max_cpu - used_cpu),
        max(0, max_mem - used_mem),
    )


def admit_native_k8s_jobs(
    *,
    jobs: Sequence[MaintenanceJobRecord],
    destination: DestinationConfig,
    store: JobStore,
    session_factory: Callable[[], Any],
    live_capacity: Mapping[str, CapacitySnapshot | int],
    stats_cache: Mapping[uuid.UUID, Any] | None = None,
    mark_status: Callable[..., None],
    cancel_pending: Callable[[str, str], None],
    defer_job: Callable[[str, str], None],
    release_lock: Callable[[str, str], None],
) -> NativeAdmitResult:
    """Pack admissible native jobs and create K8s Jobs on admit.

    When K8s is unavailable, returns fallback_to_worker > 0 so the caller can
    ``queue.admit`` those jobs onto the Worker path instead.
    """
    if not jobs:
        return NativeAdmitResult()

    ceilings = ResourceCeilings.from_config(destination.config)
    if not k8s_dispatch_available():
        logger.info(
            "Native k8s_dispatch enabled but not in-cluster; falling back to Worker for %d jobs",
            len(jobs),
        )
        return NativeAdmitResult(fallback_to_worker=len(jobs))

    _ = stats_cache
    prepared: list[_PreparedJob] = []
    by_job_id: dict[str, _PreparedJob] = {}
    # Every job reaching here already holds its table lock (acquired by the governor's
    # per-job loop before buffering for native dispatch). `resolved` tracks which of
    # those jobs have reached an explicit admitted/deferred-with-release outcome, so
    # the outer except below can safety-net release anything left dangling if a call
    # in this function raises somewhere we didn't anticipate (e.g. a DB write or K8s
    # API call that isn't individually guarded) -- otherwise that job's lock orphans
    # silently and every future admission attempt for its table self-blocks on
    # "table_locked" until the 6h TTL expires. Seen live, 2026-07-22.
    resolved: set[str] = set()
    admitted = 0
    deferred = 0
    dispatch_ids: list[str] = []

    for job in jobs:
        try:
            table_data = store.get_table_data(job.table_id)
            stats = dict(table_data.stats)
            mode = job.mode if isinstance(job.mode, Mode) else Mode(str(job.mode))
            # Estimate from stats only (no preset chunk) — chunk comes after pack.
            ops_hint = job.operations
            sizing_params = {k: v for k, v in dict(job.params).items() if k != "chunk_size_gb"}
            # Keep the CPU estimate in sync with the group_concurrency actually dispatched
            # (see the matching override in build_native_request below) -- otherwise the pod
            # gets sized/CPU-limited for concurrency=2 while the engine is told to run more
            # groups in parallel, throttling away most of the intended speedup.
            if sizing_params.get("group_concurrency") in (None, 2):
                sizing_params["group_concurrency"] = group_concurrency_for_table(
                    stats, max_cpu_milli=ceilings.max_cpu_milli
                )
            packable = build_packable_job(
                job_id=job.id,
                table_id=job.table_id,
                run_id=job.run_id,
                stats=stats,
                params=sizing_params,
                operations=ops_hint,
                ceilings=ceilings,
            )
            item = _PreparedJob(
                job=job,
                table_data=table_data,
                stats=stats,
                packable=packable,
                mode=mode,
            )
            prepared.append(item)
            by_job_id[job.id] = item
        except Exception as exc:
            logger.exception("Failed to prepare native admit for job %s: %s", job.id, exc)
            mark_status(job.id, JobStatus.FAILED, error=f"native_admit_prepare_failed: {exc}")
            cancel_pending(job.id, destination.id)
            release_lock(job.table_id, job.id)
            resolved.add(job.id)

    if not prepared:
        return NativeAdmitResult()

    try:
        specs = pack_jobs([p.packable for p in prepared], ceilings=ceilings)
        slots_left, cpu_left, mem_left = cluster_headroom(
            live_capacity, config=destination.config
        )

        for spec in specs:
            if slots_left <= 0 or spec.cpu_milli > cpu_left or spec.mem_mib > mem_left:
                deferred += _defer_spec(
                    spec,
                    defer_job=defer_job,
                    release_lock=release_lock,
                    reason="native_cluster_capacity",
                    resolved=resolved,
                )
                continue

            # Shared pod: all members use chunk / file-group caps from the dispatch mem.
            peak_stats = _peak_stats_for_spec(spec, by_job_id)
            derived_chunk = chunk_size_gb_for_pod_mem(spec.mem_mib, peak_stats)
            budget = compaction_memory_budget_bytes(spec.mem_mib)

            requests: list[dict[str, Any]] = []
            plan_ok = True
            for member in spec.members:
                prepared_item = by_job_id.get(member.job_id)
                if prepared_item is None:
                    plan_ok = False
                    break
                try:
                    plan_params = derive_chunk_params(
                        spec.mem_mib,
                        prepared_item.stats,
                        {
                            k: v
                            for k, v in dict(prepared_item.job.params).items()
                            if k != "chunk_size_gb"
                        },
                    )
                    plan = build_plan(
                        prepared_item.stats,
                        prepared_item.table_data.partitions,
                        prepared_item.mode,
                        plan_params,
                    )
                    store.save_plan(prepared_item.job.id, plan_to_dict(plan))
                    built = build_native_request(
                        plan=plan,
                        stats=prepared_item.stats,
                        source_config=prepared_item.table_data.source_config,
                        destination_config=destination.config,
                        job_id=prepared_item.job.id,
                        table_id=prepared_item.job.table_id,
                        mem_mib=spec.mem_mib,
                        derived_chunk=derived_chunk,
                        budget=budget,
                    )
                    requests.append(built.request)
                    _dispatch_fallback_operations(
                        store=store,
                        fallback_operations=built.fallback_operations,
                        plan=plan,
                        destination_config=destination.config,
                        source_config=prepared_item.table_data.source_config,
                        table_ref=plan.table_ref,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to rebuild plan for job %s with derived chunk: %s",
                        member.job_id,
                        exc,
                    )
                    plan_ok = False
                    break

            if not plan_ok or not requests:
                deferred += _defer_spec(
                    spec,
                    defer_job=defer_job,
                    release_lock=release_lock,
                    reason="native_admit_plan_rebuild_failed",
                    resolved=resolved,
                )
                continue

            batch_payload = build_batch_payload(requests, run_id=spec.run_id)
            namespace = resolve_k8s_namespace(destination.config)

            with session_factory() as session:
                row = create_dispatch_record(
                    session,
                    spec=spec,
                    destination_id=destination.id,
                    k8s_namespace=namespace,
                    status="pending",
                )
                dispatch_id = str(row.id)
                job_name = f"cryo-native-{row.id.hex[:12]}"
                row.k8s_job_name = job_name
                session.commit()

            batch_payload["dispatch_id"] = dispatch_id
            dest_cfg = dict(destination.config)
            dest_cfg["destination_id"] = destination.id
            dest_cfg["k8s_namespace"] = namespace
            dest_cfg.setdefault("k8s_image", dest_cfg.get("image") or "cryo:latest")
            dest_cfg.setdefault("max_memory_mib", ceilings.max_mem_mib)
            dest_cfg.setdefault("max_cpu_milli", ceilings.max_cpu_milli)

            created = create_native_job(
                batch_payload,
                config=dest_cfg,
                cpu_milli=spec.cpu_milli,
                mem_mib=spec.mem_mib,
                job_name=job_name,
                dispatch_id=dispatch_id,
                run_id=spec.run_id,
                table_refs=tuple(
                    str(req.get("table_ref", jid))
                    for jid, req in zip(spec.job_ids, requests, strict=False)
                ),
            )
            if not created.ok or not created.job_name:
                deferred += _defer_spec(
                    spec,
                    defer_job=defer_job,
                    release_lock=release_lock,
                    reason=created.error or "k8s_job_create_failed",
                    resolved=resolved,
                )
                with session_factory() as session:
                    from cryo.db.repositories.native_dispatch import mark_dispatch_finished

                    mark_dispatch_finished(
                        session,
                        uuid.UUID(dispatch_id),
                        status="failed",
                        error=created.error,
                    )
                    session.commit()
                continue

            cancelled_during_create = False
            with session_factory() as session:
                dispatch = get_dispatch(session, uuid.UUID(dispatch_id))
                if dispatch is not None and dispatch.status in {
                    "cancel_requested",
                    "cancelling",
                    "cancelled",
                }:
                    cancelled_during_create = True
                else:
                    mark_dispatch_running(
                        session, uuid.UUID(dispatch_id), k8s_job_name=created.job_name
                    )
                session.commit()

            if cancelled_during_create:
                logger.info(
                    "Native dispatch %s was cancelled while Job %s was being created",
                    dispatch_id,
                    created.job_name,
                )
                # The cancellation flow owns releasing these locks -- mark resolved so
                # the safety net below doesn't double-release or re-defer them.
                resolved.update(spec.job_ids)
                continue

            for job_id in spec.job_ids:
                cancel_pending(job_id, destination.id)
                mark_status(
                    job_id,
                    JobStatus.RUNNING,
                    engine_ref=created.job_name,
                    replica=created.job_name,
                )
                admitted += 1
                resolved.add(job_id)

            slots_left -= 1
            cpu_left -= spec.cpu_milli
            mem_left -= spec.mem_mib
            dispatch_ids.append(dispatch_id)
            logger.info(
                "Admitted native dispatch %s job=%s tables=%d cpu=%dm mem=%dMi chunk_gb=%.1f",
                dispatch_id,
                created.job_name,
                len(spec.job_ids),
                created.cpu_milli,
                created.mem_mib,
                derived_chunk,
            )
    except Exception as exc:
        # Anything above that isn't individually guarded (pack_jobs, cluster_headroom,
        # the create_dispatch_record/mark_dispatch_running DB writes) can throw and abort
        # this loop mid-flight. Every job here already holds its table lock; leaving it
        # orphaned means every future admission attempt for that table self-blocks on
        # "table_locked" until the 6h TTL expires -- with the actual cause never
        # surfaced. Defer and release everything not yet resolved instead of letting the
        # exception propagate and silently orphan locks.
        unresolved = [job for job in jobs if job.id not in resolved]
        logger.exception(
            "admit_native_k8s_jobs crashed mid-dispatch (%d job(s) left unresolved): %s",
            len(unresolved),
            exc,
        )
        for job in unresolved:
            try:
                defer_job(job.id, "native_admit_unexpected_error")
            except Exception:
                logger.exception("Failed to defer job %s after native admit crash", job.id)
            try:
                release_lock(job.table_id, job.id)
            except Exception:
                logger.exception(
                    "Failed to release lock for job %s after native admit crash", job.id
                )
            deferred += 1

    return NativeAdmitResult(
        admitted=admitted,
        deferred=deferred,
        dispatches=tuple(dispatch_ids),
    )


def _peak_stats_for_spec(
    spec: DispatchSpec,
    by_job_id: Mapping[str, _PreparedJob],
) -> dict[str, Any]:
    """Aggregate stats used when deriving chunk for a shared pod (worst pressure)."""
    total_files = 0
    max_part = 0
    delete_pct = 0.0
    delete_files = 0
    total_size = 0.0
    for member in spec.members:
        item = by_job_id.get(member.job_id)
        if item is None:
            continue
        s = item.stats
        total_files = max(total_files, int(s.get("total_files") or 0))
        max_part = max(max_part, int(s.get("max_files_per_partition") or 0))
        delete_pct = max(delete_pct, float(s.get("delete_file_pct") or 0.0))
        delete_files = max(delete_files, int(s.get("total_delete_files") or 0))
        total_size = max(total_size, float(s.get("total_size_gb") or 0.0))
    return {
        "total_files": total_files,
        "max_files_per_partition": max_part,
        "delete_file_pct": delete_pct,
        "total_delete_files": delete_files,
        "total_size_gb": total_size,
    }


def _defer_spec(
    spec: DispatchSpec,
    *,
    defer_job: Callable[[str, str], None],
    release_lock: Callable[[str, str], None],
    reason: str,
    mark_status: Callable[..., None] | None = None,
    resolved: set[str] | None = None,
) -> int:
    _ = mark_status
    count = 0
    for member in spec.members:
        defer_job(member.job_id, reason)
        release_lock(member.table_id, member.job_id)
        if resolved is not None:
            resolved.add(member.job_id)
        count += 1
    return count
