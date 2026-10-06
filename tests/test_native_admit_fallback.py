"""Unit tests for native_admit's SQL fallback dispatch (Governor create-on-admit path)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.k8s_engine import SparkK8sEngine
from cryo.governor.native_admit import (
    _DEFAULT_FALLBACK_DISPATCH_CONCURRENCY,
    _dispatch_fallback_operations,
    _fallback_dispatch_concurrency,
    _process_rss_mib,
    _run_fallback_dispatches_concurrently,
    split_fallback_dispatch_wave,
)
from cryo.worker.worker import DestinationConfig, InMemoryJobStore


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="demo.mart.load_test_events",
        mode=Mode.DEEP,
        operations=(),
    )


def test_spark_fallback_uses_table_sizing_before_submit(monkeypatch) -> None:
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    engine, client = _spark_engine_with_mock_client(monkeypatch)
    client.submit_spark_sql.return_value = "cryo-spark-small"
    sized_configs = []

    def sized_client(config):
        sized_configs.append(config)
        return client

    monkeypatch.setattr("cryo.engines.spark.k8s_engine.SparkK8sClient", sized_client)
    with patch("cryo.engines.factory.create_engine", return_value=engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "unsupported"),),
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref=_plan().table_ref,
            job_id="job-1",
            table_id="table-1",
            table_stats={"total_size_gb": 0.1, "total_files": 12},
        )
    client.submit_spark_sql.assert_called_once()
    # submit() constructs its client from the cloned engine's scaled configuration.
    assert sized_configs[-1].executor_instances == 1
    assert sized_configs[-1].executor_mem_mib == 1024
    client.wait_for_completion.assert_not_called()


def test_no_fallback_operations_is_a_noop() -> None:
    store = InMemoryJobStore()
    with patch("cryo.engines.factory.create_engine") as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=(),
            plan=_plan(),
            destination_config={},
            source_config={},
            table_ref="demo.mart.load_test_events",
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
            table_ref="demo.mart.load_test_events",
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
        table_ref="demo.mart.load_test_events",
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
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(ok=True, engine_ref="job-1", duration_ms=100)

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine) as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
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
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(ok=True, engine_ref="job-1", duration_ms=100)

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine) as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={"s3.access-key-id": "AKIA...", "s3.secret-access-key": "shh"},
            table_ref="demo.mart.load_test_events",
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
    demo.mart.load_test_events (static config: catalog "sample", prod metastore/warehouse).
    Spark registered its Iceberg catalog plugin under the destination's static "sample" name
    while the rendered SQL referenced "hive.system.rewrite_data_files", so the CALL silently
    resolved against the builtin spark_catalog and failed with
    MISSING_CATALOG_ABILITY.PROCEDURES. The source's own connection info must win."""
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            id="spark-1",
            engine=EngineType.SPARK_EMR,
            config={
                "catalog": "sample",
                "spark_k8s": {
                    "catalog_impl": "hive",
                    "catalog_uri": "thrift://metastore.example.invalid:9083",
                    "catalog_warehouse": "s3://example-prod-bucket/",
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
                "uri": "thrift://metastore-dev.example.invalid:9083",
                "warehouse": "s3://example-dev-bucket/",
            },
            table_ref="hive.lms.silver_adminactions",
            job_id="job-1",
            table_id="table-1",
        )

    passed_config = create_engine.call_args.args[1]
    assert passed_config["catalog"] == "hive"
    assert (
        passed_config["spark_k8s"]["catalog_uri"] == "thrift://metastore-dev.example.invalid:9083"
    )
    assert passed_config["spark_k8s"]["catalog_warehouse"] == "s3://example-dev-bucket/"
    # catalog_impl wasn't overridden (source doesn't carry one) -- the destination's default
    # correctly still applies since both this source and the "sample" default are
    # Hive-Metastore-backed.
    assert passed_config["spark_k8s"]["catalog_impl"] == "hive"
    # Original destination config in the store must be untouched (never persisted).
    assert store.get_destination("spark-1").config["catalog"] == "sample"


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
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=True,
        engine_ref="spark-job-1",
        duration_ms=4200,
        manifests_rewritten=13,
        error=None,
        detail="rewrote 13 manifests",
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
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
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=False,
        engine_ref=None,
        duration_ms=1100,
        manifests_rewritten=None,
        error="MISSING_CATALOG_ABILITY.PROCEDURES",
        detail=None,
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.status == "FAILED"
    assert record.detail == "MISSING_CATALOG_ABILITY.PROCEDURES"


