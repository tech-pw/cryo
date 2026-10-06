"""Declarative data-quality constraints evaluated on stored scan metadata, zero data scan.

Deequ (Schelter et al., "Automating Large-Scale Data Quality Verification", VLDB 2018) turns
declarative constraints into reusable metric computations and checks them against a metrics
repository. Cryo keeps that split -- metric collection is the scanner's manifest walk; this
module is only policy -- but its metrics come from Iceberg metadata instead of a data pass, as
Zero-Scan Data Quality (Verma et al., FORMATS '26) §5.4 proposes. Kinds and their Deequ
counterparts:

    size         hasSize(assertion)
    completeness hasCompleteness(column, _ >= min) / isComplete when min == 1
    range        isContainedIn(column, lower, upper) / isNonNegative / hasMin / hasMax
    size_change  hasSize with RelativeRateOfChangeStrategy anomaly detection
    freshness    no Deequ counterpart; ZSDQ commit-arrival signal

Metadata is not the data, so each check returns ``unknown`` whenever the stored metrics cannot
decide it: a column some files carry no metrics for, bounds missing from some files, or delete
files that make a failure unprovable. A passing result is only returned when it holds for the
live rows; deletes can only remove values, so bound and null-count checks that pass on all
rows still pass after deletes, while failures need a delete-free snapshot.

Results are as of the scanned snapshot, not the evaluation time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class ConstraintKind(StrEnum):
    SIZE = "size"
    COMPLETENESS = "completeness"
    RANGE = "range"
    SIZE_CHANGE = "size_change"
    FRESHNESS = "freshness"


class ConstraintStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


COLUMN_KINDS = frozenset({ConstraintKind.COMPLETENESS, ConstraintKind.RANGE})

_NUMERIC_TYPES = frozenset({"int", "long", "float", "double"})
_TIMESTAMP_TYPES = frozenset({"timestamp", "timestamptz", "timestamp_ns", "timestamptz_ns"})


@dataclass(frozen=True)
class ConstraintSpec:
    kind: ConstraintKind
    params: Mapping[str, Any]
    field_id: int | None = None
    column_name: str | None = None


@dataclass(frozen=True)
class MetricsView:
    """What one scan stored about a table: the evaluator's whole input."""

    snapshot_id: int | None
    scanned_at: datetime | None
    data_records: int | None
    live_rows: int | None
    position_delete_files: int
    equality_delete_files: int
    columns: Mapping[int, Mapping[str, Any]]
    hours_since_data_commit: float | None
    data_commits: int | None

    @property
    def has_deletes(self) -> bool:
        return self.position_delete_files > 0 or self.equality_delete_files > 0

    @classmethod
    def from_stats(
        cls,
        *,
        raw: Mapping[str, Any] | None,
        total_records: int | None,
        scanned_at: datetime | None,
        position_delete_files: int | None,
        equality_delete_files: int | None,
    ) -> MetricsView:
        raw = raw or {}
        signals = raw.get("commit_signals")
        signals = signals if isinstance(signals, Mapping) else {}
        columns: dict[int, Mapping[str, Any]] = {}
        for entry in raw.get("column_quality") or []:
            if isinstance(entry, Mapping) and isinstance(entry.get("field_id"), int):
                columns[entry["field_id"]] = entry
        snapshot_id = raw.get("current_snapshot_id")
        return cls(
            snapshot_id=snapshot_id if isinstance(snapshot_id, int) else None,
            scanned_at=scanned_at,
            data_records=total_records,
            live_rows=_int(signals.get("live_rows")),
            position_delete_files=int(
                position_delete_files or _int(raw.get("position_delete_files")) or 0
            ),
            equality_delete_files=int(
                equality_delete_files or _int(raw.get("equality_delete_files")) or 0
            ),
            columns=columns,
            hours_since_data_commit=_float(signals.get("hours_since_data_commit")),
            data_commits=_int(signals.get("data_commits")),
        )


