"""Commit-metadata quality signals: arrival/freshness and row-count anomalies, zero scan.

Implements the operational half of "Zero-Scan Data Quality" (Verma et al., FORMATS '26,
DOI 10.1145/3802514.3812601, §3): quality signals from Iceberg commit metadata alone, with
system operations filtered out by writer identity. The paper extends Iceberg commit metadata
with a writer-application tag; Cryo uses the spec's own snapshot ``operation`` instead --
``replace`` is defined as "data and delete files were added and removed without changing table
data", which is exactly compaction, sort, manifest and delete-file rewrites (all of Cryo's
commits). Row counts apply the paper's merge-on-read correction (§5.3):
``live_rows = total-records - total-position-deletes``.

The paper gives no detection thresholds; the ones below are Cryo's and are named as such.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SYSTEM_OPERATION = "replace"

# Cryo thresholds, not from the paper. Arrival is overdue once the silence exceeds twice the
# 90th-percentile gap between data commits (the paper notes even "daily" tables vary a lot in
# commit timing, so p90 rather than the median), and never under an hour.
ARRIVAL_MIN_GAPS = 5
ARRIVAL_P90_MULTIPLIER = 2.0
ARRIVAL_MIN_OVERDUE_HOURS = 1.0
# Net rows per data commit is anomalous past 5 robust z-scores (median / MAD) of the prior
# data commits' net rows; with a constant history, past a 10% change.
ROW_COUNT_MIN_BASELINE = 8
ROW_COUNT_ROBUST_Z = 5.0
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class Commit:
    committed_at: datetime
    operation: str | None = None
    added_records: int | None = None
    deleted_records: int | None = None
    total_records: int | None = None
    total_position_deletes: int | None = None

    @property
    def is_system(self) -> bool:
        return self.operation == SYSTEM_OPERATION

    @property
    def net_rows(self) -> int | None:
        if self.added_records is None and self.deleted_records is None:
            return None
        return (self.added_records or 0) - (self.deleted_records or 0)

    @property
    def live_rows(self) -> int | None:
        if self.total_records is None:
            return None
        return self.total_records - (self.total_position_deletes or 0)


def commits_from_payload(entries: Iterable[Mapping[str, Any]] | None) -> list[Commit]:
    commits = []
    for entry in entries or []:
        ms = entry.get("committed_at_ms")
        if not isinstance(ms, int | float):
            continue
        commits.append(
            Commit(
                committed_at=datetime.fromtimestamp(ms / 1000, tz=UTC),
                operation=entry.get("operation") or None,
                added_records=_int_or_none(entry.get("added_records")),
                deleted_records=_int_or_none(entry.get("deleted_records")),
                total_records=_int_or_none(entry.get("total_records")),
                total_position_deletes=_int_or_none(entry.get("total_position_deletes")),
            )
        )
    return sorted(commits, key=lambda c: c.committed_at)


def commits_to_payload(commits: Iterable[Commit]) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in {
                "committed_at_ms": int(c.committed_at.timestamp() * 1000),
                "operation": c.operation,
                "added_records": c.added_records,
                "deleted_records": c.deleted_records,
                "total_records": c.total_records,
                "total_position_deletes": c.total_position_deletes,
            }.items()
            if value is not None
        }
        for c in commits
    ]


def commits_from_snapshots(snapshots: Iterable[object], *, limit: int = 200) -> list[Commit]:
    """PyIceberg snapshots -> commits (newest ``limit``), matching the Go scanner's log."""
    commits = []
    for snapshot in snapshots:
        committed_at = _snapshot_time(snapshot)
        if committed_at is None:
            continue
        summary = getattr(snapshot, "summary", None)
        operation = getattr(summary, "operation", None)
        operation = getattr(operation, "value", operation)
        props = _summary_properties(summary)
        commits.append(
            Commit(
                committed_at=committed_at,
                operation=str(operation) if operation else None,
                added_records=_int_or_none(props.get("added-records")),
                deleted_records=_int_or_none(props.get("deleted-records")),
                total_records=_int_or_none(props.get("total-records")),
                total_position_deletes=_int_or_none(props.get("total-position-deletes")),
            )
        )
    commits.sort(key=lambda c: c.committed_at)
    return commits[-limit:]