def test_failed_dispatch_with_detail_and_error_both_set_prefers_the_real_error() -> None:
    """Regression, confirmed live 2026-08-11: k8s_engine.py's real failure paths set BOTH
    detail=stmt.sql (unconditionally, success or failure) and error=<the real exception>.
    `detail or error or reason` therefore always picked the SQL statement and never once
    surfaced the actual cause -- confirmed against real fallback-dispatch audit rows, every
    single one showed only "CALL demo.system.rewrite_manifests(...)", never why it failed.
    The prior test (test_failed_fallback_dispatch_is_recorded_not_just_logged) sets
    detail=None on its mock, which accidentally never exercised this bug -- this test matches
    the real ExecResult shape a failing engine actually returns."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=False,
        engine_ref=None,
        duration_ms=1100,
        manifests_rewritten=None,
        error="Caused by: org.apache.iceberg.exceptions.CommitFailedException: real cause",
        detail="CALL demo.system.rewrite_manifests(table => 'demo.learning.silver_user_activity')",
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.learning.silver_user_activity",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.status == "FAILED"
    assert "CommitFailedException: real cause" in record.detail
    assert (
        record.detail
        != "CALL demo.system.rewrite_manifests(table => 'demo.learning.silver_user_activity')"
    )


def test_successful_dispatch_with_detail_and_error_both_set_prefers_detail() -> None:
    """On success, detail (the SQL/outcome description) should still win over a stray error
    field an engine might leave populated -- only the failure path needs the error-first
    precedence flip."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.SPARK_EMR
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=True,
        engine_ref="run-1",
        duration_ms=1100,
        manifests_rewritten=3,
        error=None,
        detail="CALL demo.system.rewrite_manifests(table => 'demo.learning.silver_user_activity')",
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.learning.silver_user_activity",
            job_id="job-42",
            table_id="table-42",
        )

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.status == "OK"
    assert (
        record.detail
        == "CALL demo.system.rewrite_manifests(table => 'demo.learning.silver_user_activity')"
    )


def test_engine_type_mismatch_is_recorded_as_skipped() -> None:
    store = InMemoryJobStore()
    # Trino fallback destination that can't actually run optimize_manifests either.
    store.add_destination(DestinationConfig(id="trino-1", engine=EngineType.TRINO, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    with (
        patch("cryo.governor.native_admit.native_fallback_supports", return_value=False),
        patch("cryo.engines.factory.create_engine") as create_engine,
    ):
        mock_engine = MagicMock()
        mock_engine.type = EngineType.TRINO
        create_engine.return_value = mock_engine
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "trino-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
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
        table_ref="demo.mart.load_test_events",
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
        table_ref="demo.mart.load_test_events",
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
    mock_engine.render.return_value = [MagicMock(sql="CALL demo.system.rewrite_manifests(...)")]
    mock_engine.execute.return_value = MagicMock(
        ok=True, engine_ref="job-1", duration_ms=100, manifests_rewritten=1, error=None, detail=None
    )

    with (
        patch("cryo.engines.factory.create_engine", return_value=mock_engine),
        patch.object(store, "record_audit", side_effect=RuntimeError("db hiccup")),
    ):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
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
            table_ref="demo.mart.load_test_events",
            job_id="job-1",
            table_id="table-1",
        )  # must not raise -- a fallback failure can't break the native Job dispatch


# --- _fallback_dispatch_concurrency -----------------------------------------------


def test_fallback_dispatch_concurrency_resolves_from_destination_slot_cap() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 8})
    )
    got = _fallback_dispatch_concurrency({"sql_fallback_destination_id": "spark-1"}, store=store)
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
    got = _fallback_dispatch_concurrency({"sql_fallback_destination_id": "spark-1"}, store=store)
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


def _spark_engine_with_mock_client(monkeypatch) -> tuple[SparkK8sEngine, MagicMock]:
    """A real SparkK8sEngine (so isinstance(engine, SparkK8sEngine) is True, taking the
    async submit() path) with its underlying SparkK8sClient replaced by a mock -- matches
    tests/test_execute_layout_recommendation.py's _mock_spark_client pattern."""
    mock_client = MagicMock()
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client)
    engine = SparkK8sEngine(config={"catalog": "sample"})
    return engine, mock_client