@dataclass(frozen=True)
class ConstraintResult:
    status: ConstraintStatus
    observed: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def column_kinds(column: Mapping[str, Any]) -> list[ConstraintKind]:
    """Column constraint kinds a stored column_quality entry can support."""
    column_type = str(column.get("column_type") or "")
    if _nested(column_type):
        return []
    kinds = [ConstraintKind.COMPLETENESS]
    if _range_type(column_type):
        kinds.append(ConstraintKind.RANGE)
    return kinds


def validate_spec(spec: ConstraintSpec, column: Mapping[str, Any] | None = None) -> None:
    """Raise ValueError for a constraint that can never be evaluated as written."""
    p = spec.params
    if spec.kind in COLUMN_KINDS and spec.field_id is None:
        raise ValueError(f"{spec.kind} needs a column")
    if spec.kind not in COLUMN_KINDS and spec.field_id is not None:
        raise ValueError(f"{spec.kind} is a table-level constraint")
    column_type = str((column or {}).get("column_type") or "")
    if spec.kind in COLUMN_KINDS and column is not None and _nested(column_type):
        raise ValueError("constraints apply to top-level primitive columns only")
    if spec.kind is ConstraintKind.SIZE:
        lo, hi = _opt_int(p, "min_rows"), _opt_int(p, "max_rows")
        if lo is None and hi is None:
            raise ValueError("size needs min_rows and/or max_rows")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("min_rows exceeds max_rows")
    elif spec.kind is ConstraintKind.COMPLETENESS:
        minimum = _opt_float(p, "min")
        if minimum is None or not 0 < minimum <= 1:
            raise ValueError("completeness needs 0 < min <= 1")
    elif spec.kind is ConstraintKind.RANGE:
        if p.get("min") is None and p.get("max") is None:
            raise ValueError("range needs min and/or max")
        if column is not None and not _range_type(column_type):
            raise ValueError(f"range is not supported for column type {column_type!r}")
        if column is not None:
            low = _parse(column_type, p.get("min"))
            high = _parse(column_type, p.get("max"))
            if (p.get("min") is not None and low is None) or (
                p.get("max") is not None and high is None
            ):
                raise ValueError(f"range bounds do not parse as {column_type}")
            if low is not None and high is not None and low > high:
                raise ValueError("range min exceeds max")
    elif spec.kind is ConstraintKind.SIZE_CHANGE:
        dec, inc = _opt_float(p, "max_decrease"), _opt_float(p, "max_increase")
        if dec is None and inc is None:
            raise ValueError("size_change needs max_decrease and/or max_increase")
        if dec is not None and not 0 <= dec <= 1:
            raise ValueError("max_decrease is a fraction between 0 and 1")
        if inc is not None and inc < 0:
            raise ValueError("max_increase must be non-negative")
    elif spec.kind is ConstraintKind.FRESHNESS:
        hours = _opt_float(p, "max_hours")
        if hours is None or hours <= 0:
            raise ValueError("freshness needs max_hours > 0")


def evaluate(
    spec: ConstraintSpec,
    view: MetricsView,
    *,
    baseline: Mapping[str, Any] | None = None,
) -> ConstraintResult:
    """Evaluate one constraint. ``baseline`` is size_change's previous observation."""
    evidence: dict[str, Any] = {
        "snapshot_id": view.snapshot_id,
        "scanned_at": view.scanned_at.isoformat() if view.scanned_at else None,
        "position_delete_files": view.position_delete_files,
        "equality_delete_files": view.equality_delete_files,
    }
    if spec.kind is ConstraintKind.SIZE:
        return _size(spec, view, evidence)
    if spec.kind is ConstraintKind.SIZE_CHANGE:
        return _size_change(spec, view, evidence, baseline)
    if spec.kind is ConstraintKind.FRESHNESS:
        return _freshness(spec, view, evidence)
    column = view.columns.get(spec.field_id) if spec.field_id is not None else None
    if column is None:
        return _unknown(
            "no stored metrics for this column (dropped, or metrics disabled)", evidence
        )
    coverage = _coverage(column, view)
    evidence["coverage"] = coverage
    if coverage != 1.0:
        return _unknown(
            "some data files carry no metrics for this column"
            if coverage is not None
            else "table record count unknown",
            evidence,
        )
    if spec.kind is ConstraintKind.COMPLETENESS:
        return _completeness(spec, view, column, evidence)
    return _range(spec, view, column, evidence)


