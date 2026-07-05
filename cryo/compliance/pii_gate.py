"""Block rewrite operations on tables with untagged likely-PII columns."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from cryo.ai.pii import classify_table_columns
from cryo.db.models import TableStats
from cryo.domain.enums import OperationType

_REWRITE_OPS = frozenset(
    {
        OperationType.OPTIMIZE.value,
        "optimize",
        "rewrite_data_files",
    }
)


def maintenance_blocked_for_untagged_pii(
    session: Session,
    table_id: uuid.UUID,
    plan_operations: list[dict[str, Any]] | None,
    *,
    job_params: Mapping[str, Any] | None = None,
) -> str | None:
    """Return a deferral reason when rewrite ops would touch untagged sensitive columns."""
    params = job_params or {}
    if params.get("force") or params.get("ignore_pii_gate"):
        return None

    if not _plan_has_rewrite(plan_operations):
        return None

    stats = session.get(TableStats, table_id)
    if stats is None or not stats.raw:
        return None

    raw = stats.raw if isinstance(stats.raw, dict) else {}
    columns = raw.get("column_quality") or []
    if not isinstance(columns, list):
        return None

    matches = classify_table_columns(columns)
    if not matches:
        return None

    columns_list = ", ".join(m.column_name for m in matches[:5])
    suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
    return (
        f"Untagged likely-PII columns detected ({columns_list}{suffix}). "
        "Tag columns or pass force/ignore_pii_gate to proceed."
    )


def _plan_has_rewrite(plan_operations: list[dict[str, Any]] | None) -> bool:
    if not plan_operations:
        return False
    for op in plan_operations:
        if not isinstance(op, dict):
            continue
        op_type = str(op.get("type") or op.get("operation") or "").lower()
        if op_type in _REWRITE_OPS:
            return True
    return False
