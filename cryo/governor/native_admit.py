"""Governor-side native K8s Job creation on admit (pack + size + create).

Sizing order: estimate pod from stats → pack → derive chunk_size_gb and
max_bytes_per_group from pod mem → rebuild plan so rewrite utilizes the Job.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from typing import Any

from cryo.config import get_settings
from cryo.db.repositories.native_dispatch import (
    create_dispatch_record,
    get_dispatch,
    mark_dispatch_running,
)
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
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
from cryo.engines.native.resources import (
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    compaction_memory_budget_bytes,
    derive_chunk_params,
    group_concurrency_for_table,
    max_bytes_per_group_for_pod_mem,
)
from cryo.engines.native.routing import (
    native_fallback_mismatch_detail,
    native_fallback_supports,
    resolve_native_routing,
)
from cryo.planner.execution import gate_native_optimize_manifests, skip_operations_from_preset
from cryo.planner.planner import build_plan
from cryo.worker.worker import (
    DestinationConfig,
    JobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    plan_to_dict,
)

logger = logging.getLogger(__name__)

# Fallback dispatch concurrency default when the SQL fallback destination doesn't set
# its own slot_cap. _dispatch_fallback_operations used to run once per table, inline
# and fully sequential, inside the single-leader Governor process -- fine for the
# "rare/occasional exception" case it was designed for, but a real bottleneck once a
# whole cohort needs the same fallback op (e.g. optimize_manifests, unsupported by the
# native engine): observed live, 2026-08-05, ~65-70s per table with near-zero gap
# between dispatches -- 2,833 tables projected to ~90 hours serialized. Each dispatch
# creates its own engine/K8s client (no shared mutable state -- see SparkK8sClient,
# create_engine), so running them concurrently is safe; bounding by the fallback
# destination's slot_cap keeps it consistent with the capacity the operator configured.
_DEFAULT_FALLBACK_DISPATCH_CONCURRENCY = 6

# Operations that mutate a table's snapshot/manifest state: expiring or removing files
# changes which manifests are "live", and rewriting/optimizing data or manifests commits
# a new snapshot. Two of these running concurrently against the *same table* -- one via
# the native (iceberg-go) K8s job, one via the SQL fallback dispatch -- race on that same
# metadata with no coordination between them.
#
# Real incident, 2026-08-11 evening: a single admission wave dispatched native
# expire_snapshots + remove_orphan_files and, in the same cycle, fallback-routed
# optimize_manifests (CALL rewrite_manifests) to Spark for the same tables -- both
# writers touching live Iceberg metadata at once. ~3,751 native jobs failed in that
# window; 870 tables were left with a current snapshot referencing a manifest file that
# had already been deleted (Trino: "Metadata not found in metadata location";
# scanner: "could not open manifest file ... NoSuchKey"). Whichever engine committed
# last won; the loser's assumptions about what was still live were already stale.
_SNAPSHOT_MUTATING_OPERATIONS: frozenset[OperationType] = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
        OperationType.CONVERT_DELETES,
        OperationType.REWRITE_POSITION_DELETES,
    }
)


@dataclass(frozen=True)
class NativeAdmitResult:
    admitted: int = 0
    deferred: int = 0
    fallback_to_worker: int = 0
    dispatches: tuple[str, ...] = ()
    # Native-unsupported ops (typically optimize_manifests) collected during this
    # admit. The caller (Governor.run_wave) must dispatch them with a *wave-level*
    # cap -- see split_fallback_dispatch_wave -- so a large cohort cannot hold the
    # admission loop for many slot_cap batches (PDAE-8352).
    pending_fallback_dispatches: tuple[Callable[[], None], ...] = ()


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
    fallback_operations = routing.fallback_operations

    # A fallback op that mutates snapshot/manifest state must never be dispatched in the
    # same cycle as native operations that also mutate that state -- the native K8s job
    # and the fallback dispatch have no coordination between them and both write to the
    # same table's Iceberg metadata concurrently. See _SNAPSHOT_MUTATING_OPERATIONS'
    # docstring for the real incident this fixes. Deferred fallback ops are dropped from
    # this dispatch (not executed, not skipped-permanently) -- they get re-planned and
    # re-routed on the table's next scheduled run, once native_ops has had a full cycle
    # to complete and the table's snapshot state has settled.
    if native_ops and any(op.type in _SNAPSHOT_MUTATING_OPERATIONS for op in native_ops):
        deferred_fallback = [
            (op, reason)
            for op, reason in fallback_operations
            if op.type in _SNAPSHOT_MUTATING_OPERATIONS
        ]
        if deferred_fallback:
            logger.warning(
                "Table %s: deferring %s fallback op(s) to next cycle -- native ops in this "
                "same dispatch (%s) also mutate snapshot/manifest state, and running both "
                "concurrently against the same table risks a manifest-not-found corruption "
                "(see 2026-08-11 incident)",
                table_id,
                ", ".join(sorted(op.type.value for op, _ in deferred_fallback)),
                ", ".join(sorted(op.type.value for op in native_ops)),
            )
            deferred_types = {op.type for op, _ in deferred_fallback}
            fallback_operations = tuple(
                (op, reason) for op, reason in fallback_operations if op.type not in deferred_types
            )

    if fallback_operations or routing.skipped_operations:
        dropped = {op.type for op, _ in (*fallback_operations, *routing.skipped_operations)}
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
    return NativeRequestResult(request=request, fallback_operations=fallback_operations)


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
    "hive", metastore thrift://trino-metastore-v2.penpencil.co:9083, warehouse
    s3://pw-data-cdp-dev-bronze-layer/) fell back to this same destination as
    cdp.mview.load_test_basefact, whose static config points at a completely different
    metastore/warehouse (thrift://trino-prod-metastore.penpencil.co:9083,
    s3://pw-data-cdp-prod-gold-layer/) under catalog name "cdp". Spark registered its Iceberg
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


def _record_fallback_audit(
    store: JobStore,
    *,
    job_id: str,
    table_id: str,
    table_ref: str,
    operation: Operation,
    status: str,
    engine: EngineType | str,
    detail: str | None,
    duration_ms: int | None = None,
    manifests_rewritten: int | None = None,
    engine_ref: str | None = None,
) -> None:
    """Persist a fallback-dispatch outcome so it's queryable (GET /tables/{id}/runs' `steps`),
    not just a log line that vanishes when the pod cycles.

    Regression, 2026-08-10: confirmed live that _dispatch_fallback_operations already attempts
    real work (e.g. routing optimize_manifests to Spark when the native engine can't run it),
    but its outcome only ever reached logger.info/logger.error -- a job could be marked
    "succeeded" overall while its manifest consolidation silently failed, with zero persisted
    trace. record_audit() already existed for exactly this (AuditRecord has a
    manifests_rewritten field) but nothing here had ever called it. The audit write itself is
    wrapped separately from the dispatch attempt it's recording -- a DB hiccup writing the
    audit trail must not be mistaken for (or mask) the real dispatch outcome already logged.
    """
    try:
        from cryo.worker.worker import AuditRecord

        store.record_audit(
            AuditRecord(
                job_id=job_id,
                table_id=table_id,
                action=operation.type,
                status=status,
                engine=engine,
                detail=detail,
                duration_ms=duration_ms,
                manifests_rewritten=manifests_rewritten,
                engine_ref=engine_ref,
            )
        )
    except Exception:
        logger.exception(
            "%s: failed to record audit for fallback %s (dispatch outcome itself already logged "
            "above; this only affects whether it's also queryable)",
            table_ref,
            operation.type.value,
        )


def _dispatch_fallback_operations(
    *,
    store: JobStore,
    fallback_operations: tuple[tuple[Operation, str], ...],
    plan: MaintenancePlan,
    destination_config: Mapping[str, Any],
    source_config: Mapping[str, Any],
    table_ref: str,
    job_id: str,
    table_id: str,
    table_stats: Mapping[str, Any] | None = None,
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
        for operation, reason in fallback_operations:
            _record_fallback_audit(
                store,
                job_id=job_id,
                table_id=table_id,
                table_ref=table_ref,
                operation=operation,
                status="SKIPPED",
                engine="none",
                detail=f"no sql_fallback_destination_id configured ({reason})",
            )
        return

    try:
        from cryo.engines.factory import create_engine
        from cryo.engines.spark.k8s_engine import SparkK8sEngine

        try:
            fallback_destination = store.get_destination(fallback_id.strip())
        except KeyError:
            logger.error("%s: sql_fallback_destination_id %r not found", table_ref, fallback_id)
            for operation, reason in fallback_operations:
                _record_fallback_audit(
                    store,
                    job_id=job_id,
                    table_id=table_id,
                    table_ref=table_ref,
                    operation=operation,
                    status="FAILED",
                    engine="none",
                    detail=f"sql_fallback_destination_id {fallback_id!r} not found ({reason})",
                )
            return

        fallback_engine = create_engine(
            fallback_destination.engine,
            _fallback_engine_config(fallback_destination.config, source_config),
        )
        if isinstance(fallback_engine, SparkK8sEngine):
            fallback_engine = fallback_engine.with_table_stats(table_stats)
        for operation, reason in fallback_operations:
            if not native_fallback_supports(fallback_engine.type, operation.type):
                mismatch_detail = native_fallback_mismatch_detail(
                    fallback_engine.type, operation.type
                )
                logger.error("%s: %s", table_ref, mismatch_detail)
                _record_fallback_audit(
                    store,
                    job_id=job_id,
                    table_id=table_id,
                    table_ref=table_ref,
                    operation=operation,
                    status="SKIPPED",
                    engine=fallback_engine.type,
                    detail=mismatch_detail,
                )
                continue
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
            # PDAE-8352: submit() (fire-and-forget, no wait_for_completion()) exists only
            # on SparkK8sEngine -- that's the destination this actually matters for (Spark
            # fallback dispatches are the ones with real wall-clock submit+run time that
            # were capping the whole wave's throughput at the fallback destination's
            # slot_cap). Any other engine (e.g. Trino) keeps the synchronous execute() path;
            # those calls are fast enough that blocking on them was never the bottleneck.
            use_async = isinstance(fallback_engine, SparkK8sEngine)
            for statement in fallback_engine.render(fallback_plan):
                result = (
                    fallback_engine.submit(statement)
                    if isinstance(fallback_engine, SparkK8sEngine)
                    else fallback_engine.execute(statement)
                )
                if use_async and result.ok:
                    logger.info(
                        "%s: fallback %s submitted (engine_ref=%s, outcome pending)",
                        table_ref,
                        operation.type.value,
                        result.engine_ref,
                    )
                elif result.ok:
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
                # Regression, confirmed live 2026-08-11: ExecResult.detail is unconditionally
                # populated on both success and failure (k8s_engine.py sets detail=stmt.sql
                # even in its except-blocks, alongside the real error= text) -- `detail or
                # error or reason` therefore always picked the SQL statement and never once
                # reached the actual exception. Every fallback-dispatch failure's audit row
                # showed only "CALL cdp.system.rewrite_manifests(...)", never why it failed.
                # On failure, prefer the real error text; only fall back to detail/reason when
                # the engine genuinely didn't produce one.
                audit_detail = (
                    (result.error or result.detail or reason)
                    if not result.ok
                    else (result.detail or result.error or reason)
                )
                audit_status = (
                    "SUBMITTED" if (use_async and result.ok) else ("OK" if result.ok else "FAILED")
                )
                _record_fallback_audit(
                    store,
                    job_id=job_id,
                    table_id=table_id,
                    table_ref=table_ref,
                    operation=operation,
                    status=audit_status,
                    engine=fallback_engine.type,
                    detail=audit_detail,
                    engine_ref=result.engine_ref if use_async else None,
                    duration_ms=result.duration_ms,
                    manifests_rewritten=result.manifests_rewritten,
                )
    except Exception:
        logger.exception("%s: SQL fallback dispatch raised unexpectedly", table_ref)


def _fallback_dispatch_concurrency(
    destination_config: Mapping[str, Any],
    *,
    store: JobStore,
) -> int:
    """Resolve how many fallback dispatches to run at once.

    Mirrors the fallback destination's own slot_cap when resolvable (same capacity
    semantics an operator already configured for that destination), falling back to
    a conservative default so a misconfigured/missing fallback destination can't
    blow up concurrency -- this only bounds a thread pool, not real admission, so it
    fails closed to the default rather than to unlimited.
    """
    fallback_id = destination_config.get("sql_fallback_destination_id") or destination_config.get(
        "fallback_destination_id"
    )
    if isinstance(fallback_id, str) and fallback_id.strip():
        try:
            fallback_destination = store.get_destination(fallback_id.strip())
            slot_cap = fallback_destination.config.get("slot_cap")
            if slot_cap:
                return max(1, int(slot_cap))
        except Exception:
            logger.debug(
                "Could not resolve slot_cap for fallback destination %s; using default %d",
                fallback_id,
                _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY,
            )
    return _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY


def _run_fallback_dispatches_concurrently(
    dispatches: Sequence[Callable[[], None]],
    *,
    destination_config: Mapping[str, Any],
    store: JobStore,
) -> None:
    """Run queued native-unsupported-operation fallback dispatches in parallel.

    Each callable is a bound call to _dispatch_fallback_operations for one table;
    that function already catches and logs every exception internally and never
    raises (see its docstring), so running many concurrently via a thread pool is
    safe -- a stuck/slow table can't block the others, and a failing one can't take
    down the batch. Previously these ran one at a time, inline, sequentially inside
    this single-leader Governor process regardless of the fallback destination's
    configured slot_cap; that was fine for the occasional one-off fallback table but
    became the dominant bottleneck for any run where a whole cohort needs the same
    native-unsupported operation (e.g. optimize_manifests) -- confirmed live,
    2026-08-05: ~65-70s per table, ~1 concurrent, projecting ~90 hours for a
    2,833-table rollout despite the destination's slot_cap=6 and idle cluster
    headroom neither of which this path previously consulted.

    This helper still runs **every** callable it is given (bounded by slot_cap
    workers). The wave-level cap -- "at most one slot_cap-sized batch, then yield
    so run_wave can admit the regular async queue" -- lives one level up in
    ``split_fallback_dispatch_wave`` / ``Governor._run_one_fallback_dispatch_wave``
    (PDAE-8352, 2026-08-18: 49 dispatches, 0 "Wave complete" in 10 min, ~9.5 min
    per wave when this ran the full cohort before returning).
    """
    concurrency = min(
        _fallback_dispatch_concurrency(destination_config, store=store),
        len(dispatches),
    )
    if concurrency <= 1:
        for dispatch in dispatches:
            dispatch()
        return

    # Diagnostic logging only, added 2026-08-16 after cryo-worker pods OOMKilled 46-77x each
    # in a burst with the same "Created Spark-on-K8s dispatch Job ... Deleted Spark driver
    # pod" signature as the 2026-08-14 client-caching fix -- that fix is confirmed live and
    # its own code path looks clean on review, so this batch's real memory footprint
    # (concurrency up to _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY=6 simultaneous Spark-K8s
    # dispatches) is the next suspect. Nothing here queried at investigation time could
    # reconstruct what RSS actually did during the burst -- this makes that data available
    # for the next occurrence instead of guessing again. rss_mib is None if the `resource`
    # module is unavailable (non-POSIX) rather than failing the batch over a metric.
    rss_before_mib = _process_rss_mib()
    logger.info(
        "Fallback dispatch batch starting: %d dispatch(es), concurrency=%d, rss=%s MiB",
        len(dispatches),
        concurrency,
        rss_before_mib,
    )

    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="fallback-dispatch"
    ) as executor:
        futures = [executor.submit(dispatch) for dispatch in dispatches]
        for future in as_completed(futures):
            # dispatch() itself never raises (best-effort, logs internally) -- this
            # is a defensive backstop only, so one truly unexpected exception can't
            # silently swallow the rest of the batch's futures.
            try:
                future.result()
            except Exception:
                logger.exception("Fallback dispatch task raised unexpectedly")

    rss_after_mib = _process_rss_mib()
    logger.info(
        "Fallback dispatch batch finished: %d dispatch(es), concurrency=%d, rss=%s MiB "
        "(was %s MiB)",
        len(dispatches),
        concurrency,
        rss_after_mib,
        rss_before_mib,
    )


def split_fallback_dispatch_wave(
    dispatches: Sequence[Callable[[], None]],
    *,
    destination_config: Mapping[str, Any],
    store: JobStore,
) -> tuple[tuple[Callable[[], None], ...], tuple[Callable[[], None], ...]]:
    """Cap fallback work to one slot_cap-sized concurrent batch; return the rest.

    ``_run_fallback_dispatches_concurrently`` already bounds *in-flight* dispatches
    to slot_cap. This is the next level up: a wave that collected dozens of
    fallbacks must not wait for every batch of that pool before ``run_wave()``
    returns. Live evidence (PDAE-8352, 2026-08-18): 49 dispatches, slot_cap=6,
    40–80s each, ~9.5 min per wave, 0 "Wave complete" in 10 min -- regular
    async-queue admission starved for the whole cohort.

    The returned ``this_wave`` length is ``min(len(dispatches), slot_cap)`` so
    the concurrent helper still saturates the configured cap; ``later_waves``
    must be held and retried on subsequent ``run_wave`` ticks.
    """
    if not dispatches:
        return (), ()
    cap = _fallback_dispatch_concurrency(destination_config, store=store)
    batch_size = min(cap, len(dispatches))
    return tuple(dispatches[:batch_size]), tuple(dispatches[batch_size:])


def _process_rss_mib() -> float | None:
    """Best-effort current process peak RSS in MiB, for the diagnostic logging above.

    Uses the stdlib `resource` module (POSIX-only, already true of every environment this
    runs in) rather than adding a psutil dependency for two log lines. `ru_maxrss` is
    cumulative peak RSS for the process's lifetime (not current RSS) -- that's actually more
    useful here than instantaneous RSS: it captures the batch's real peak even if a spike
    happens between the before/after log calls, which an instantaneous read could miss
    entirely.

    `ru_maxrss`'s unit is platform-dependent: KiB on Linux (every real deploy target for
    this codebase), bytes on macOS/BSD (a real gap caught locally, 2026-08-16: without this
    branch the value came back ~1000x too large when smoke-tested on a Mac). Divides
    accordingly rather than assuming Linux's convention everywhere.
    """
    try:
        import platform
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 if platform.system() == "Linux" else 1024 * 1024
        return round(raw / divisor, 1)
    except Exception:  # noqa: BLE001 - diagnostic only, must never affect dispatch
        return None


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

    Native-unsupported operations (typically ``optimize_manifests``) are returned
    on ``pending_fallback_dispatches`` and are **not** executed here. The Governor
    caps them to one slot_cap concurrent batch per ``run_wave`` so the regular
    async queue is not starved (PDAE-8352).
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
    # Collected here instead of dispatched inline -- see _run_fallback_dispatches_concurrently.
    pending_fallback_dispatches: list[Callable[[], None]] = []

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
        slots_left, cpu_left, mem_left = cluster_headroom(live_capacity, config=destination.config)
        metastore_headroom = _MetastoreHeadroom(store)
        fleet_rate_limiter = _FleetAdmissionRateLimiter(store)

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

            if not fleet_rate_limiter.has_headroom():
                deferred += _defer_spec(
                    spec,
                    defer_job=defer_job,
                    release_lock=release_lock,
                    reason="fleet_admission_rate_cap",
                    resolved=resolved,
                )
                continue
            fleet_rate_limiter.reserve(len(spec.members))

            spec_uris = _spec_metastore_uris(spec, by_job_id)
            over_capacity_uri = next(
                (uri for uri in spec_uris if not metastore_headroom.has_headroom(uri)), None
            )
            if over_capacity_uri is not None:
                deferred += _defer_spec(
                    spec,
                    defer_job=defer_job,
                    release_lock=release_lock,
                    reason=f"metastore_capacity:{over_capacity_uri}",
                    resolved=resolved,
                )
                continue
            for uri in spec_uris:
                metastore_headroom.reserve(uri)

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
                    skipped = skip_operations_from_preset(prepared_item.job.operations)
                    if skipped:
                        plan_params["skip_operations"] = skipped
                    plan = build_plan(
                        prepared_item.stats,
                        prepared_item.table_data.partitions,
                        prepared_item.mode,
                        plan_params,
                    )
                    plan = gate_native_optimize_manifests(
                        plan, operations=prepared_item.job.operations, destination=destination
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
                    if built.fallback_operations:
                        pending_fallback_dispatches.append(
                            partial(
                                _dispatch_fallback_operations,
                                store=store,
                                fallback_operations=built.fallback_operations,
                                plan=plan,
                                destination_config=destination.config,
                                source_config=prepared_item.table_data.source_config,
                                table_ref=plan.table_ref,
                                job_id=prepared_item.job.id,
                                table_id=prepared_item.job.table_id,
                                table_stats={
                                    key: prepared_item.stats[key]
                                    for key in (
                                        "total_size_gb",
                                        "total_files",
                                        "manifest_count",
                                        "total_snapshots",
                                    )
                                    if key in prepared_item.stats
                                },
                            )
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

        # Do not run fallbacks here. Returning them lets run_wave admit the rest
        # of the async queue (and return "Wave complete") after at most one
        # slot_cap batch -- see split_fallback_dispatch_wave.
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
        pending_fallback_dispatches=tuple(pending_fallback_dispatches),
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


class _MetastoreHeadroom:
    """Remaining admission headroom per metastore URI. A URI with no currently-running
    jobs at all is absent from the underlying count query's GROUP BY result -- treated
    here as fully open (cap slots free), not zero. Real incident, 2026-08-12: see
    Settings.max_concurrent_jobs_per_metastore's docstring for the full incident this
    guards against.

    Duck-typed like list_running_jobs -- not every JobStore implementation (e.g. tests'
    InMemoryJobStore) needs to support count_running_jobs_by_metastore_uri(); a store
    that doesn't leaves this permanently "no cap enforced" (has_headroom always True).
    """

    def __init__(self, store: JobStore) -> None:
        self._cap = get_settings().max_concurrent_jobs_per_metastore
        self._remaining: dict[str, int] = {}
        counter = getattr(store, "count_running_jobs_by_metastore_uri", None)
        if not callable(counter):
            return
        try:
            current_counts = counter()
        except Exception:
            logger.warning("Failed to compute metastore concurrency counts", exc_info=True)
            return
        self._remaining = {uri: max(0, self._cap - count) for uri, count in current_counts.items()}

    def has_headroom(self, uri: str) -> bool:
        return self._remaining.get(uri, self._cap) > 0

    def reserve(self, uri: str) -> None:
        if uri in self._remaining:
            self._remaining[uri] -= 1
        else:
            self._remaining[uri] = self._cap - 1


class _FleetAdmissionRateLimiter:
    """Blunt, mechanism-agnostic ceiling on fleet-wide admission rate. See
    Settings.fleet_admission_rate_cap's docstring for the incident this guards against --
    unlike _MetastoreHeadroom (which closes one specific downstream bottleneck), this
    bounds the burst itself, so a *different* bottleneck hitting the same admission
    volume still gets caught.

    Duck-typed like _MetastoreHeadroom -- a JobStore that doesn't implement
    count_jobs_started_since leaves this permanently "no cap enforced" (has_headroom
    always True), matching every other guardrail's fail-open-on-missing-support
    contract in this module.
    """

    def __init__(self, store: JobStore) -> None:
        settings = get_settings()
        self._cap = settings.fleet_admission_rate_cap
        self._remaining = self._cap
        counter = getattr(store, "count_jobs_started_since", None)
        if not callable(counter):
            return
        try:
            from datetime import UTC, datetime, timedelta

            cutoff = datetime.now(UTC) - timedelta(
                minutes=settings.fleet_admission_rate_window_minutes
            )
            current_count = counter(cutoff)
        except Exception:
            logger.warning("Failed to compute fleet admission rate", exc_info=True)
            return
        self._remaining = max(0, self._cap - current_count)

    def has_headroom(self) -> bool:
        return self._remaining > 0

    def reserve(self, n: int = 1) -> None:
        self._remaining = max(0, self._remaining - n)


def _spec_metastore_uris(spec: DispatchSpec, by_job_id: Mapping[str, _PreparedJob]) -> set[str]:
    uris: set[str] = set()
    for member in spec.members:
        prepared_item = by_job_id.get(member.job_id)
        if prepared_item is None:
            continue
        uri = prepared_item.table_data.source_config.get("uri")
        if isinstance(uri, str) and uri:
            uris.add(uri)
    return uris


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
