"""Regression, 2026-07-27: the native (Go engine) OPTIMIZE dispatch never persisted
write.target-file-size-bytes / write.distribution-mode the way cryo/engines/spark/sql.py's
_render_optimize() does for Spark -- confirmed live, pw_bq.silver_dbt_pw_launch kept failing
the "Suboptimal properties" health check after every native OPTIMIZE run. These tests cover
the target-bytes extraction and the best-effort persistence call, mocked at the
PyIcebergSourceCatalog boundary since no real Iceberg catalog is available in unit tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import MaintenanceJob, Source, Table
from cryo.engines.native.optimize_properties import (
    _optimize_target_bytes,
    maybe_persist_optimize_properties,
)


def _job_with_plan(plan: dict) -> MaintenanceJob:
    return MaintenanceJob(
        id=uuid4(),
        run_id=uuid4(),
        table_id=uuid4(),
        destination_id=uuid4(),
        mode="deep",
        status="succeeded",
        plan=plan,
    )


def test_target_bytes_prefers_explicit_target_file_size_bytes() -> None:
    plan = {"operations": [{"type": "optimize", "params": {"target_file_size_bytes": 134217728}}]}
    assert _optimize_target_bytes(plan) == 134217728


def test_target_bytes_falls_back_to_file_size_mb() -> None:
    plan = {"operations": [{"type": "optimize", "params": {"file_size_mb": 256}}]}
    assert _optimize_target_bytes(plan) == 256 * 1024 * 1024


def test_target_bytes_defaults_when_optimize_has_no_size_params() -> None:
    plan = {"operations": [{"type": "optimize", "params": {}}]}
    assert _optimize_target_bytes(plan) == 256 * 1024 * 1024


def test_target_bytes_none_when_plan_has_no_optimize_op() -> None:
    plan = {"operations": [{"type": "expire_snapshots", "params": {}}]}
    assert _optimize_target_bytes(plan) is None


def test_target_bytes_none_for_malformed_plan() -> None:
    assert _optimize_target_bytes(None) is None
    assert _optimize_target_bytes({}) is None
    assert _optimize_target_bytes({"operations": "not-a-list"}) is None


def test_persists_properties_via_transaction_on_optimize_success(session, monkeypatch) -> None:
    table = Table(
        id=uuid4(), source_id=uuid4(), schema_name="pw_bq", table_name="silver_dbt_pw_launch"
    )
    source = Source(id=table.source_id, name="s", type="rest", config={"uri": "http://x"})
    session.add_all([table, source])
    session.commit()

    job = _job_with_plan({"operations": [{"type": "optimize", "params": {"file_size_mb": 256}}]})
    job.table_id = table.id

    mock_txn = MagicMock()
    mock_txn.__enter__ = MagicMock(return_value=mock_txn)
    mock_txn.__exit__ = MagicMock(return_value=False)
    mock_iceberg_table = SimpleNamespace(transaction=lambda: mock_txn)
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_iceberg_table
    monkeypatch.setattr(
        "cryo.engines.native.optimize_properties.PyIcebergSourceCatalog",
        lambda name, config: mock_catalog,
    )

    result = maybe_persist_optimize_properties(session, job)

    assert result is True
    mock_catalog.load_table.assert_called_once_with("pw_bq", "silver_dbt_pw_launch")
    mock_txn.set_properties.assert_called_once_with(
        **{
            "write.target-file-size-bytes": str(256 * 1024 * 1024),
            "write.distribution-mode": "hash",
        }
    )


def test_returns_none_without_attempting_catalog_connection_when_no_optimize(
    session, monkeypatch
) -> None:
    job = _job_with_plan({"operations": [{"type": "expire_snapshots", "params": {}}]})
    monkeypatch.setattr(
        "cryo.engines.native.optimize_properties.PyIcebergSourceCatalog",
        MagicMock(side_effect=AssertionError("must not be called")),
    )

    assert maybe_persist_optimize_properties(session, job) is None


def test_catalog_failure_is_swallowed_and_returns_false(session, monkeypatch) -> None:
    """A property-persist failure is best-effort -- the rewrite already succeeded, so this
    must never raise or block job finalization."""
    table = Table(id=uuid4(), source_id=uuid4(), schema_name="pw_bq", table_name="t")
    source = Source(id=table.source_id, name="s", type="rest", config={"uri": "http://x"})
    session.add_all([table, source])
    session.commit()

    job = _job_with_plan({"operations": [{"type": "optimize", "params": {}}]})
    job.table_id = table.id
    monkeypatch.setattr(
        "cryo.engines.native.optimize_properties.PyIcebergSourceCatalog",
        MagicMock(side_effect=RuntimeError("catalog unreachable")),
    )

    result = maybe_persist_optimize_properties(session, job)

    assert result is False
