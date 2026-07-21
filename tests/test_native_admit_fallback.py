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
            table_ref="cdp.mview.load_test_basefact",
        )

    create_engine.assert_called_once_with(EngineType.SPARK_EMR, {"submit_mode": "k8s"})
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
            table_ref="cdp.mview.load_test_basefact",
        )  # must not raise -- a fallback failure can't break the native Job dispatch
