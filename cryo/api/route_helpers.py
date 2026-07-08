"""Shared internal helpers for the API routers in ``cryo/api/``.

The single ``cryo/api/routes.py`` file was 1,990 LoC and dominated the
remaining D1 (modularity) hotspot after wave 2 of ``docs/RATING_2026.md``.
The failed split attempt is documented there as a reverted
``routes_sources.py`` extraction; this module is the de-risked *first* move:

  * move what is already shared across the intended per-domain routers here,
  * keep public surface identical so the per-domain splits can follow
    without a behaviour diff to chase.

Currently houses:
  * ``_run_with_timeout`` — wall-clock timeout wrapper used by both source
    and destination connection probes (verified PyIceberg's HiveCatalog
    opens a raw Thrift TSocket with zero timeout — see the function
    docstring for the full bug history).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# Connection-test timeout: 120s for the explicit "Test connection" button
# where the user has asked to verify reachability and is willing to wait.
CONNECTION_TEST_TIMEOUT_SECONDS: float = 120.0

# Interactive picker timeout: the scan-scope dropdown opens while a human
# is waiting, so this has to fail fast (the picker silently falls back to
# scanned schemas on timeout/error). Keep well below the connection-test
# timeout above.
NAMESPACE_LIST_TIMEOUT_SECONDS: float = 10.0


def run_with_timeout(
    fn: Callable[[], Any],
    *,
    seconds: float = CONNECTION_TEST_TIMEOUT_SECONDS,
) -> Any:
    """Run a blocking call with a hard wall-clock timeout.

    Needed because some catalog/engine clients block indefinitely with no
    timeout of their own — verified PyIceberg's HiveCatalog opens a raw
    Thrift TSocket with zero timeout handling anywhere in its code, so an
    unreachable metastore host (wrong network, VPN required, firewalled)
    hangs the request forever instead of failing. The underlying worker
    thread can still be stuck after this returns (a blocking socket
    connect can't be cleanly interrupted from another thread), but the
    caller gets a bounded, honest "timed out" response instead of an
    infinite spinner.

    Uses a plain daemon Thread, not ThreadPoolExecutor: (1)
    ThreadPoolExecutor's worker threads are NOT daemon threads, so a
    leaked one (still blocked on the underlying call) would prevent a
    clean process shutdown — e.g. a k8s pod failing to terminate on
    SIGTERM during a rolling restart, exactly the kind of thing that's
    easy to miss until it happens in production. (2) ThreadPoolExecutor
    used as a context manager also calls shutdown(wait=True) on exit,
    which blocks on that same stuck thread and defeats the entire point
    of this function — an earlier version of this had exactly that bug,
    caught by tests/test_connection_test_timeout.py.

    Public alias ``run_with_timeout`` replaces the module-private
    ``_run_with_timeout`` that lived in cryo.api.routes for the duration
    of the failed split attempt documented in docs/RATING_2026.md §10.
    """
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result_box["value"] = fn()
        except BaseException as exc:
            error_box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=seconds)

    if thread.is_alive():
        raise TimeoutError(
            f"Connection attempt timed out after {seconds:.0f}s — check the host is "
            "reachable from the Cryo backend's network (VPN, firewall, DNS)."
        )
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")
