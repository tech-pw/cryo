"""Regression test for the connection-test timeout helper in cryo/api/routes.py.

Guards against a real bug: an earlier version used ThreadPoolExecutor as a context manager,
whose __exit__ calls shutdown(wait=True) -- that blocks on the still-running background thread
even after future.result(timeout=...) has already raised, defeating the entire point of the
timeout (a "Test connection" click against an unreachable host would still hang the request for
as long as the underlying blocking call takes, sometimes indefinitely).
"""

from __future__ import annotations

import threading
import time

from cryo.api.routes import _run_with_timeout


def test_run_with_timeout_returns_promptly_on_timeout() -> None:
    started_at = time.monotonic()

    def _blocks_forever() -> None:
        # Simulates PyIceberg's HiveCatalog opening a raw Thrift socket with no timeout of its
        # own against an unreachable host -- this never returns on its own within the test.
        time.sleep(30)

    try:
        _run_with_timeout(_blocks_forever, seconds=0.2)
        raise AssertionError("expected _run_with_timeout to raise TimeoutError")
    except TimeoutError as exc:
        elapsed = time.monotonic() - started_at
        assert elapsed < 2.0, f"took {elapsed:.2f}s to return -- shutdown(wait=True) regression?"
        assert "timed out after" in str(exc)


def test_run_with_timeout_returns_result_when_fast_enough() -> None:
    assert _run_with_timeout(lambda: 42, seconds=5.0) == 42


def test_run_with_timeout_does_not_block_process_exit() -> None:
    """The orphaned thread from a timed-out call must not be a non-daemon thread that would
    keep the whole process alive -- ThreadPoolExecutor threads are daemonized by default in
    modern Python, but this pins that assumption down explicitly."""
    before = {t.ident for t in threading.enumerate()}

    def _blocks_forever() -> None:
        time.sleep(30)

    try:
        _run_with_timeout(_blocks_forever, seconds=0.1)
    except TimeoutError:
        pass

    after = threading.enumerate()
    leaked = [t for t in after if t.ident not in before]
    assert all(t.daemon for t in leaked), "leaked thread(s) are not daemon threads"