def live_rows(view: MetricsView) -> int | None:
    """Rows a reader sees, when metadata determines it exactly."""
    if not view.has_deletes:
        return view.data_records
    if view.equality_delete_files:
        return None
    return view.live_rows


def _size(spec: ConstraintSpec, view: MetricsView, evidence: dict[str, Any]) -> ConstraintResult:
    rows = live_rows(view)
    if rows is None:
        return _unknown(_rows_unknown_reason(view), evidence)
    lo, hi = _opt_int(spec.params, "min_rows"), _opt_int(spec.params, "max_rows")
    ok = (lo is None or rows >= lo) and (hi is None or rows <= hi)
    return _decided(ok, {"live_rows": rows}, evidence, f"{rows} live rows outside [{lo}, {hi}]")


def _size_change(
    spec: ConstraintSpec,
    view: MetricsView,
    evidence: dict[str, Any],
    baseline: Mapping[str, Any] | None,
) -> ConstraintResult:
    rows = live_rows(view)
    if rows is None:
        return _unknown(_rows_unknown_reason(view), evidence)
    observed: dict[str, Any] = {"live_rows": rows}
    previous = _int((baseline or {}).get("live_rows"))
    if previous is None:
        return ConstraintResult(
            ConstraintStatus.UNKNOWN, observed, "no earlier snapshot to compare against", evidence
        )
    evidence["baseline_snapshot_id"] = (baseline or {}).get("snapshot_id")
    observed["baseline_live_rows"] = previous
    if previous == 0:
        return ConstraintResult(
            ConstraintStatus.UNKNOWN, observed, "baseline was empty; rate undefined", evidence
        )
    change = (rows - previous) / previous
    observed["relative_change"] = round(change, 6)
    dec = _opt_float(spec.params, "max_decrease")
    inc = _opt_float(spec.params, "max_increase")
    ok = (dec is None or change >= -dec) and (inc is None or change <= inc)
    return _decided(ok, observed, evidence, f"live rows changed {change:+.1%} since the baseline")


def _freshness(
    spec: ConstraintSpec, view: MetricsView, evidence: dict[str, Any]
) -> ConstraintResult:
    hours = view.hours_since_data_commit
    if hours is None:
        return _unknown(
            "no data commit in the retained snapshot log"
            if view.data_commits == 0
            else "commit log not recorded by this scan",
            evidence,
        )
    limit = float(spec.params["max_hours"])
    return _decided(
        hours <= limit,
        {"hours_since_data_commit": hours},
        evidence,
        f"last data commit {hours:.1f}h before the scan (limit {limit:g}h)",
    )


def _completeness(
    spec: ConstraintSpec,
    view: MetricsView,
    column: Mapping[str, Any],
    evidence: dict[str, Any],
) -> ConstraintResult:
    minimum = float(spec.params["min"])
    nulls = int(column.get("null_count") or 0)
    if not view.has_deletes:
        rows = view.data_records or 0
        if rows == 0:
            return _unknown("table is empty", evidence)
        completeness = 1 - nulls / rows
        return _decided(
            completeness >= minimum,
            {"completeness": round(completeness, 6), "null_count": nulls, "rows": rows},
            evidence,
            f"completeness {completeness:.4%} below {minimum:.4%}",
        )
    live = live_rows(view)
    if live is None:
        return _unknown(_rows_unknown_reason(view), evidence)
    if live == 0:
        return _unknown("table is empty", evidence)
    # Deleted rows can only take nulls away, so this is the worst case for the live rows.
    floor = 1 - min(nulls, live) / live
    observed = {"completeness_lower_bound": round(floor, 6), "null_count_all_rows": nulls}
    if floor >= minimum:
        return ConstraintResult(ConstraintStatus.PASSED, observed, None, evidence)
    return ConstraintResult(
        ConstraintStatus.UNKNOWN,
        observed,
        "delete files may remove some of the counted nulls; failure is not provable",
        evidence,
    )


