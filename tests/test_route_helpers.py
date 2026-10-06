"""Tests for ``cryo.api.route_helpers``.

The helper module was extracted from ``cryo.api.routes`` as the de-risked
*first* move of the routes.py decomposition (Move 22), per
``docs/RATING_2026.md`` §10. It owns the wall-clock timeout wrapper that
both source and destination probes depend on, so its behaviour must be
covered independently of the routers that import it.
"""

from __future__ import annotations

import threading
import time

import pytest

from cryo.api.route_helpers import (
    CONNECTION_TEST_TIMEOUT_SECONDS,
    NAMESPACE_LIST_TIMEOUT_SECONDS,
    run_with_timeout,
)


def test_returns_value_when_fn_completes_within_budget() -> None:
    assert run_with_timeout(lambda: 42, seconds=1.0) == 42


def test_string_return_value_round_trips() -> None:
    assert run_with_timeout(lambda: "ok", seconds=1.0) == "ok"


def test_raises_timeout_error_when_fn_exceeds_budget() -> None:
    def slow() -> int:
        time.sleep(0.5)
        return 1

    start = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out"):
        run_with_timeout(slow, seconds=0.05)
    elapsed = time.monotonic() - start
    assert elapsed < 0.4, "timeout should fire within the budget, not block on the worker"


def test_propagates_caller_exception() -> None:
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        run_with_timeout(boom, seconds=1.0)


def test_thread_is_daemon_so_it_doesnt_block_shutdown() -> None:
    # Sanity-check the daemon=True choice that the function comment calls out
    # as load-bearing for clean k8s rollouts (see route_helpers docstring).
    started: dict[str, threading.Thread] = {}

    def grab_thread() -> None:
        for t in threading.enumerate():
            started["t"] = t

    run_with_timeout(grab_thread, seconds=1.0)
    thread = started.get("t")
    assert thread is None or thread.daemon is True


def test_default_seconds_matches_connection_test_budget() -> None:
    # The default is wired to the connection-test button path; source/router
    # callers that pass an explicit shorter budget get the picker timeout.
    assert CONNECTION_TEST_TIMEOUT_SECONDS == 120.0
    assert NAMESPACE_LIST_TIMEOUT_SECONDS < CONNECTION_TEST_TIMEOUT_SECONDS
