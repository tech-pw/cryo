"""Quiet-window gates for metadata-dangerous Iceberg ops."""

from __future__ import annotations

from cryo.domain.enums import OperationType
from cryo.worker.safety_gates import TableActivity, requires_quiet_window, wait_for_table_quiet


def test_dangerous_ops_require_quiet_window() -> None:
    assert requires_quiet_window(OperationType.EXPIRE_SNAPSHOTS)
    assert requires_quiet_window(OperationType.REMOVE_ORPHAN_FILES)
    assert requires_quiet_window(OperationType.OPTIMIZE_MANIFESTS)
    assert not requires_quiet_window(OperationType.OPTIMIZE)


def test_quiet_window_needs_consecutive_probes() -> None:
    activities = iter([TableActivity.ACTIVE, TableActivity.QUIET, TableActivity.QUIET])
    assert wait_for_table_quiet(
        lambda: next(activities),
        operation=OperationType.EXPIRE_SNAPSHOTS,
        required_probes=2,
        interval_sec=0,
        max_wait_sec=10,
        sleep_fn=lambda _s: None,
    )


def test_uncertain_blocks_dangerous_ops() -> None:
    n = {"calls": 0}

    def probe() -> TableActivity:
        n["calls"] += 1
        return TableActivity.UNCERTAIN

    assert not wait_for_table_quiet(
        probe,
        operation=OperationType.REMOVE_ORPHAN_FILES,
        required_probes=2,
        interval_sec=0,
        max_wait_sec=0.01,
        sleep_fn=lambda _s: None,
    )
    assert n["calls"] >= 1
