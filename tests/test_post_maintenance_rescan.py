"""A successful on-demand job queues a stats scan so after-stats appear right away."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cryo.worker.worker import Worker


def _worker(request_rescan):
    return Worker(queue=MagicMock(), store=MagicMock(), request_rescan=request_rescan)


@pytest.mark.parametrize("trigger", ["manual", "api", "continuous:writes", None])
def test_on_demand_success_requests_a_rescan(trigger) -> None:
    calls: list[str] = []
    _worker(lambda table_id: calls.append(table_id) or True)._rescan_after_success(
        SimpleNamespace(table_id="t-1", run_trigger=trigger)
    )
    assert calls == ["t-1"]


@pytest.mark.parametrize("trigger", ["cron", "nightly", "batch:nightly", "scheduled"])
def test_batch_runs_leave_the_rescan_to_the_coordinator(trigger) -> None:
    calls: list[str] = []
    _worker(lambda table_id: calls.append(table_id) or True)._rescan_after_success(
        SimpleNamespace(table_id="t-1", run_trigger=trigger)
    )
    assert calls == []


def test_a_failed_request_does_not_raise() -> None:
    def boom(_table_id: str) -> bool:
        raise ConnectionError("redis down")

    _worker(boom)._rescan_after_success(SimpleNamespace(table_id="t-1", run_trigger="manual"))
