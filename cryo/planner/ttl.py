"""Time-based data expiry planning. Emits no SQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from cryo.domain.enums import OperationType
from cryo.domain.models import Operation

_RETENTION_UNITS = {"d": "days", "h": "hours"}


def parse_retention(value: str) -> timedelta:
    text = value.strip().lower()
    if len(text) < 2 or text[-1] not in _RETENTION_UNITS or not text[:-1].isdigit():
        raise ValueError(f"expire_data retention must look like 7d or 12h, got {value!r}")
    amount = int(text[:-1])
    if amount <= 0:
        raise ValueError(f"expire_data retention must be positive, got {value!r}")
    return timedelta(**{_RETENTION_UNITS[text[-1]]: amount})


def plan_expire_data(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    *,
    now: datetime | None = None,
    protected_snapshot_ids: Sequence[int] = (),
) -> tuple[Operation | None, tuple[str, ...]]:
    if not bool(params.get("expire_data_enabled")):
        return None, ()
    notes: list[str] = []
    field = str(params.get("expire_data_field") or "").strip()
    retention_text = str(params.get("expire_data_retention") or "").strip()
    if not field or not retention_text:
        return None, (
            "Skipped expire_data: expire_data_enabled is set but field or retention is missing.",
        )
    try:
        retention = parse_retention(retention_text)
    except ValueError as exc:
        return None, (f"Skipped expire_data: {exc}.",)

    level = str(params.get("expire_data_level") or "partition").strip().lower()
    if level not in {"partition", "file"}:
        return None, (f"Skipped expire_data: level {level!r} is not partition or file.",)
    base = str(params.get("expire_data_base") or "last_commit_time").strip().lower()
    if base not in {"last_commit_time", "current_time"}:
        return None, (
            f"Skipped expire_data: base {base!r} is not last_commit_time or current_time.",
        )

    anchor_now = _as_utc(now or _parse_time(params.get("expire_data_now")) or datetime.now(UTC))
    last_commit = _as_utc(_parse_time(_last_commit(stats, params)))
    if base == "last_commit_time" and last_commit is None:
        return None, (
            "Skipped expire_data: last_commit_time has no non-optimize commit timestamp, "
            "so the cutoff was not taken from the wall clock.",
        )
    anchor = last_commit if base == "last_commit_time" else anchor_now
    assert anchor is not None
    cutoff = anchor - retention

    partition_col = str(stats.get("partition_col") or "").strip()
    aligned = level == "partition" and partition_col == field
    if level == "partition" and not aligned:
        return None, (
            "Skipped expire_data: field "
            f"{field!r} is not the partition column {partition_col or '<unset>'!r}. "
            "Set expire_data_level=file to expire rows instead of whole partitions.",
        )

    estimated_partitions, estimated_bytes = _estimate(partitions, cutoff)
    protected = sorted({int(item) for item in protected_snapshot_ids})
    op_params: dict[str, Any] = {
        "field": field,
        "level": level,
        "retention": retention_text,
        "base": base,
        "cutoff": cutoff.isoformat(),
        "field_type": str(params.get("expire_data_field_type") or "timestamp"),
        "number_format": str(params.get("expire_data_number_format") or "TIMESTAMP_MS"),
        "datetime_pattern": str(params.get("expire_data_datetime_pattern") or "yyyy-MM-dd"),
        "row_level": level == "file",
        "partition_aligned": aligned,
        "protected_snapshot_ids": protected,
        "estimated_partitions": estimated_partitions,
        "estimated_bytes": estimated_bytes,
    }
    notes.append(
        f"Planned expire_data on {field} < {cutoff.isoformat()} "
        f"({level}, retention {retention_text}, base {base}). "
        f"Estimated {estimated_partitions} partitions / {estimated_bytes} bytes."
    )
    if level == "file":
        notes.append(
            "expire_data level=file deletes matching rows and may rewrite data files. "
            "It is destructive and requires approval."
        )
    if protected:
        notes.append(
            "Protected snapshots "
            f"{protected} still show the expired rows via time travel until expire_snapshots "
            "is allowed to remove them."
        )
    return Operation(OperationType.EXPIRE_DATA, op_params), tuple(notes)


def _last_commit(stats: Mapping[str, Any], params: Mapping[str, Any]) -> Any:
    if params.get("expire_data_last_commit_at"):
        return params.get("expire_data_last_commit_at")
    raw_value = stats.get("raw")
    raw: Mapping[str, Any] = raw_value if isinstance(raw_value, Mapping) else {}
    return (
        stats.get("last_non_optimize_commit_at")
        or raw.get("last_non_optimize_commit_at")
        or stats.get("last_snapshot_committed_at")
        or raw.get("last_snapshot_committed_at")
    )


def _estimate(partitions: Sequence[Mapping[str, Any]], cutoff: datetime) -> tuple[int, int]:
    count = 0
    nbytes = 0
    for row in partitions:
        parsed = _as_utc(_parse_time(row.get("partition_value", row.get("partition_values"))))
        if parsed is None or parsed >= cutoff:
            continue
        count += 1
        nbytes += int(row.get("total_bytes") or row.get("data_bytes") or 0)
    return count, nbytes


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=int(value))
    text = str(value).strip()
    if text.isdigit():
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