def _range(
    spec: ConstraintSpec,
    view: MetricsView,
    column: Mapping[str, Any],
    evidence: dict[str, Any],
) -> ConstraintResult:
    column_type = str(column.get("column_type") or "")
    if not _range_type(column_type):
        return _unknown(f"range is not supported for column type {column_type!r}", evidence)
    if column.get("bounds_complete") is not True:
        return _unknown(
            "some files holding values carry no bounds for this column"
            if column.get("bounds_complete") is False
            else "scan predates bound-completeness tracking; rescan to evaluate",
            evidence,
        )
    non_null = int(column.get("value_count") or 0) - int(column.get("null_count") or 0)
    if non_null <= 0:
        return ConstraintResult(ConstraintStatus.PASSED, {"non_null_values": 0}, None, evidence)
    lower = _parse(column_type, column.get("lower_bound"))
    upper = _parse(column_type, column.get("upper_bound"))
    if lower is None or upper is None:
        return _unknown("column bounds missing or unparseable", evidence)
    lo = _parse(column_type, spec.params.get("min"))
    hi = _parse(column_type, spec.params.get("max"))
    observed = {"min": column.get("lower_bound"), "max": column.get("upper_bound")}
    if (lo is None or lower >= lo) and (hi is None or upper <= hi):
        return ConstraintResult(ConstraintStatus.PASSED, observed, None, evidence)
    if view.has_deletes:
        return ConstraintResult(
            ConstraintStatus.UNKNOWN,
            observed,
            "out-of-range values may belong to deleted rows; failure is not provable",
            evidence,
        )
    return ConstraintResult(
        ConstraintStatus.FAILED,
        observed,
        f"values span [{observed['min']}, {observed['max']}], allowed "
        f"[{spec.params.get('min')}, {spec.params.get('max')}]",
        evidence,
    )


def _coverage(column: Mapping[str, Any], view: MetricsView) -> float | None:
    # Iceberg value_counts include nulls, so a column every data file reports sums to the
    # table's record count exactly.
    if view.data_records is None:
        return None
    if view.data_records == 0:
        return 1.0
    return round(int(column.get("value_count") or 0) / view.data_records, 6)


def _rows_unknown_reason(view: MetricsView) -> str:
    if view.equality_delete_files:
        return "equality deletes: live row count is not derivable from metadata"
    return "live row count not recorded by this scan"


def _decided(
    ok: bool, observed: dict[str, Any], evidence: dict[str, Any], failure: str
) -> ConstraintResult:
    if ok:
        return ConstraintResult(ConstraintStatus.PASSED, observed, None, evidence)
    return ConstraintResult(ConstraintStatus.FAILED, observed, failure, evidence)


def _unknown(reason: str, evidence: dict[str, Any]) -> ConstraintResult:
    return ConstraintResult(ConstraintStatus.UNKNOWN, {}, reason, evidence)


def _nested(column_type: str) -> bool:
    return any(token in column_type for token in ("<", "struct", "list", "map"))


def _range_type(column_type: str) -> bool:
    return (
        column_type in _NUMERIC_TYPES
        or column_type in _TIMESTAMP_TYPES
        or column_type == "date"
        or column_type.startswith("decimal(")
    )


_FRACTION = re.compile(r"(\.\d{6})\d+")


# Decimal, date or datetime; always the same one for a given column type, so values parsed
# for one column compare with each other.
type _Ordered = Any


def _parse(column_type: str, value: Any) -> _Ordered | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if column_type in _NUMERIC_TYPES or column_type.startswith("decimal("):
            return Decimal(str(value))
        if column_type == "date":
            return date.fromisoformat(str(value)[:10])
        if column_type in _TIMESTAMP_TYPES:
            parsed = datetime.fromisoformat(_FRACTION.sub(r"\1", str(value)))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (InvalidOperation, ValueError):
        return None
    return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(params: Mapping[str, Any], key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    parsed = _int(value)
    if parsed is None:
        raise ValueError(f"{key} must be an integer")
    return parsed


def _opt_float(params: Mapping[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value is None:
        return None
    parsed = _float(value)
    if parsed is None:
        raise ValueError(f"{key} must be a number")
    return parsed
