"""Unit tests for native_admit's SQL fallback dispatch (Governor create-on-admit path)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.governor.native_admit import (
    _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY,
    _dispatch_fallback_operations,
    _fallback_dispatch_concurrency,
    _run_fallback_dispatches_concurrently,
)
from cryo.worker.worker import DestinationConfig, InMemoryJobStore


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="cdp.mview.load_test_basefact",
        mode=Mode.DEEP,
        operations=(),
    )


def test_no_fallback_operations_is_a_noop() -> None:
    store = InMemoryJobStore()
    with patch("cryo.engines.factory.create_engine") as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=(),
            plan=_plan(),
            destination_config={},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-1",
            table_id="table-1",
        )
    create_engine.assert_not_called()


def test_missing_fallback_destination_id_logs_and_returns() -> None:
    store = InMemoryJobStore()
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)
    with patch("cryo.engines.factory.create_engine") as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-1",
            table_id="table-1",
        )
    create_engine.assert_not_called()


def test_unresolvable_fallback_destination_does_not_raise() -> None:
    store = InMemoryJobStore()  # no destinations registered
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "reason"),)
    _dispatch_fallback_operations(
        store=store,
        fallback_operations=fallback_ops,
        plan=_plan(),
        destination_config={"sql_fallback_destination_id": "missing-dest"},
        source_config={},
        table_ref="cdp.mview.load_test_basefact",
        job_id="job-1",
        table_id="table-1",
    )  # must not raise


def test_dispatches_each_fallback_operation_to_the_configured_engine() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"submit_mode": "k8s"})
    )
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL cdp.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(ok=True, engine_ref="job-1", duration_ms=100)

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine) as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-1",
            table_id="table-1",
        )

    create_engine.assert_called_once_with(EngineType.SPARK_EMR, {"submit_mode": "k8s"})
    mock_engine.execute.assert_called_once()


def test_source_s3_credentials_are_merged_into_fallback_engine_config() -> None:
    """The native engine gets S3 credentials from source_config (payload.py); the Spark
    fallback needs the same ones, since the node's ambient IAM role alone isn't enough
    (confirmed live: AccessDeniedException with only the instance role)."""
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"submit_mode": "k8s"})
    )
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "reason"),)
    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL cdp.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(ok=True, engine_ref="job-1", duration_ms=100)

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine) as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={"s3.access-key-id": "AKIA...", "s3.secret-access-key": "shh"},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-1",
            table_id="table-1",
        )

    passed_config = create_engine.call_args.args[1]
    assert passed_config["spark_k8s"]["s3_access_key"] == "AKIA..."
    assert passed_config["spark_k8s"]["s3_secret_key"] == "shh"
    # Original destination config in the store must be untouched (never persisted).
    assert "spark_k8s" not in store.get_destination("spark-1").config


def test_source_catalog_connection_overrides_destination_default() -> None:
    """Regression, 2026-07-24: a single SQL-fallback destination is shared across every
    table routed to it, so its spark_k8s.catalog_uri/catalog_warehouse/catalog are only a
    static default -- correct for whichever catalog the destination was originally tuned
    for, wrong for any other. Confirmed live: lms.silver_adminactions (source catalog_name
    "hive", dev metastore/warehouse) fell back to the same destination as
    cdp.mview.load_test_basefact (static config: catalog "cdp", prod metastore/warehouse).
    Spark registered its Iceberg catalog plugin under the destination's static "cdp" name
    while the rendered SQL referenced "hive.system.rewrite_data_files", so the CALL silently
    resolved against the builtin spark_catalog and failed with
    MISSING_CATALOG_ABILITY.PROCEDURES. The source's own connection info must win."""
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            id="spark-1",
            engine=EngineType.SPARK_EMR,
            config={
                "catalog": "cdp",
                "spark_k8s": {
                    "catalog_impl": "hive",
                    "catalog_uri": "thrift://trino-prod-metastore.penpencil.co:9083",
                    "catalog_warehouse": "s3://pw-data-cdp-prod-gold-layer/",
                },
            },
        )
    )
    fallback_ops = ((Operation(OperationType.OPTIMIZE, {}), "reason"),)
    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL hive.system.rewrite_data_files(...)")]
    mock_engine.execute.return_value = MagicMock(ok=True, engine_ref="job-1", duration_ms=100)

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine) as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={
                "catalog_name": "hive",
                "uri": "thrift://trino-metastore-v2.penpencil.co:9083",
                "warehouse": "s3://pw-data-cdp-dev-bronze-layer/",
            },
            table_ref="hive.lms.silver_adminactions",
            job_id="job-1",
            table_id="table-1",
        )

    passed_config = create_engine.call_args.args[1]
    assert passed_config["catalog"] == "hive"
    assert (
        passed_config["spark_k8s"]["catalog_uri"] == "thrift://trino-metastore-v2.penpencil.co:9083"
    )
    assert passed_config["spark_k8s"]["catalog_warehouse"] == "s3://pw-data-cdp-dev-bronze-layer/"
    # catalog_impl wasn't overridden (source doesn't carry one) -- the destination's default
    # correctly still applies since both this source and the "cdp" default are
    # Hive-Metastore-backed.
    assert passed_config["spark_k8s"]["catalog_impl"] == "hive"
    # Original destination config in the store must be untouched (never persisted).
    assert store.get_destination("spark-1").config["catalog"] == "cdp"


