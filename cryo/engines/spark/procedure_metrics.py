"""Turn an Iceberg procedure result into bytes freed.

A missing number stays missing. A measured zero is only returned when the
procedure itself said nothing was removed.
"""

from __future__ import annotations

import json
from typing import Any

RESULT_PREFIX = "[cryo-result] "

_EXPIRE_COUNT_KEYS = (
    "deleted_data_files_count",
    "deleted_position_delete_files_count",
    "deleted_equality_delete_files_count",
    "deleted_manifest_files_count",
    "deleted_manifest_lists_count",
)


def bytes_freed_from_procedure(action: str, metrics: dict[str, Any] | None) -> int | None:
    metrics = metrics or {}
    if action == "remove_orphan_files":
        return _optional_int(metrics.get("bytes_removed"))
    if action == "rewrite_position_deletes":
        rewritten = _optional_int(metrics.get("rewritten_bytes_count"))
        added = _optional_int(metrics.get("added_bytes_count"))
        if rewritten is None or added is None:
            return None
        return max(rewritten - added, 0)
    if action == "expire_snapshots":
        counts = [_optional_int(metrics.get(key)) for key in _EXPIRE_COUNT_KEYS]
        if any(count is None for count in counts):
            return None
        if all(count == 0 for count in counts):
            return 0
        # Iceberg's expire procedure returns file counts, not sizes. Do not invent bytes.
        return None
    if action == "optimize":
        return _optional_int(metrics.get("rewritten_bytes_count"))
    return None


def action_from_sql(sql: str) -> str | None:
    lowered = sql.lower()
    if "remove_orphan_files" in lowered:
        return "remove_orphan_files"
    if "rewrite_position_delete_files" in lowered:
        return "rewrite_position_deletes"
    if "expire_snapshots" in lowered:
        return "expire_snapshots"
    if "rewrite_data_files" in lowered:
        return "optimize"
    return None


def with_orphan_dry_run(sql: str) -> str:
    if "dry_run" in sql.lower():
        return sql
    stripped = sql.rstrip()
    if not stripped.endswith(")"):
        return sql
    return stripped[:-1] + ", dry_run => true)"


def bytes_removed_from_log(text: str | None) -> int | None:
    if not text:
        return None
    found: int | None = None
    for line in text.splitlines():
        marker = line.find(RESULT_PREFIX)
        if marker < 0:
            continue
        try:
            payload = json.loads(line[marker + len(RESULT_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if payload.get("ok") and payload.get("bytes_removed") is not None:
            found = int(payload["bytes_removed"])
    return found


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
