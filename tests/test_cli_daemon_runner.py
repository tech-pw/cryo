"""DaemonRunner.consecutive_tick_failures -- the readiness signal a stuck daemon needs.

Real incident, 2026-10 (twice): cryo-governor's Redis connection went stale after a
CoreDNS blip and every tick failed with the same error for the rest of the pod's life.
run() already caught and logged each exception, so the process stayed alive and
"not stopped" the whole time -- nothing distinguished a governor silently failing every
30s from a healthy one until a human noticed zero admissions and restarted the pod.
"""

from __future__ import annotations

from cryo.cli import DaemonRunner


class _FlakyRunner(DaemonRunner):
    def __init__(self, failures_then_success: list[bool]) -> None:
        super().__init__("flaky", interval=0)
        self._plan = list(failures_then_success)

    def _tick(self) -> None:
        if self._plan.pop(0):
            raise RuntimeError("stale connection")


def _run_n_ticks(runner: DaemonRunner, n: int) -> None:
    for _ in range(n):
        if runner._stop.is_set():
            break
        try:
            runner._tick()
            runner.consecutive_tick_failures = 0
        except Exception:
            runner.consecutive_tick_failures += 1


def test_starts_at_zero_failures() -> None:
    runner = _FlakyRunner([])
    assert runner.consecutive_tick_failures == 0


def test_counts_consecutive_failures() -> None:
    runner = _FlakyRunner([True, True, True])
    _run_n_ticks(runner, 3)
    assert runner.consecutive_tick_failures == 3


def test_a_success_resets_the_counter() -> None:
    runner = _FlakyRunner([True, True, False, True])
    _run_n_ticks(runner, 4)
    # Only the final tick (after the False reset it to 0) failed.
    assert runner.consecutive_tick_failures == 1


def test_run_itself_tracks_failures_without_stopping() -> None:
    """The real run() loop (not the test helper above) must also update the counter --
    this is what the readiness check in _run_with_signal actually reads from a live
    runner, so the tracking has to work through run()'s own try/except, not just in a
    hand-rolled test loop."""
    runner = _FlakyRunner([True, True])

    def _stop_after_two(timeout: float | None = None) -> bool:
        # Event.wait() is the real loop's only yield point between ticks.
        if not runner._plan:
            runner._stop.set()
        return False

    runner._stop.wait = _stop_after_two  # type: ignore[method-assign]
    runner.run()
    assert runner.consecutive_tick_failures == 2
