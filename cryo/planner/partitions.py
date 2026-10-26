from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from cryo.domain.enums import PartitionTransform, PredicateCapability
from cryo.domain.models import PartitionPredicate, PartitionPredicateTerm

_EPOCH = date(1970, 1, 1)

DEFAULT_RENDERABLE_CAPABILITIES = {PredicateCapability.BASIC_COMPARISON}


@dataclass(frozen=True)
class PartitionFieldSpec:
    base_column: str
    transform: PartitionTransform = PartitionTransform.IDENTITY
    col_type: str = ""
    partition_field: str | None = None
    transform_param: int | None = None

    @property
    def value_keys(self) -> tuple[str, ...]:
        keys = [self.partition_field, self.base_column]
        if self.transform is not PartitionTransform.IDENTITY:
            keys.append(f"{self.base_column}_{self.transform.value}")
        return tuple(key for key in keys if key)


def normalize_transform(value: Any) -> PartitionTransform:
    if isinstance(value, PartitionTransform):
        return value
    if value is None:
        return PartitionTransform.IDENTITY
    transform = str(value).strip().lower()
    if transform.startswith("bucket"):
        return PartitionTransform.BUCKET
    try:
        return PartitionTransform(transform)
    except ValueError:
        return PartitionTransform.UNSUPPORTED


# Display-only sentinel used by scanners for tables with an empty partition spec.
# It is NOT a real Iceberg column; treating it as bindable produces Go binder panics:
# "could not bind reference '_unpartitioned'".
_UNPARTITIONED_SENTINEL = "_unpartitioned"


def is_unpartitioned_sentinel(name: Any) -> bool:
    """True when *name* is the scanner display label for an unpartitioned table.

    ``None`` / blank are treated as sentinel when checking ``partition_col`` from
    stats. For optional ``partition_field`` on a real field, callers must not use
    this alone — see the filter in ``partition_fields_from_stats``.
    """
    if name is None:
        return True
    if not isinstance(name, str):
        return False
    return name.strip().lower() in {"", _UNPARTITIONED_SENTINEL}


def _is_sentinel_field(field: PartitionFieldSpec) -> bool:
    if is_unpartitioned_sentinel(field.base_column):
        return True
    if field.partition_field is not None and is_unpartitioned_sentinel(field.partition_field):
        return True
    return False


def infer_transform_from_name(name: str | None) -> PartitionTransform:
    if not name or is_unpartitioned_sentinel(name):
        return PartitionTransform.UNPARTITIONED
    lowered = name.lower()
    if lowered.endswith("_day"):
        return PartitionTransform.DAY
    if lowered.endswith("_month"):
        return PartitionTransform.MONTH
    if lowered.endswith("_year"):
        return PartitionTransform.YEAR
    bucket_match = re.search(r"bucket(?:\[(\d+)])?$", lowered)
    if bucket_match or lowered.endswith("_bucket"):
        return PartitionTransform.BUCKET
    return PartitionTransform.IDENTITY


def base_column_from_partition_name(name: str, transform: PartitionTransform) -> str:
    suffix = f"_{transform.value}"
    if transform in {PartitionTransform.DAY, PartitionTransform.MONTH, PartitionTransform.YEAR}:
        return name[: -len(suffix)] if name.lower().endswith(suffix) else name
    if transform is PartitionTransform.BUCKET and name.lower().endswith("_bucket"):
        return name[: -len("_bucket")]
    return name