def commit_signals(commits: list[Commit], *, now: datetime) -> dict[str, Any]:
    ordered = sorted(commits, key=lambda c: c.committed_at)
    data = [c for c in ordered if not c.is_system]
    signals: dict[str, Any] = {
        "retained_commits": len(ordered),
        "system_commits": len(ordered) - len(data),
        "data_commits": len(data),
        "last_data_commit_at": data[-1].committed_at.isoformat() if data else None,
        "hours_since_data_commit": None,
        "arrival_gap_p50_hours": None,
        "arrival_gap_p90_hours": None,
        "arrival_overdue": None,
        "live_rows": ordered[-1].live_rows if ordered else None,
        "last_commit_net_rows": None,
        "net_rows_baseline_median": None,
        "row_count_anomaly": None,
    }
    if data:
        signals["hours_since_data_commit"] = round(
            max(0.0, (now - data[-1].committed_at).total_seconds()) / 3600, 3
        )
    signals.update(_arrival(data, signals["hours_since_data_commit"]))
    signals.update(_row_count(data))
    return signals


def _arrival(data: list[Commit], hours_since: float | None) -> dict[str, Any]:
    gaps = [
        (b.committed_at - a.committed_at).total_seconds() / 3600
        for a, b in zip(data, data[1:], strict=False)
    ]
    if not gaps:
        return {}
    p50 = statistics.median(gaps)
    p90 = _percentile(gaps, 0.9)
    result: dict[str, Any] = {
        "arrival_gap_p50_hours": round(p50, 3),
        "arrival_gap_p90_hours": round(p90, 3),
    }
    if len(gaps) >= ARRIVAL_MIN_GAPS and hours_since is not None:
        limit = max(ARRIVAL_P90_MULTIPLIER * p90, ARRIVAL_MIN_OVERDUE_HOURS)
        result["arrival_overdue"] = hours_since > limit
    return result


def _row_count(data: list[Commit]) -> dict[str, Any]:
    nets = [n for c in data if (n := c.net_rows) is not None]
    if not nets:
        return {}
    last, baseline = nets[-1], nets[:-1]
    result: dict[str, Any] = {"last_commit_net_rows": last}
    if len(baseline) < ROW_COUNT_MIN_BASELINE:
        return result
    median = statistics.median(baseline)
    mad = statistics.median(abs(n - median) for n in baseline)
    result["net_rows_baseline_median"] = median
    if mad == 0:
        # Constant history (e.g. same-size overwrites): any change beyond 10% is a break.
        anomalous = abs(last - median) > max(1.0, 0.1 * abs(median))
    else:
        anomalous = abs(last - median) / (_MAD_TO_SIGMA * mad) > ROW_COUNT_ROBUST_Z
    if anomalous:
        result["row_count_anomaly"] = "drop" if last < median else "spike"
    return result


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = q * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def _snapshot_time(snapshot: object) -> datetime | None:
    ms = getattr(snapshot, "timestamp_ms", None)
    if isinstance(ms, int | float) and not isinstance(ms, bool):
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    committed_at = getattr(snapshot, "committed_at", None)
    if isinstance(committed_at, datetime):
        return committed_at if committed_at.tzinfo else committed_at.replace(tzinfo=UTC)
    return None


def _summary_properties(summary: object) -> Mapping[str, Any]:
    if summary is None:
        return {}
    for attr in ("additional_properties", "properties"):
        props = getattr(summary, attr, None)
        if isinstance(props, Mapping):
            return props
    if isinstance(summary, Mapping):
        return summary
    return {}


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None
