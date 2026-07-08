"""Performance helpers for native compaction e2e (§8.6 P2/P4/P5)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Mapping
from typing import Any

from cryo.engines.native.runner import (
    NativeExecuteResult,
    _first_error,
    _parse_operation_result,
    split_stdout_lines,
)

# §8.6 P4: streaming compaction must not materialize whole partitions in memory.
P4_MAX_RSS_BYTES = 1 * 1024 * 1024 * 1024


def peak_rss_bytes(pid: int) -> int:
    """Return peak RSS for ``pid`` in bytes (macOS ``ps`` or Linux ``/proc``)."""
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
        )
        kb = int(completed.stdout.strip() or "0")
        return kb * 1024
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0


def execute_request_with_peak_rss(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[NativeExecuteResult, int]:
    """Run cryo-engine-go and return (result, peak_rss_bytes)."""
    from cryo.engines.native.runner import resolve_binary_path

    binary = resolve_binary_path(dict(config or {}))
    if binary is None:
        raise RuntimeError("cryo-engine binary not found")

    payload = json.dumps(request, default=str)
    proc = subprocess.Popen(
        [binary, "execute"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(payload)
    proc.stdin.close()

    peak_rss = 0
    stop = threading.Event()

    def _poll_rss() -> None:
        nonlocal peak_rss
        while not stop.is_set():
            if proc.poll() is not None:
                return
            peak_rss = max(peak_rss, peak_rss_bytes(proc.pid))
            time.sleep(0.1)

    poll_thread = threading.Thread(target=_poll_rss, daemon=True)
    poll_thread.start()
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    finally:
        stop.set()
        poll_thread.join(timeout=1)
        peak_rss = max(peak_rss, peak_rss_bytes(proc.pid))

    stderr_text = (stderr or "").strip() or None
    returncode = proc.returncode or 0
    _, response_lines = split_stdout_lines(stdout or "")
    if not response_lines:
        return (
            NativeExecuteResult(
                ok=False,
                table_ref=str(request.get("table_ref", "")),
                results=(),
                error="cryo-engine produced no JSON response on stdout",
                stdout=stdout,
                stderr=stderr_text,
            ),
            peak_rss,
        )
    try:
        parsed = json.loads(response_lines[-1])
    except json.JSONDecodeError as exc:
        return (
            NativeExecuteResult(
                ok=False,
                table_ref=str(request.get("table_ref", "")),
                results=(),
                error=f"invalid cryo-engine stdout JSON: {exc}",
                stdout=stdout,
                stderr=stderr_text,
            ),
            peak_rss,
        )
    results = tuple(
        _parse_operation_result(item, 0) for item in parsed.get("results", [])
    )
    ok = bool(parsed.get("ok", False)) and returncode == 0
    return (
        NativeExecuteResult(
            ok=ok,
            table_ref=str(parsed.get("table_ref", request.get("table_ref", ""))),
            results=results,
            error=None if ok else str(parsed.get("error") or _first_error(results)),
            stdout=stdout,
            stderr=stderr_text,
        ),
        peak_rss,
    )
