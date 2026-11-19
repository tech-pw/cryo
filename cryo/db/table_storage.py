"""Per-table storage categories, shared by the Tables list and the Home storage breakdown.

Snapshot bytes are files only older snapshots reference: retained history (inside the
retention window, from an S3 listing) plus dead bytes (expirable now). Junk is everything
that is neither live data nor table metadata: snapshot bytes plus orphans.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, case, func

from cryo.db.models import Table, TableCost


def listed_expr() -> ColumnElement[bool]:
    return TableCost.listed_bytes.isnot(None)


def retained_history_expr() -> ColumnElement[Any]:
    accounted = (
        TableCost.live_bytes
        + TableCost.dead_bytes
        + TableCost.metadata_bytes
        + func.coalesce(TableCost.orphan_bytes, 0)
    )
    return case(
        (listed_expr() & (TableCost.listed_bytes > accounted), TableCost.listed_bytes - accounted),
        else_=0,
    )


def orphan_bytes_expr() -> ColumnElement[Any]:
    """Null when no S3 listing ran: unmeasured is not zero."""
    return case((listed_expr(), func.coalesce(TableCost.orphan_bytes, 0)), else_=None)


def snapshot_bytes_expr() -> ColumnElement[Any]:
    return TableCost.dead_bytes + retained_history_expr()


def junk_bytes_expr() -> ColumnElement[Any]:
    return snapshot_bytes_expr() + func.coalesce(orphan_bytes_expr(), 0)


def bucket_expr() -> ColumnElement[Any]:
    """Bucket of ``s3://bucket/...`` / ``s3a://bucket/...``; null for other locations."""
    return case(
        (Table.location.like("%://%"), func.nullif(func.split_part(Table.location, "/", 3), "")),
        else_=None,
    )


def bucket_of(location: str | None) -> str | None:
    if not location or "://" not in location:
        return None
    return location.split("://", 1)[1].split("/", 1)[0] or None


def storage_fields(cost: TableCost | None) -> dict[str, int | None]:
    """Python twin of the expressions above, for one loaded TableCost row."""
    if cost is None:
        return {}
    listed = cost.listed_bytes
    orphan = (cost.orphan_bytes or 0) if listed is not None else None
    accounted = cost.live_bytes + cost.dead_bytes + cost.metadata_bytes + (cost.orphan_bytes or 0)
    retained = listed - accounted if listed is not None and listed > accounted else 0
    snapshot = cost.dead_bytes + retained
    return {
        "live_bytes": cost.live_bytes,
        "snapshot_bytes": snapshot,
        "orphan_bytes": orphan,
        "metadata_bytes": cost.metadata_bytes,
        "junk_bytes": snapshot + (orphan or 0),
    }
