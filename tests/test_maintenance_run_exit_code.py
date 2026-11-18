"""Exit code of ``python -m cryo.maintenance.run``: a failed run must fail its Kubernetes Job."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from cryo.config import Settings
from cryo.maintenance import run as run_module
from cryo.maintenance.coordinator import RunReport
from cryo.maintenance.run import exit_code

NOW = datetime(2026, 11, 18, 19, 0, tzinfo=UTC)


def _report(**counts: int) -> RunReport:
    report = RunReport(run_id=None, trigger="batch-cron", dry_run=False, started_at=NOW)
    for name, value in counts.items():
        setattr(report, name, value)
    return report


@pytest.mark.parametrize(
    ("counts", "fail_on_partial", "expected"),
    [
        pytest.param({"selected": 3, "succeeded": 3}, False, 0, id="succeeded"),
        pytest.param({"selected": 39, "failed": 39}, False, 1, id="every-table-failed"),
        pytest.param({"selected": 2, "failed": 1, "skipped": 1}, False, 1, id="failed-and-skipped"),
        pytest.param({"selected": 2, "planning_failed": 2}, False, 1, id="every-plan-failed"),
        pytest.param({"selected": 2, "succeeded": 1, "failed": 1}, False, 0, id="partial-default"),
        pytest.param({"selected": 2, "succeeded": 1, "failed": 1}, True, 1, id="partial-opt-in"),
        pytest.param({"selected": 1, "partial": 1}, False, 0, id="table-partial-default"),
        pytest.param({"selected": 1, "partial": 1}, True, 1, id="table-partial-opt-in"),
        pytest.param({"selected": 2, "succeeded": 1, "skipped": 1}, True, 0, id="some-skipped"),
        pytest.param({"selected": 4, "skipped": 4}, True, 0, id="all-skipped"),
        pytest.param({"selected": 4, "nothing_to_do": 4}, True, 0, id="nothing-to-do"),
        pytest.param({}, True, 0, id="empty-or-paused"),
    ],
)
def test_exit_code_per_run_outcome(
    counts: dict[str, int], fail_on_partial: bool, expected: int
) -> None:
    assert exit_code(_report(**counts), fail_on_partial=fail_on_partial) == expected


class _FakeCoordinator:
    report: RunReport

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def run(self, **_kwargs: Any) -> RunReport:
        return self.report


@pytest.mark.parametrize(
    ("counts", "fail_on_partial", "expected"),
    [
        ({"selected": 39, "failed": 39}, False, 1),
        ({"selected": 2, "succeeded": 1, "failed": 1}, False, 0),
        ({"selected": 2, "succeeded": 1, "failed": 1}, True, 1),
        ({"selected": 4, "skipped": 4}, False, 0),
    ],
)
def test_main_returns_the_exit_code_and_still_prints_the_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    counts: dict[str, int],
    fail_on_partial: bool,
    expected: int,
) -> None:
    _FakeCoordinator.report = _report(**counts)
    monkeypatch.setattr(run_module, "BatchMaintenance", _FakeCoordinator)
    monkeypatch.setattr(run_module, "configure_process_logging", lambda: None)
    monkeypatch.setattr(run_module.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        run_module,
        "get_settings",
        lambda: Settings(maintenance_fail_on_partial=fail_on_partial),
    )

    assert run_module.main(["--dry-run"]) == expected

    summary = json.loads(capsys.readouterr().out)
    assert summary["failed"] == counts.get("failed", 0)
    assert summary["selected"] == counts["selected"]