def test_successful_fallback_dispatch_is_recorded_as_a_queryable_audit() -> None:
    """Regression, 2026-08-10: _dispatch_fallback_operations already attempted real work
    (routing optimize_manifests to Spark), but the outcome only ever reached
    logger.info/logger.error -- a job could show "succeeded" overall while manifest
    consolidation silently never happened, with zero persisted trace. This is the fix:
    the outcome must land in store.record_audit(), not just application logs."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL cdp.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=True, engine_ref="spark-job-1", duration_ms=4200, manifests_rewritten=13, error=None,
        detail="rewrote 13 manifests",
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.job_id == "job-42"
    assert record.table_id == "table-42"
    assert record.action == OperationType.OPTIMIZE_MANIFESTS
    assert record.status == "OK"
    assert record.engine == EngineType.SPARK_EMR
    assert record.manifests_rewritten == 13
    assert record.duration_ms == 4200


def test_failed_fallback_dispatch_is_recorded_not_just_logged() -> None:
    """The exact scenario this whole fix exists for: the fallback attempt fails, and that
    failure must be visible somewhere queryable, not just a log line."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL cdp.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=False, engine_ref=None, duration_ms=1100, manifests_rewritten=None,
        error="MISSING_CATALOG_ABILITY.PROCEDURES", detail=None,
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.status == "FAILED"
    assert record.detail == "MISSING_CATALOG_ABILITY.PROCEDURES"


def test_engine_type_mismatch_is_recorded_as_skipped() -> None:
    store = InMemoryJobStore()
    # Trino fallback destination that can't actually run optimize_manifests either.
    store.add_destination(DestinationConfig(id="trino-1", engine=EngineType.TRINO, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    with patch(
        "cryo.governor.native_admit.native_fallback_supports", return_value=False
    ), patch("cryo.engines.factory.create_engine") as create_engine:
        mock_engine = MagicMock()
        mock_engine.type = EngineType.TRINO
        create_engine.return_value = mock_engine
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "trino-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    assert store.audit[0].status == "SKIPPED"
    mock_engine.execute.assert_not_called()


def test_missing_fallback_destination_id_is_recorded_as_skipped_not_only_logged() -> None:
    store = InMemoryJobStore()
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)
    _dispatch_fallback_operations(
        store=store,
        fallback_operations=fallback_ops,
        plan=_plan(),
        destination_config={},
        source_config={},
        table_ref="cdp.mview.load_test_basefact",
        job_id="job-42",
        table_id="table-42",
    )
    assert len(store.audit) == 1
    assert store.audit[0].status == "SKIPPED"


def test_unresolvable_destination_is_recorded_as_failed_not_only_logged() -> None:
    store = InMemoryJobStore()  # no destinations registered
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "reason"),)
    _dispatch_fallback_operations(
        store=store,
        fallback_operations=fallback_ops,
        plan=_plan(),
        destination_config={"sql_fallback_destination_id": "missing-dest"},
        source_config={},
        table_ref="cdp.mview.load_test_basefact",
        job_id="job-42",
        table_id="table-42",
    )
    assert len(store.audit) == 1
    assert store.audit[0].status == "FAILED"


