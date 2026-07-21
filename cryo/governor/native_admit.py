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
from cryo.domain.models import CapacitySnapshot
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
from cryo.engines.native.resources import (
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    compaction_memory_budget_bytes,
    derive_chunk_params,
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

    for job in jobs:
        try:
            table_data = store.get_table_data(job.table_id)
            stats = dict(table_data.stats)
            mode = job.mode if isinstance(job.mode, Mode) else Mode(str(job.mode))
            # Estimate from stats only (no preset chunk) — chunk comes after pack.
            ops_hint = job.operations
            packable = build_packable_job(
                job_id=job.id,
                table_id=job.table_id,
                run_id=job.run_id,
                stats=stats,
                params={k: v for k, v in dict(job.params).items() if k != "chunk_size_gb"},
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

    if not prepared:
        return NativeAdmitResult()

    specs = pack_jobs([p.packable for p in prepared], ceilings=ceilings)
    slots_left, cpu_left, mem_left = cluster_headroom(
        live_capacity, config=destination.config
    )

    admitted = 0
    deferred = 0
    dispatch_ids: list[str] = []

    for spec in specs:
        if slots_left <= 0 or spec.cpu_milli > cpu_left or spec.mem_mib > mem_left:
            deferred += _defer_spec(
                spec,
                defer_job=defer_job,
                release_lock=release_lock,
                reason="native_cluster_capacity",
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
                request = build_execute_request_for_operations(
                    plan=plan,
                    operations=plan.operations,
                    source_config=prepared_item.table_data.source_config,
                    destination_config=destination.config,
                )
                request["job_id"] = prepared_item.job.id
                request["table_id"] = prepared_item.job.table_id
                for op in request.get("operations") or []:
                    if isinstance(op, dict) and op.get("type") == "rewrite_data_files":
                        params = dict(op.get("params") or {})
                        params["compaction_memory_budget_bytes"] = budget
                        params["chunk_size_gb"] = derived_chunk
                        # Prefer plan-derived max_bytes; fall back to dispatch sizing.
                        if "max_bytes_per_group" not in params:
                            params["max_bytes_per_group"] = max_bytes_per_group_for_pod_mem(
                                spec.mem_mib, params, chunk_size_gb=derived_chunk
                            )
                        op["params"] = params
                requests.append(request)
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
) -> int:
    _ = mark_status
    count = 0
    for member in spec.members:
        defer_job(member.job_id, reason)
        release_lock(member.table_id, member.job_id)
        count += 1
    return count