def test_spark_fallback_dispatch_submits_without_waiting_for_completion(monkeypatch) -> None:
    """Regression, 2026-08-11 (): _dispatch_fallback_operations used to call
    execute() (submit + block on wait_for_completion()) inline for every native-unsupported
    operation -- with the fallback destination's own slot_cap (e.g. 6) and each dispatch
    taking real wall-clock time, this made it the effective ceiling on the whole Governor's
    per-wave admission throughput, independent of the native engine's own much higher
    slot_cap. Confirmed live: 7,855 deep-mode jobs queued, draining at ~12/min. Fix: for a
    real SparkK8sEngine, use submit() (fire-and-forget) and record status=SUBMITTED with
    the engine_ref, instead of blocking on the full result."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    engine, mock_client = _spark_engine_with_mock_client(monkeypatch)
    mock_client.submit_spark_sql.return_value = "cryo-spark-abc123"

    with patch("cryo.engines.factory.create_engine", return_value=engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "spark-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
            job_id="job-42",
            table_id="table-42",
        )

    # The whole point: wait_for_completion() must never be called from this path.
    mock_client.wait_for_completion.assert_not_called()
    mock_client.submit_spark_sql.assert_called_once()

    assert len(store.audit) == 1
    record = store.audit[0]
    assert record.status == "SUBMITTED"
    assert record.engine_ref == "cryo-spark-abc123"


def test_non_spark_fallback_dispatch_still_blocks_synchronously() -> None:
    """The async path is Spark-specific (submit() only exists on SparkK8sEngine) -- any
    other fallback engine (e.g. Trino) keeps the original synchronous execute() call,
    since those were never the bottleneck this fix targets."""
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(id="trino-1", engine=EngineType.TRINO, config={}))
    fallback_ops = ((Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),)

    mock_engine = MagicMock()
    mock_engine.type = EngineType.TRINO
    mock_engine.render.return_value = [MagicMock(sql="ALTER TABLE ... EXECUTE optimize_manifests")]
    mock_engine.execute.return_value = MagicMock(
        ok=True, engine_ref="trino-query-1", duration_ms=300, error=None, detail="done"
    )

    with patch("cryo.engines.factory.create_engine", return_value=mock_engine):
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={"sql_fallback_destination_id": "trino-1"},
            source_config={},
            table_ref="demo.mart.load_test_events",
            job_id="job-1",
            table_id="table-1",
        )

    mock_engine.execute.assert_called_once()
    assert store.audit[0].status == "OK"


class TestProcessRssMib:
    """Diagnostic-logging helper added 2026-08-16 after cryo-worker pods OOMKilled 46-77x
    each in a burst with no time-series data available to reconstruct what happened --
    _run_fallback_dispatches_concurrently now logs RSS around each concurrent batch so the
    next occurrence has real data. This is the platform-unit-handling logic behind that."""

    def test_returns_a_positive_reasonable_value_on_this_platform(self) -> None:
        result = _process_rss_mib()
        assert result is not None
        # A live pytest process's own RSS is a real, bounded number -- not exactly
        # predictable, but anywhere from a few tens of MiB to a few GiB is a sane range.
        # This is the same assertion shape that would have caught the macOS/Linux unit bug
        # (bytes vs KiB) found while smoke-testing this function locally: on macOS, dividing
        # a bytes-denominated ru_maxrss by 1024 (the Linux KiB convention) alone produces a
        # value in the tens-of-thousands-of-MiB range, clearly outside this bound.
        assert 0 < result < 10_000

    def test_uses_kib_divisor_on_linux(self, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        fake_resource = MagicMock()
        fake_resource.getrusage.return_value = MagicMock(ru_maxrss=2_097_152)  # 2 GiB in KiB
        with patch.dict("sys.modules", {"resource": fake_resource}):
            result = _process_rss_mib()
        assert result == 2048.0

    def test_uses_bytes_divisor_on_non_linux(self, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        fake_resource = MagicMock()
        fake_resource.getrusage.return_value = MagicMock(ru_maxrss=2_147_483_648)  # 2 GiB in bytes
        with patch.dict("sys.modules", {"resource": fake_resource}):
            result = _process_rss_mib()
        assert result == 2048.0

    def test_returns_none_rather_than_raising_on_failure(self, monkeypatch) -> None:
        fake_resource = MagicMock()
        fake_resource.getrusage.side_effect = RuntimeError("unavailable")
        with patch.dict("sys.modules", {"resource": fake_resource}):
            result = _process_rss_mib()
        assert result is None


def test_split_fallback_dispatch_wave_empty() -> None:
    store = InMemoryJobStore()
    this_wave, later = split_fallback_dispatch_wave([], destination_config={}, store=store)
    assert this_wave == ()
    assert later == ()


def test_split_fallback_dispatch_wave_caps_to_slot_cap_and_keeps_remainder() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 6})
    )
    dispatches = [lambda: None for _ in range(49)]
    this_wave, later = split_fallback_dispatch_wave(
        dispatches,
        destination_config={"sql_fallback_destination_id": "spark-1"},
        store=store,
    )
    assert len(this_wave) == 6
    assert len(later) == 43
    assert this_wave + later == tuple(dispatches)


def test_split_fallback_dispatch_wave_smaller_than_cap_is_all_this_wave() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 6})
    )
    dispatches = [lambda: None, lambda: None]
    this_wave, later = split_fallback_dispatch_wave(
        dispatches,
        destination_config={"sql_fallback_destination_id": "spark-1"},
        store=store,
    )
    assert len(this_wave) == 2
    assert later == ()


def test_run_fallback_dispatches_concurrently_logs_rss_around_the_batch(caplog) -> None:
    """The diagnostic logging itself: must fire for a real multi-dispatch concurrent batch
    (not the concurrency<=1 fast path, which returns before ever reaching it)."""
    import logging

    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 3})
    )
    dispatches = [lambda: None for _ in range(5)]

    with caplog.at_level(logging.INFO, logger="cryo.governor.native_admit"):
        _run_fallback_dispatches_concurrently(
            dispatches,
            destination_config={"sql_fallback_destination_id": "spark-1"},
            store=store,
        )

    messages = [r.message for r in caplog.records]
    assert any("Fallback dispatch batch starting" in m for m in messages)
    assert any("Fallback dispatch batch finished" in m for m in messages)
