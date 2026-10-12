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

# Substring match for engine-go's own connect-timeout error text (connectWithTimeout,
# third_party/iceberg-go-v0.6.0-patched/catalog/hive/client.go: "connect timed out after %s")
# as it surfaces through catalog.go's wrapping ("failed to connect to catalog: after N
# attempts: failed to create Hive client: failed to connect to metastore: connect timed out
# after 45s"). Deliberately narrow -- this must only match the specific bounded-timeout
# signature the 2026-08-28 fix produces, not every metastore-adjacent error string, so a
# genuinely broken/misconfigured table (bad credentials, malformed URI, table truly deleted)
# still fails fast instead of retrying pointlessly.
#
# Moved here (was cryo/engines/native/watcher.py) as part of the 2026-09-16 catalog circuit
# breaker (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md §1): watcher.py already imports
# TABLE_RESULT_PREFIX from this module, so batch.py (which needs this same signature to open
# the breaker) importing back from watcher.py would be a cycle -- batch.py is the more
# upstream module (it's where the subprocess spawn this signature describes actually happens),
# so the constant lives here and watcher.py imports it from here instead.
TRANSIENT_METASTORE_ERROR_SUBSTRING = "connect timed out after"


def is_transient_metastore_error(error: str | None) -> bool:
    if not error:
        return False
    return TRANSIENT_METASTORE_ERROR_SUBSTRING in error


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


# Cost remediation plan §1, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): a real
# incident found a native pod holding ~11.6GiB reserved for ~48+ minutes (well before its
# 4h active deadline) hitting the same unreachable metastore across 32 serial tables, each
# paying its own bounded connect-timeout (2 operations x 45s per table observed) before moving
# on -- the whole batch already knew by table 2 that this catalog was down, but kept spending
# real wall-clock/reserved-capacity time re-discovering that fact per table all the way to
# table 32. This breaker opens per catalog_uri after _CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
# consecutive transient-metastore-timeout failures against that same URI within one batch
# execution, and every remaining member sharing that URI is short-circuited to a retryable
# result (same TRANSIENT_METASTORE_ERROR_SUBSTRING signature watcher.py's _finalize_success
# already classifies as DEFERRED, not FAILED -- see _is_transient_metastore_error there) instead
# of spawning a doomed subprocess. Scoped to one batch execution (a dict, not any persistent
# store) since that's the exact blast radius of the incident: one pod, one shared-nothing
# process lifetime, no cross-pod coordination needed or wanted here.
_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES = 2


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
    # uri -> consecutive transient-metastore-timeout failures seen so far this batch. Reset to
    # 0 the instant any member against that same uri succeeds or fails for a different reason
    # -- this only ever opens on a genuine run of the same failure signature against the same
    # catalog, never on an unrelated per-table error.
    consecutive_metastore_failures: dict[str, int] = {}
    open_breaker_uris: set[str] = set()
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
        catalog_uri = request.get("catalog_uri")
        if isinstance(catalog_uri, str) and catalog_uri in open_breaker_uris:
            entry: dict[str, Any] = {
                "ok": False,
                "index": index,
                "job_id": job_id,
                "table_id": table_id,
                "table_ref": table_ref,
                "error": (
                    f"catalog_circuit_breaker_open: {TRANSIENT_METASTORE_ERROR_SUBSTRING} "
                    f"(repeated for {catalog_uri}, skipping remaining batch members against "
                    "this catalog rather than re-discovering the same outage per table)"
                ),
                "results": [],
            }
            all_ok = False
            per_table.append(entry)
            if on_table_result is not None:
                on_table_result(entry)
            continue
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
            if isinstance(catalog_uri, str):
                if not result.ok and is_transient_metastore_error(result.error):
                    consecutive_metastore_failures[catalog_uri] = (
                        consecutive_metastore_failures.get(catalog_uri, 0) + 1
                    )
                    if (
                        consecutive_metastore_failures[catalog_uri]
                        >= _CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
                    ):
                        open_breaker_uris.add(catalog_uri)
                        logger.warning(
                            "Catalog circuit breaker opened for %s after %d consecutive "
                            "connect-timeout failures -- skipping remaining batch members "
                            "against this catalog",
                            catalog_uri,
                            consecutive_metastore_failures[catalog_uri],
                        )
                else:
                    consecutive_metastore_failures[catalog_uri] = 0
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
            if isinstance(catalog_uri, str):
                consecutive_metastore_failures[catalog_uri] = 0

    return {
        "ok": all_ok,
        "batch": True,
        "dispatch_id": payload.get("dispatch_id"),
        "run_id": payload.get("run_id"),
        "results": per_table,
        "error": None if all_ok else "one or more tables failed",
        "member_count": len(tables),
        "peak_cgroup_memory_mib": read_peak_cgroup_memory_mib(),
    }


# Cost remediation plan §3, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): the serial
# batch memory allowance below (_SERIAL_MEMBER_OVERHEAD_MIB in packing.py) is a heuristic from
# a prior OOM incident, explicitly flagged in its own comment as "pending real cgroup-memory
# telemetry from a live batch pod -- revisit the exact number once that exists." No such
# telemetry existed anywhere in this codebase before this fix. This reads this pod's own
# cgroup v2 peak memory usage (memory.peak, the kernel-tracked high-water-mark for this
# cgroup's lifetime -- exactly the pod-level real-usage signal the packing heuristic is
# guessing at) so it travels with the batch's own result JSON, where NativeJobWatcher already
# parses aggregate fields and can log/record it. Cgroup v1 (memory.max_usage_in_bytes) is a
# fallback for older node AMIs; returns None (not 0, which would look like "measured zero
# usage") when neither file is readable, since a batch that ran on cgroup v1 without the v1
# path, or outside any cgroup at all (e.g. local dev), genuinely has no measurement to report.
def read_peak_cgroup_memory_mib() -> int | None:
    for path in (
        "/sys/fs/cgroup/memory.peak",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.max_usage_in_bytes",  # cgroup v1
    ):
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read().strip()
            return int(raw) // (1024 * 1024)
        except (OSError, ValueError):
            continue
    return None


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
