"""Subprocess runner for the cryo-engine binary."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BINARY_NAMES = ("cryo-engine",)
PROGRESS_PREFIX = "CRYO_PROGRESS:"
_PROGRESS_EVENTS = frozenset({"started", "progress", "completed", "failed"})


@dataclass(frozen=True)
class NativeOperationResult:
    operation: str
    success: bool
    files_rewritten: int | None = None
    input_files: int | None = None
    output_files: int | None = None
    bytes_removed: int | None = None
    bytes_written: int | None = None
    snapshots_expired: int | None = None
    manifests_rewritten: int | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class NativeExecuteResult:
    ok: bool
    table_ref: str
    results: tuple[NativeOperationResult, ...]
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None


def resolve_binary_path(config: Mapping[str, Any]) -> str | None:
    configured = config.get("binary_path")
    if isinstance(configured, str) and configured.strip():
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
    for name in DEFAULT_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    repo_root = Path(__file__).resolve().parents[3]
    repo_candidate = repo_root / "cryo-engine" / "target" / "release" / "cryo-engine"
    if repo_candidate.is_file():
        return str(repo_candidate)
    debug_candidate = repo_candidate.parent.parent / "debug" / "cryo-engine"
    if debug_candidate.is_file():
        return str(debug_candidate)
    return None


def is_cryo_engine_available(config: Mapping[str, Any] | None = None) -> bool:
    return resolve_binary_path(config or {}) is not None


def verify_cryo_engine_spawnable(config: Mapping[str, Any] | None = None) -> str | None:
    """Return an error message when cryo-engine cannot be spawned, else None."""
    binary = resolve_binary_path(config or {})
    if binary is None:
        return "cryo-engine binary not found (set destination.config.binary_path)"
    try:
        proc = subprocess.run(
            [binary],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        return f"failed to spawn cryo-engine: {exc}"
    except subprocess.TimeoutExpired:
        return "cryo-engine did not respond"
    if proc.returncode == 2:
        return None
    return None if proc.returncode in {0, 1, 2} else (
        f"cryo-engine returned unexpected exit code {proc.returncode}"
    )


def is_progress_event(event: Mapping[str, Any]) -> bool:
    event_name = event.get("event")
    operation = event.get("operation")
    return (
        isinstance(event_name, str)
        and event_name in _PROGRESS_EVENTS
        and isinstance(operation, str)
        and bool(operation)
    )


def split_stdout_lines(stdout: str) -> tuple[list[str], list[str]]:
    """Split stdout into progress JSON payloads and response JSON lines."""
    progress_lines: list[str] = []
    response_lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith(PROGRESS_PREFIX):
            progress_lines.append(line[len(PROGRESS_PREFIX) :])
        elif line.strip():
            response_lines.append(line)
    return progress_lines, response_lines


def execute_request(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: int | None = None,
) -> NativeExecuteResult:
    binary = resolve_binary_path(config)
    if binary is None:
        return NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error="cryo-engine binary not found (set destination.config.binary_path)",
        )

    payload = json.dumps(dict(request))
    started = time.monotonic()
    if on_progress is not None:
        stdout_text, stderr_text, returncode = _run_with_progress(
            binary,
            payload,
            on_progress=on_progress,
            timeout_seconds=timeout_seconds,
        )
    else:
        try:
            proc = subprocess.run(
                [binary, "execute"],
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return NativeExecuteResult(
                ok=False,
                table_ref=str(request.get("table_ref", "")),
                results=(),
                error="cryo-engine timed out",
            )
        except OSError as exc:
            return NativeExecuteResult(
                ok=False,
                table_ref=str(request.get("table_ref", "")),
                results=(),
                error=f"failed to spawn cryo-engine: {exc}",
            )
        stdout_text = proc.stdout or ""
        stderr_text = (proc.stderr or "").strip() or None
        returncode = proc.returncode

    duration_ms = int((time.monotonic() - started) * 1000)

    if returncode != 0 and not stdout_text.strip():
        return NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error=stderr_text or f"cryo-engine exited with code {returncode}",
            stderr=stderr_text,
        )

    _, response_lines = split_stdout_lines(stdout_text)
    if not response_lines:
        return NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error="cryo-engine produced no JSON response on stdout",
            stdout=stdout_text,
            stderr=stderr_text,
        )

    try:
        parsed = json.loads(response_lines[-1])
    except json.JSONDecodeError as exc:
        return NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error=f"invalid cryo-engine stdout JSON: {exc}",
            stdout=stdout_text,
            stderr=stderr_text,
        )

    results = tuple(
        _parse_operation_result(item, duration_ms) for item in parsed.get("results", [])
    )
    ok = bool(parsed.get("ok", False)) and returncode == 0
    return NativeExecuteResult(
        ok=ok,
        table_ref=str(parsed.get("table_ref", request.get("table_ref", ""))),
        results=results,
        error=None if ok else str(parsed.get("error") or _first_error(results)),
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _run_with_progress(
    binary: str,
    payload: str,
    *,
    on_progress: Callable[[dict[str, Any]], None],
    timeout_seconds: int | None,
) -> tuple[str, str | None, int]:
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

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            if line.startswith(PROGRESS_PREFIX):
                _emit_progress_payload(line[len(PROGRESS_PREFIX) :].strip(), on_progress)

    def _read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        returncode = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    stderr_text = "".join(stderr_chunks).strip() or None
    return "".join(stdout_chunks), stderr_text, returncode


def _emit_progress_from_stdout(
    stdout: str,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> None:
    progress_lines, _ = split_stdout_lines(stdout)
    for payload in progress_lines:
        _emit_progress_payload(payload, on_progress)


def _emit_progress_payload(
    payload: str,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> None:
    if on_progress is None or not payload:
        return
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return
    if isinstance(event, dict) and is_progress_event(event):
        on_progress(event)


def _parse_operation_result(item: Any, fallback_duration_ms: int) -> NativeOperationResult:
    if not isinstance(item, dict):
        return NativeOperationResult(
            operation="unknown",
            success=False,
            error="invalid operation result payload",
            duration_ms=fallback_duration_ms,
        )
    input_files = _optional_int(item.get("input_files"))
    output_files = _optional_int(item.get("output_files"))
    files_rewritten = _optional_int(item.get("files_rewritten"))
    if input_files is None:
        input_files = files_rewritten
    if output_files is None:
        output_files = files_rewritten
    return NativeOperationResult(
        operation=str(item.get("operation", "unknown")),
        success=bool(item.get("success", False)),
        files_rewritten=files_rewritten,
        input_files=input_files,
        output_files=output_files,
        bytes_removed=_optional_int(item.get("bytes_removed")),
        bytes_written=_optional_int(item.get("bytes_written")),
        snapshots_expired=_optional_int(item.get("snapshots_expired")),
        manifests_rewritten=_optional_int(item.get("manifests_rewritten")),
        error=item.get("error") if isinstance(item.get("error"), str) else None,
        duration_ms=int(item.get("duration_ms") or fallback_duration_ms),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_error(results: tuple[NativeOperationResult, ...]) -> str | None:
    for result in results:
        if not result.success and result.error:
            return result.error
    for result in results:
        if not result.success:
            return f"{result.operation} failed"
    return "cryo-engine execution failed"
