"""Unit tests for native_admit's SQL fallback dispatch (Governor create-on-admit path)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.governor.native_admit import _dispatch_fallback_operations
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
        )
    create_engine.assert_not_called()


def test_missing_fallback_destination_id_logs_and_returns() -> None:
    store = InMemoryJobStore()
    fallback_ops = (
        (Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),
    )
    with patch("cryo.engines.factory.create_engine") as create_engine:
        _dispatch_fallback_operations(
            store=store,
            fallback_operations=fallback_ops,
            plan=_plan(),
            destination_config={},
            source_config={},
            table_ref="cdp.mview.load_test_basefact",
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
    )  # must not raise


def test_dispatches_each_fallback_operation_to_the_configured_engine() -> None:
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"submit_mode": "k8s"})
    )
    fallback_ops = (
        (Operation(OperationType.OPTIMIZE_MANIFESTS, {}), "not supported by native"),
    )

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
        )

    passed_config = create_engine.call_args.args[1]
    assert passed_config["catalog"] == "hive"
    assert passed_config["spark_k8s"]["catalog_uri"] == "thrift://trino-metastore-v2.penpencil.co:9083"
    assert passed_config["spark_k8s"]["catalog_warehouse"] == "s3://pw-data-cdp-dev-bronze-layer/"
    # catalog_impl wasn't overridden (source doesn't carry one) -- the destination's default
    # correctly still applies since both this source and the "cdp" default are Hive-Metastore-backed.
    assert passed_config["spark_k8s"]["catalog_impl"] == "hive"
    # Original destination config in the store must be untouched (never persisted).
    assert store.get_destination("spark-1").config["catalog"] == "cdp"


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
        )  # must not raise -- a fallback failure can't break the native Job dispatch