def parse_literal_to_date(value: Any) -> date | None:
    """Parse a query-predicate literal (ISO text or a date/datetime) into a calendar date.

    Mirrors the acceptance rules of the Trino renderer's date parsing so both directions of
    the transform (render epoch-day -> SQL literal, and here: SQL literal -> epoch-day) agree
    on what counts as a date. Returns ``None`` rather than raising -- callers treat an
    unparseable literal as "can't attribute this query to a partition", not an error.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def date_to_transform_value(value: date, transform: PartitionTransform) -> int:
    """Convert a calendar date into the raw Iceberg partition-transform integer.

    The inverse of the epoch-day math the Trino/Spark renderers use to turn a stored
    transform value back into a SQL date literal (``value = EPOCH + timedelta(days=N)`` for
    DAY, etc.) -- same definitions, opposite direction, so a literal parsed from a query's
    WHERE clause lands on the same integer the scanner already recorded for that partition.
    """
    if transform is PartitionTransform.DAY:
        return (value - _EPOCH).days
    if transform is PartitionTransform.MONTH:
        return (value.year - _EPOCH.year) * 12 + (value.month - _EPOCH.month)
    if transform is PartitionTransform.YEAR:
        return value.year - _EPOCH.year
    raise ValueError(f"date_to_transform_value: unsupported transform {transform.value}")


_TIME_TRANSFORMS = (PartitionTransform.DAY, PartitionTransform.MONTH, PartitionTransform.YEAR)


def partition_date(field: PartitionFieldSpec, partition_row: Mapping[str, Any]) -> date | None:
    """First calendar day of a day/month/year partition, or None when it can't be read.

    Scanners store either the raw transform integer (days/months/years since 1970) or an
    ISO-style string ("2026-10-20", "2026-10", "2026"); both resolve to the same date.
    """
    if field.transform not in _TIME_TRANSFORMS:
        return None
    value = _partition_value(field, partition_row)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return _period_start(value, field.transform)
    text = str(value).strip()
    if text.lstrip("-").isdigit() and (
        field.transform is not PartitionTransform.YEAR or len(text) < 4
    ):
        return _transform_value_to_date(int(text), field.transform)
    try:
        if field.transform is PartitionTransform.DAY:
            return date.fromisoformat(text[:10])
        if field.transform is PartitionTransform.MONTH:
            return date(int(text[:4]), int(text[5:7]), 1)
        return date(int(text[:4]), 1, 1)
    except (ValueError, IndexError):
        return None


def _period_start(value: date, transform: PartitionTransform) -> date:
    if transform is PartitionTransform.MONTH:
        return value.replace(day=1)
    if transform is PartitionTransform.YEAR:
        return value.replace(month=1, day=1)
    return value


def _transform_value_to_date(value: int, transform: PartitionTransform) -> date | None:
    try:
        if transform is PartitionTransform.DAY:
            return date.fromordinal(_EPOCH.toordinal() + value)
        if transform is PartitionTransform.MONTH:
            year, month = divmod(value, 12)
            return date(_EPOCH.year + year, month + 1, 1)
        return date(_EPOCH.year + value, 1, 1)
    except (ValueError, OverflowError):
        return None


def is_open_time_partition(
    fields: Sequence[PartitionFieldSpec],
    partition_row: Mapping[str, Any],
    today: date,
) -> bool:
    """True when a day/month/year partition value is the current period (still taking writes)."""
    for field in fields:
        start = partition_date(field, partition_row)
        if start is not None and start == _period_start(today, field.transform):
            return True
    return False


def partition_fields_from_stats(
    stats: Mapping[str, Any],
    params: Mapping[str, Any] | None = None,
) -> tuple[PartitionFieldSpec, ...]:
    params = params or {}
    # Both scanners store the real spec under stats.raw. The partition_col fallback below
    # guesses the transform from the column name; for columns like `joining_date` (a month
    # transform) that guessed identity, which live 2026-09-24 was wrong for ~2,500 tables.
    raw_fields = (
        params.get("partition_fields")
        or stats.get("partition_fields")
        or (stats.get("raw") or {}).get("partition_fields")
    )
    if raw_fields:
        fields = tuple(
            field
            for field in (_field_from_raw(raw) for raw in raw_fields)
            if not _is_sentinel_field(field)
        )
        return fields

    partition_col = params.get("partition_col") or stats.get("partition_col")
    # Legacy scans stored the display sentinel "_unpartitioned" as partition_col.
    # That must not become a bindable predicate term.
    if is_unpartitioned_sentinel(partition_col):
        return ()

    transform = normalize_transform(
        params.get("partition_transform") or stats.get("partition_transform")
    )
    if transform is PartitionTransform.IDENTITY and not (
        params.get("partition_transform") or stats.get("partition_transform")
    ):
        transform = infer_transform_from_name(str(partition_col))
    if transform is PartitionTransform.UNPARTITIONED:
        return ()

    return (
        PartitionFieldSpec(
            base_column=str(
                params.get("base_column")
                or base_column_from_partition_name(str(partition_col), transform)
            ),
            transform=transform,
            col_type=str(params.get("partition_col_type") or stats.get("partition_col_type") or ""),
            partition_field=str(partition_col),
            transform_param=_extract_bucket_count(
                params.get("partition_transform") or stats.get("partition_transform")
            ),
        ),
    )


def build_predicate(
    fields: Sequence[PartitionFieldSpec],
    partition_row: Mapping[str, Any],
) -> PartitionPredicate:
    terms: list[PartitionPredicateTerm] = []
    unsupported: list[str] = []
    for field in fields:
        value = _partition_value(field, partition_row)
        reason = None
        if field.transform is PartitionTransform.UNSUPPORTED:
            reason = (
                f"unsupported partition transform for {field.partition_field or field.base_column}"
            )
            unsupported.append(reason)
        elif value is None:
            reason = f"missing partition value for {field.partition_field or field.base_column}"
            unsupported.append(reason)
        terms.append(
            PartitionPredicateTerm(
                base_column=field.base_column,
                transform=field.transform,
                value=value,
                col_type=field.col_type,
                transform_param=field.transform_param,
                partition_field=field.partition_field,
                unsupported_reason=reason,
            )
        )
    return PartitionPredicate(
        terms=tuple(terms),
        unsupported_reason="; ".join(unsupported) if unsupported else None,
    )


def predicate_is_renderable(
    predicate: PartitionPredicate,
    capabilities: set[PredicateCapability] | None = None,
) -> bool:
    return predicate.unsupported_for(capabilities or DEFAULT_RENDERABLE_CAPABILITIES) is None


def _field_from_raw(field: Any) -> PartitionFieldSpec:
    if isinstance(field, PartitionFieldSpec):
        return field
    if isinstance(field, str):
        transform = infer_transform_from_name(field)
        return PartitionFieldSpec(
            base_column=base_column_from_partition_name(field, transform),
            transform=transform,
            partition_field=field,
        )

    raw = dict(field)
    partition_field = raw.get("partition_field") or raw.get("name") or raw.get("field")
    transform_raw = raw.get("transform") or raw.get("partition_transform")
    transform = (
        normalize_transform(transform_raw)
        if transform_raw
        else infer_transform_from_name(str(partition_field or ""))
    )
    return PartitionFieldSpec(
        base_column=str(
            raw.get("base_column")
            or raw.get("source_column")
            or base_column_from_partition_name(str(partition_field), transform)
        ),
        transform=transform,
        col_type=str(raw.get("col_type") or raw.get("type") or raw.get("partition_col_type") or ""),
        partition_field=str(partition_field) if partition_field else None,
        transform_param=raw.get("transform_param")
        or raw.get("num_buckets")
        or _extract_bucket_count(transform_raw),
    )


def _partition_value(field: PartitionFieldSpec, partition_row: Mapping[str, Any]) -> Any:
    values = partition_row.get("partition_values")
    if isinstance(values, Mapping) and values:
        for key in field.value_keys:
            if key in values:
                return values[key]
        # A structured row without this field is a partition written under another spec
        # (e.g. before the table was partitioned). `partition_value` is only a display
        # string there -- "<nil>" from the Go scanner -- never a bindable value.
        return None

    for key in field.value_keys:
        if key in partition_row:
            return partition_row[key]

    if len(field.value_keys) == 1 and "partition_value" in partition_row:
        return partition_row["partition_value"]
    if "partition_value" in partition_row and field.partition_field:
        value = partition_row["partition_value"]
        if not isinstance(value, Mapping):
            return value
    return None


def _extract_bucket_count(transform: Any) -> int | None:
    if transform is None:
        return None
    match = re.search(r"bucket[^0-9]{0,16}([0-9]{1,9})", str(transform).lower())
    return int(match.group(1)) if match else None
