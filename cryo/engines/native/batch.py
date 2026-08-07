"""Serial multi-table native execute for shared K8s Job pods.

Reads a batch payload (list of per-table execute requests) and runs each
table serially via the Go engine subprocess, preserving per-table failure
isolation.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cryo.engines.native.runner import NativeExecuteResult, execute_request

logger = logging.getLogger(__name__)
TABLE_RESULT_PREFIX = "CRYO_TABLE_RESULT "


def build_batch_payload(
    table_requests: Sequence[Mapping[str, Any]],
    *,
    dispatch_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Wrap per-table execute requests into a batch payload for a shared pod."""
    if not table_requests:
        raise ValueError("batch payload requires at least one table request")
    return {
        "batch": True,
        "dispatch_id": dispatch_id,
        "run_id": run_id,
        "tables": [dict(req) for req in table_requests],
    }


def is_batch_payload(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("batch")) and isinstance(payload.get("tables"), list)


def execute_batch(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    timeout_seconds: int | None = None,
    on_table_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute each table request serially; return aggregate + per-table results."""
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return {
            "ok": False,
            "batch": True,
            "error": "batch payload missing tables",
            "results": [],
        }

    cfg = dict(config or {})
    per_table: list[dict[str, Any]] = []
    all_ok = True
    for index, request in enumerate(tables):
        if not isinstance(request, Mapping):
            per_table.append(
                {
                    "ok": False,
                    "index": index,
                    "error": "invalid table request",
                }
            )
            all_ok = False
            if on_table_result is not None:
                on_table_result(per_table[-1])
            continue
        job_id = request.get("job_id")
        table_id = request.get("table_id")
        table_ref = str(request.get("table_ref", "unknown"))
        try:
            result: NativeExecuteResult = execute_request(
                dict(request),
                config=cfg,
                timeout_seconds=timeout_seconds,
            )
            entry = {
                "ok": result.ok,
                "index": index,
                "job_id": job_id,
                "table_id": table_id,
                "table_ref": result.table_ref or table_ref,
                "error": result.error,
                "results": [
                    {
                        "operation": r.operation,
                        "success": r.success,
                        "error": r.error,
                        "input_files": r.input_files,
                        "output_files": r.output_files,
                        "files_rewritten": r.files_rewritten,
                        "bytes_removed": r.bytes_removed,
                        "bytes_written": r.bytes_written,
                        "snapshots_expired": r.snapshots_expired,
                        "manifests_rewritten": r.manifests_rewritten,
                        "duration_ms": r.duration_ms,
                    }
                    for r in result.results
                ],
            }
            if not result.ok:
                all_ok = False
            per_table.append(entry)
            if on_table_result is not None:
                on_table_result(entry)
        except Exception as exc:
            logger.exception("Batch table %s failed: %s", table_ref, exc)
            all_ok = False
            per_table.append(
                {
                    "ok": False,
                    "index": index,
                    "job_id": job_id,
                    "table_id": table_id,
                    "table_ref": table_ref,
                    "error": str(exc),
                    "results": [],
                }
            )
            if on_table_result is not None:
                on_table_result(per_table[-1])

    return {
        "ok": all_ok,
        "batch": True,
        "dispatch_id": payload.get("dispatch_id"),
        "run_id": payload.get("run_id"),
        "results": per_table,
        "error": None if all_ok else "one or more tables failed",
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry: decode the batch payload (file or env var) and run batch/single execute.

    Prefers CRYO_PAYLOAD_FILE (a mounted Secret volume) over CRYO_PAYLOAD_B64 (the env
    var) -- Linux caps any single argv/env string at MAX_ARG_STRLEN (128KiB), which a
    large table's payload (e.g. 200k+ files) exceeds as base64, failing before this
    process even starts ("exec .../python: argument list too long", confirmed live
    2026-08-07 on a 211,158-file table). A mounted file has no such limit. The env var
    is kept as a fallback for any caller still using the old delivery path.
    """
    import os

    raw_b64: str | None = None
    payload_file = os.environ.get("CRYO_PAYLOAD_FILE")
    if payload_file:
        try:
            with open(payload_file, encoding="utf-8") as f:
                raw_b64 = f.read().strip()
        except OSError as exc:
            err = f"cannot read CRYO_PAYLOAD_FILE: {exc}"
            print(json.dumps({"ok": False, "error": err}), flush=True)
            return 1
    if not raw_b64:
        raw_b64 = os.environ.get("CRYO_PAYLOAD_B64")
    if not raw_b64:
        err = "neither CRYO_PAYLOAD_FILE nor CRYO_PAYLOAD_B64 set"
        print(json.dumps({"ok": False, "error": err}), flush=True)
        return 1
    try:
        payload = json.loads(base64.b64decode(raw_b64).decode())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid payload: {exc}"}), flush=True)
        return 1

    config: dict[str, Any] = {}
    binary = os.environ.get("CRYO_ENGINE_BINARY")
    if binary:
        config["binary_path"] = binary

    batch_payload = is_batch_payload(payload)
    if batch_payload:
        result = execute_batch(
            payload,
            config=config,
            on_table_result=lambda entry: print(
                f"{TABLE_RESULT_PREFIX}{json.dumps(entry)}",
                flush=True,
            ),
        )
    else:
        # Single-table: still run via Python so Job command can be uniform.
        try:
            native = execute_request(payload, config=config)
            result = {
                "ok": native.ok,
                "batch": False,
                "table_ref": native.table_ref,
                "error": native.error,
                "results": [
                    {
                        "operation": r.operation,
                        "success": r.success,
                        "error": r.error,
                        "input_files": r.input_files,
                        "output_files": r.output_files,
                        "files_rewritten": r.files_rewritten,
                        "bytes_removed": r.bytes_removed,
                        "bytes_written": r.bytes_written,
                        "snapshots_expired": r.snapshots_expired,
                        "manifests_rewritten": r.manifests_rewritten,
                        "duration_ms": r.duration_ms,
                    }
                    for r in native.results
                ],
            }
        except Exception as exc:
            result = {"ok": False, "batch": False, "error": str(exc), "results": []}

    print(json.dumps(result), flush=True)
    # A batch Job is an execution envelope: individual table failures are
    # domain results consumed by NativeJobWatcher, not a pod-level failure.
    # Exiting zero preserves successful members instead of making Kubernetes
    # classify the entire shared pod as failed.
    return 0 if batch_payload or result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
