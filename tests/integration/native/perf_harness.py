"""Performance helpers for native compaction e2e (§8.6 + throughput loop)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cryo.engines.native.runner import (
    PROGRESS_PREFIX,
    NativeExecuteResult,
    _first_error,
    _parse_operation_result,
    split_stdout_lines,
)

# §8.6 P4: streaming compaction must not materialize whole partitions in memory.
P4_MAX_RSS_BYTES = 1 * 1024 * 1024 * 1024

# Throughput target for a 2–3 GB dense table on local MinIO.
THROUGHPUT_TARGET_SECONDS = 20 * 60


@dataclass
class ProgressEvent:
    event: str
    operation: str
    progress_pct: int | None = None
    files_processed: int | None = None
    bytes_processed: int | None = None
    message: str | None = None
    group_index: int | None = None
    total_groups: int | None = None
    committed_groups: int | None = None
    ts_monotonic: float | None = None


@dataclass
class PerfRunResult:
    """Wall-time + RSS + throughput metrics for one cryo-engine-go execute."""

    ok: bool
    table_ref: str
    wall_seconds: float
    peak_rss_bytes: int
    input_bytes: int
    output_bytes: int
    input_files: int
    output_files: int
    mb_per_second: float
    op_durations_ms: dict[str, int] = field(default_factory=dict)
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    label: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def peak_rss_bytes(pid: int) -> int:
    """Return current RSS for ``pid`` in bytes (macOS ``ps`` or Linux ``/proc``)."""
    try:
        proc_status = Path(f"/proc/{pid}/status")
        if proc_status.is_file():
            for line in proc_status.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return 0
        kb = int((completed.stdout or "").strip() or "0")
        return kb * 1024
    except (OSError, ValueError):
        return 0


def parse_progress_events(stdout: str) -> list[ProgressEvent]:
    events: list[ProgressEvent] = []
    for line in (stdout or "").splitlines():
        if not line.startswith(PROGRESS_PREFIX):
            continue
        raw = line[len(PROGRESS_PREFIX) :]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        events.append(
            ProgressEvent(
                event=str(payload.get("event") or ""),
                operation=str(payload.get("operation") or ""),
                progress_pct=payload.get("progress_pct")
                if isinstance(payload.get("progress_pct"), int)
                else None,
                files_processed=payload.get("files_processed")
                if isinstance(payload.get("files_processed"), int)
                else None,
                bytes_processed=payload.get("bytes_processed")
                if isinstance(payload.get("bytes_processed"), int)
                else None,
                message=payload.get("message") if isinstance(payload.get("message"), str) else None,
                group_index=payload.get("group_index")
                if isinstance(payload.get("group_index"), int)
                else None,
                total_groups=payload.get("total_groups")
                if isinstance(payload.get("total_groups"), int)
                else None,
                committed_groups=payload.get("committed_groups")
                if isinstance(payload.get("committed_groups"), int)
                else None,
            )
        )
    return events


def execute_request_with_peak_rss(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[NativeExecuteResult, int]:
    """Run cryo-engine-go and return (result, peak_rss_bytes)."""
    result, metrics = execute_request_with_metrics(
        request, config=config, timeout_seconds=timeout_seconds
    )
    return result, metrics.peak_rss_bytes


def execute_request_with_metrics(
    request: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    timeout_seconds: int | None = None,
    label: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[NativeExecuteResult, PerfRunResult]:
    """Run cryo-engine-go and return (result, PerfRunResult with wall/RSS/MB/s)."""
    from cryo.engines.native.runner import resolve_binary_path

    binary = resolve_binary_path(dict(config or {}))
    if binary is None:
        raise RuntimeError("cryo-engine binary not found")

    payload = json.dumps(request, default=str)
    run_env = os.environ.copy()
    if env:
        run_env.update({k: str(v) for k, v in env.items()})
    proc = subprocess.Popen(
        [binary, "execute"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
    )

    peak_rss = 0
    stop = threading.Event()

    def _poll_rss() -> None:
        nonlocal peak_rss
        while not stop.is_set():
            if proc.poll() is not None:
                return
            if proc.pid:
                peak_rss = max(peak_rss, peak_rss_bytes(proc.pid))
            time.sleep(0.1)

    poll_thread = threading.Thread(target=_poll_rss, daemon=True)
    poll_thread.start()
    started = time.perf_counter()
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_seconds)
    finally:
        stop.set()
        poll_thread.join(timeout=1)
        if proc.pid:
            peak_rss = max(peak_rss, peak_rss_bytes(proc.pid))
    wall_seconds = time.perf_counter() - started

    stderr_text = (stderr or "").strip() or None
    returncode = proc.returncode or 0
    progress_events = parse_progress_events(stdout or "")
    _, response_lines = split_stdout_lines(stdout or "")
    if not response_lines:
        from cryo.engines.native.runner import _empty_stdout_error

        native = NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error=_empty_stdout_error(returncode, stderr_text),
            stdout=stdout,
            stderr=stderr_text,
        )
        return native, _metrics_from_native(
            native, wall_seconds, peak_rss, progress_events, request, label
        )
    try:
        parsed = json.loads(response_lines[-1])
    except json.JSONDecodeError as exc:
        native = NativeExecuteResult(
            ok=False,
            table_ref=str(request.get("table_ref", "")),
            results=(),
            error=f"invalid cryo-engine stdout JSON: {exc}",
            stdout=stdout,
            stderr=stderr_text,
        )
        return native, _metrics_from_native(
            native, wall_seconds, peak_rss, progress_events, request, label
        )
    results = tuple(_parse_operation_result(item, 0) for item in parsed.get("results", []))
    ok = bool(parsed.get("ok", False)) and returncode == 0
    native = NativeExecuteResult(
        ok=ok,
        table_ref=str(parsed.get("table_ref", request.get("table_ref", ""))),
        results=results,
        error=None if ok else str(parsed.get("error") or _first_error(results)),
        stdout=stdout,
        stderr=stderr_text,
    )
    return native, _metrics_from_native(
        native, wall_seconds, peak_rss, progress_events, request, label
    )


def _metrics_from_native(
    native: NativeExecuteResult,
    wall_seconds: float,
    peak_rss: int,
    progress_events: Sequence[ProgressEvent],
    request: Mapping[str, Any],
    label: str | None,
) -> PerfRunResult:
    input_bytes = 0
    output_bytes = 0
    input_files = 0
    output_files = 0
    op_durations: dict[str, int] = {}
    for op in native.results:
        input_bytes += int(op.bytes_removed or 0)
        output_bytes += int(getattr(op, "bytes_written", None) or 0)
        input_files += int(op.input_files or 0)
        output_files += int(op.output_files or 0)
        if op.duration_ms:
            op_durations[op.operation] = int(op.duration_ms)
    # Prefer progress-line candidate bytes when op metrics are empty (plan-only).
    if input_bytes == 0:
        for ev in progress_events:
            if ev.event == "progress" and ev.bytes_processed:
                input_bytes = max(input_bytes, int(ev.bytes_processed))
    mb_s = 0.0
    if wall_seconds > 0 and input_bytes > 0:
        mb_s = (input_bytes / (1024 * 1024)) / wall_seconds

    params: dict[str, Any] = {}
    ops = request.get("operations")
    if isinstance(ops, list) and ops and isinstance(ops[0], dict):
        raw_params = ops[0].get("params")
        if isinstance(raw_params, dict):
            params = dict(raw_params)

    return PerfRunResult(
        ok=native.ok,
        table_ref=native.table_ref,
        wall_seconds=wall_seconds,
        peak_rss_bytes=peak_rss,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        input_files=input_files,
        output_files=output_files,
        mb_per_second=mb_s,
        op_durations_ms=op_durations,
        progress_events=[asdict(ev) for ev in progress_events],
        error=native.error,
        label=label,
        params=params,
    )


def append_perf_result(
    result: PerfRunResult,
    *,
    results_dir: str | Path = "tmp/native-compaction-perf",
) -> Path:
    """Append one JSONL row and refresh a CSV summary for the optimization loop."""
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "results.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_dict(), default=str) + "\n")

    csv_path = out_dir / "results.csv"
    headers = [
        "label",
        "ok",
        "table_ref",
        "wall_seconds",
        "peak_rss_mib",
        "input_bytes",
        "output_bytes",
        "input_files",
        "output_files",
        "mb_per_second",
        "error",
    ]
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8") as handle:
        if write_header:
            handle.write(",".join(headers) + "\n")
        row = [
            result.label or "",
            str(result.ok),
            result.table_ref,
            f"{result.wall_seconds:.3f}",
            str(result.peak_rss_bytes // (1024 * 1024)),
            str(result.input_bytes),
            str(result.output_bytes),
            str(result.input_files),
            str(result.output_files),
            f"{result.mb_per_second:.3f}",
            (result.error or "").replace(",", ";"),
        ]
        handle.write(",".join(row) + "\n")
    return jsonl_path