def test_audit_write_failure_does_not_break_the_dispatch_itself() -> None:
    """The audit write is a second, independent thing that can fail on its own (a DB
    hiccup) -- it must never mask or crash the real dispatch outcome that's already been
    logged and attempted."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "reason"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL cdp.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=True, engine_ref="job-1", duration_ms=100, manifests_rewritten=1, error=None, detail=None
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine), patch.object(
        store, "record_audit", side_effect=RuntimeError("db hiccup")
    ):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-42",
            table_id="table-42",
        )  # must not raise
    mock_engine.execute.assert_called_once()


def test_fallback_engine_exception_is_caught_not_raised() -> None:
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "reason"),)

    with patch("cryo.engines.factory.create_engine", side_effect=RuntimeError("boom")):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
            job_id="job-1",
            table_id="table-1",
        )  # must not raise -- a fallback failure can't break the native Job dispatch


# --- _fallback_dispatch_concurrency -----------------------------------------------


def test_fallback_dispatch_concurrency_resolves_from_destination_slot_cap() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 8})
    )
    got = _fallback_dispatch_concurrency(
        {"sql_fallback_destination_id": "spark-1"}, store=store
    )
    assert got == 8


def test_fallback_dispatch_concurrency_falls_back_to_default_when_missing_id() -> None:
    store = InMemoryJobStore()
    got = _fallback_dispatch_concurrency({}, store=store)
    assert got == _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY


def test_fallback_dispatch_concurrency_falls_back_to_default_when_destination_missing() -> None:
    store = InMemoryJobStore()  # no destinations registered
    got = _fallback_dispatch_concurrency(
        {"sql_fallback_destination_id": "missing-dest"}, store=store
    )
    assert got == _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY


def test_fallback_dispatch_concurrency_falls_back_to_default_when_slot_cap_unset() -> None:
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    got = _fallback_dispatch_concurrency(
        {"sql_fallback_destination_id": "spark-1"}, store=store
    )
    assert got == _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY


# --- _run_fallback_dispatches_concurrently ----------------------------------------


def test_run_fallback_dispatches_concurrently_empty_list_is_noop() -> None:
    store = InMemoryJobStore()
    _run_fallback_dispatches_concurrently([], destination_config={}, store=store)  # must not raise


def test_run_fallback_dispatches_concurrently_calls_every_dispatch() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 4})
    )
    calls: list[int] = []
    lock = threading.Lock()

    def make_dispatch(i: int):
        def _dispatch() -> None:
            with lock:
                calls.append(i)

        return _dispatch

    dispatches = [make_dispatch(i) for i in range(10)]
    _run_fallback_dispatches_concurrently(
        dispatches, destination_config={"sql_fallback_destination_id": "spark-1"}, store=store
    )
    assert sorted(calls) == list(range(10))


def test_run_fallback_dispatches_concurrently_respects_slot_cap_bound() -> None:
    """The whole point of this refactor: many tables' fallback dispatches must be able
    to run at once (previously exactly 1 at a time, regardless of slot_cap -- see the
    module docstring on _run_fallback_dispatches_concurrently), but bounded by the
    fallback destination's own configured slot_cap, not unbounded."""
    store = InMemoryJobStore()
    slot_cap = 3
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": slot_cap})
    )
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_dispatch() -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    dispatches = [slow_dispatch for _ in range(9)]
    _run_fallback_dispatches_concurrently(
        dispatches, destination_config={"sql_fallback_destination_id": "spark-1"}, store=store
    )
    # Some real concurrency happened (not serialized to 1)...
    assert max_active > 1
    # ...but never exceeded the configured slot_cap.
    assert max_active <= slot_cap


def test_run_fallback_dispatches_concurrently_single_dispatch_runs_synchronously() -> None:
    store = InMemoryJobStore()
    ran = []
    _run_fallback_dispatches_concurrently(
        [lambda: ran.append(1)], destination_config={}, store=store
    )
    assert ran == [1]


def test_run_fallback_dispatches_concurrently_one_failure_does_not_block_others() -> None:
    """Defensive backstop: _dispatch_fallback_operations itself never raises (it's
    best-effort and catches internally), but the thread pool must not let one truly
    unexpected exception swallow the rest of the batch either."""
    store = InMemoryJobStore()
    ran: list[int] = []
    lock = threading.Lock()

    def failing_dispatch() -> None:
        raise RuntimeError("boom")

    def ok_dispatch(i: int):
        def _dispatch() -> None:
            with lock:
                ran.append(i)

        return _dispatch

    dispatches = [ok_dispatch(0), failing_dispatch, ok_dispatch(1), ok_dispatch(2)]
    _run_fallback_dispatches_concurrently(
        dispatches, destination_config={}, store=store
    )  # must not raise
    assert sorted(ran) == [0, 1, 2]
