from __future__ import annotations

import time

import pytest

from cryo.scanner.run import _run_bounded


def test_run_bounded_returns_result_within_timeout() -> None:
    assert _run_bounded(lambda: 42, timeout_seconds=1.0, description="quick") == 42


def test_run_bounded_raises_timeout_instead_of_hanging() -> None:
    """The bug this guards against: a table scan blocked on an unresponsive
    catalog/S3 call for 100+ minutes in production, wedging the scanner daemon's
    single-threaded poll loop and starving every subsequently queued scan. The
    caller must get control back (a TimeoutError) well before the slow call
    finishes, not block until it does.
    """
    started = time.monotonic()

    def _slow() -> None:
        time.sleep(0.5)

    with pytest.raises(TimeoutError, match="slow table scan"):
        _run_bounded(_slow, timeout_seconds=0.05, description="slow table scan")

    elapsed = time.monotonic() - started
    assert elapsed < 0.4, f"caller blocked for {elapsed:.2f}s despite a 0.05s timeout bound"
