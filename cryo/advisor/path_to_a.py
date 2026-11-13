"""Path to A: every health deduction on a table, and the step that removes it.

The advisor never changes the scoring rubric. It replays the scorer's own thresholds
(``cryo.scanner.health``) over projected metrics, so a projected grade is the grade the next
scan would give if the step's estimate holds. Projections come from the latest scan's
partition rows; each step says whether its numbers were measured from those rows, estimated
by scaling them, or assumed.

Step kinds:

* ``automatic`` -- routine maintenance Cryo already runs (or can run now) with no extra risk.
* ``approval`` -- Cryo can do it, but it changes behaviour someone may depend on (time
  travel window, table properties, a larger rewrite), so a person approves it.
* ``design`` -- a table layout change (partition spec, sort order). Admin only, typed
  confirmation; affects new writes, and existing data as it is rewritten.
* ``external`` -- needs a change outside Cryo (the writer's commit rate or file format).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from statistics import median
from typing import Any, Literal

from cryo.domain.enums import PartitionTransform
from cryo.domain.lifecycle import (
    ARCHIVE_CANDIDATE_NO_COMMIT_DAYS,
    ARCHIVE_CANDIDATE_NO_READ_DAYS,
    is_archive_candidate,
    is_archived,
)
from cryo.planner.partitions import PartitionFieldSpec, is_open_time_partition, partition_date
from cryo.planner.planner import _expire_params
from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    MIN_SMALL_FILES_FOR_COMPACTION,
    compute_design_findings,
    excess_files_per_partition,
    grade_for_score,
    health_thresholds_for,
)

StepKind = Literal["automatic", "approval", "design", "external"]
Confidence = Literal["measured", "estimated", "assumed"]

MIB = 1024 * 1024
GIB = 1024 * MIB
# Must match cryo.scanner.analyze.HEALTH_SMALL_FILE_BYTES (what health counts as a small file).
HEALTH_SMALL_FILE_BYTES = 8 * MIB

_LABEL_KEYS = {
    "Small-file fragmentation": "small_file_pct",
    "Excessive file count": "total_files",
    "High file count": "total_files",
    "Position delete file buildup": "position_deletes",
    "Equality delete buildup": "equality_deletes",
    "Snapshot retention buildup": "snapshot_count",
    "Stale table commits": "snapshot_age_days",
    "Manifest files bloat": "manifest_count",
    "Partition count complexity": "partition_count",
    "Partition volume skew": "partition_skew_ratio",
    "Fragmented partitions": "avg_files_per_partition",
    "Metadata ratio overhead": "metadata_to_data_ratio",
    "V3 upgrade candidate": "v3_upgrade",
    "High velocity streaming": "streaming",
    "Suboptimal properties": "properties",
    "Legacy file formats": "file_formats",
}
# Partition count and skew are design findings since #285 and cost no points, so a layout
# change is judged only on the file metrics it moves.
_FILE_KEYS = ("small_file_pct", "total_files", "avg_files_per_partition")
_LAYOUT_KEYS = _FILE_KEYS

_COARSER = {
    PartitionTransform.DAY: (PartitionTransform.MONTH, PartitionTransform.YEAR),
    PartitionTransform.MONTH: (PartitionTransform.YEAR,),
}
_TIME_TRANSFORMS = (PartitionTransform.DAY, PartitionTransform.MONTH, PartitionTransform.YEAR)


@dataclass(frozen=True)
class TableSnapshot:
    """What the advisor reads: the latest scan, the effective workflow, and query signals.

    ``commits_per_day`` of None means the rate couldn't be measured.
    """

    table_name: str
    score: int
    grade: str
    deductions: Sequence[Mapping[str, Any]]
    total_files: int
    total_partitions: int
    total_size_bytes: int
    total_snapshots: int
    manifest_count: int
    commits_per_day: float | None
    is_streaming: bool
    last_commit_at: datetime | None
    table_target_bytes: int | None
    has_sort_order: bool
    write_distribution_mode: str | None
    params: Mapping[str, Any]
    scheduled_operations: frozenset[str]
    partition_fields: tuple[PartitionFieldSpec, ...]
    partitions: Sequence[Mapping[str, Any]]
    partition_skew_ratio: float = 0.0
    query_filters: Mapping[str, int] = field(default_factory=dict)
    property_changes: Mapping[str, str] = field(default_factory=dict)
    sort_columns: Sequence[str] = ()
    # The scanner's stats as compute_health_score saw them (total_size_gb, avg_file_size_mb,
    # snapshot_retention_days, commits_per_day, ...), so projections use the same limits.
    stats: Mapping[str, Any] = field(default_factory=dict)
    # The scanner's raw["spec_migration"]: default_spec_id and the old-spec partition, file and
    # byte counts. Empty when the scan predates it.
    spec_migration: Mapping[str, Any] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    # #519: "archived" tables get no compaction, layout or property steps, and their grade
    # carries no stale-commit deduction. query_count_30d of None means reads aren't recorded.
    lifecycle: str = "active"
    query_count_30d: int | None = None
    # ISO date of the next scheduled remove_orphan_files under the per-table cadence
    # (cryo.planner.orphan_cadence). None when the workflow does not schedule it.
    next_orphan_cleanup_due: str | None = None


@dataclass
class Step:
    id: str
    kind: StepKind
    title: str
    detail: str
    fixes: list[str]
    projected_score: int
    projected_grade: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)
    action: dict[str, Any] | None = None
    required_role: str | None = None
    confirm_text: str | None = None
    in_schedule: bool = False
    # Deductions this step clears only together with the named steps (#520).
    fixes_with: list[str] = field(default_factory=list)
    fixes_with_steps: list[str] = field(default_factory=list)


@dataclass
class PathToA:
    score: int
    grade: str
    deductions: list[dict[str, Any]]
    steps: list[Step]
    cryo_score: int
    cryo_grade: str
    reachable_score: int
    reachable_grade: str
    notes: list[str]
    design_findings: list[dict[str, Any]] = field(default_factory=list)
    next_orphan_cleanup_due: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def points_for(
    key: str, value: float, thresholds: Mapping[str, int | float] = HEALTH_THRESHOLDS
) -> int:
    """Deduction the scorer gives a metric value against the given warn/crit limits."""
    if value >= thresholds[f"{key}_crit"]:
        return 20
    if value >= thresholds[f"{key}_warn"]:
        return 8
    return 0


def small_file_points(
    small_files: int,
    total_files: int,
    thresholds: Mapping[str, int | float] = HEALTH_THRESHOLDS,
) -> int:
    if small_files < MIN_SMALL_FILES_FOR_COMPACTION:
        return 0
    return points_for("small_file_pct", small_files / max(total_files, 1) * 100, thresholds)


def _health_stats(snapshot: TableSnapshot, **overrides: Any) -> dict[str, Any]:
    """The scorer's inputs for this table, optionally with projected file/partition counts.

    A projected file count keeps the table's data size, so the scorer's file-count floor
    (data size / target size) doesn't move with it.
    """
    stats: dict[str, Any] = dict(snapshot.stats)
    stats.setdefault("total_size_gb", snapshot.total_size_bytes / GIB)
    stats.setdefault("total_files", snapshot.total_files)
    stats.setdefault("total_partitions", snapshot.total_partitions)
    stats.setdefault("target_file_size_bytes", snapshot.table_target_bytes)
    stats.setdefault("partition_skew_ratio", snapshot.partition_skew_ratio)
    stats.setdefault("is_streaming", snapshot.is_streaming)
    if "avg_file_size_mb" not in stats and snapshot.total_files:
        stats["avg_file_size_mb"] = snapshot.total_size_bytes / MIB / snapshot.total_files
    files = overrides.pop("total_files", None)
    if files:
        data_bytes = float(stats.get("avg_file_size_mb") or 0) * MIB * int(stats["total_files"])
        stats["total_files"] = files
        stats["avg_file_size_mb"] = data_bytes / MIB / files
    stats.update(overrides)
    return stats


def _thresholds(snapshot: TableSnapshot, **overrides: Any) -> dict[str, int | float]:
    return health_thresholds_for(_health_stats(snapshot, **overrides))


_SEVERITY_WEIGHT = {"warning": 1, "critical": 2}


def _design_labels(snapshot: TableSnapshot, **overrides: Any) -> dict[str, int]:
    """Design findings as label -> severity weight (critical 2, warning 1)."""
    findings = compute_design_findings(_health_stats(snapshot, **overrides))
    return {
        str(finding["label"]): _SEVERITY_WEIGHT.get(str(finding["severity"]), 1)
        for finding in findings
    }


def commit_rate_from_history(history: Sequence[Mapping[str, Any]]) -> float | None:
    """Median commits/day across scan pairs at least an hour apart where snapshots grew.

    The scanner's own signal compares only the last two scans and reads 0 when they land
    close together; this looks further back. Expiry between two scans hides commits, so
    pairs where the count fell are skipped and the result leans low.
    """
    rates: list[float] = []
    ordered = [row for row in history if row.get("scan_at") and row.get("total_snapshots")]
    for previous, latest in zip(ordered, ordered[1:], strict=False):
        start = _as_datetime(previous["scan_at"])
        end = _as_datetime(latest["scan_at"])
        if start is None or end is None:
            continue
        hours = (end - start).total_seconds() / 3600
        delta = int(latest["total_snapshots"]) - int(previous["total_snapshots"])
        if hours >= 1 and delta > 0:
            rates.append(delta / hours * 24)
    return float(median(rates)) if rates else None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class _Projection:
    def __init__(self, deductions: Sequence[Mapping[str, Any]]) -> None:
        self.points: dict[str, int] = {}
        self.labels: dict[str, str] = {}
        for deduction in deductions:
            label = str(deduction.get("label") or "")
            key = _LABEL_KEYS.get(label, f"other:{label}")
            self.points[key] = self.points.get(key, 0) + int(deduction.get("deduction") or 0)
            self.labels[key] = label

    def copy(self) -> _Projection:
        clone = _Projection([])
        clone.points = dict(self.points)
        clone.labels = dict(self.labels)
        return clone

    def score(self) -> int:
        return _score_of(self.points)

    def has(self, key: str) -> bool:
        return self.points.get(key, 0) > 0

    def set(self, key: str, points: int) -> None:
        if points:
            self.points[key] = points
        else:
            self.points.pop(key, None)

    def fixed_since(self, before: Mapping[str, int]) -> list[str]:
        return [
            self.labels.get(key, key)
            for key, points in before.items()
            if self.points.get(key, 0) < points
        ]


def _score_of(points: Mapping[str, int]) -> int:
    return max(0, 100 - sum(points.values()))


@dataclass(frozen=True)
class _Part:
    start: date | None
    rest: tuple[tuple[str, str], ...]
    files: int
    below_target: int
    size: int
    open: bool


@dataclass(frozen=True)
class _Scale:
    """How far the scanned partition rows are from the whole table."""

    by_partitions: float
    by_size: float

    @classmethod
    def of(cls, snapshot: TableSnapshot, parts: Sequence[_Part]) -> _Scale:
        if not parts:
            return cls(1.0, 1.0)
        sample_size = sum(part.size for part in parts)
        by_partitions = max(1.0, snapshot.total_partitions / len(parts))
        by_size = max(1.0, snapshot.total_size_bytes / sample_size) if sample_size else 1.0
        return cls(by_partitions, by_size)

    @property
    def partial(self) -> bool:
        return self.by_partitions > 1.0 or self.by_size > 1.0

    @property
    def low(self) -> float:
        return min(self.by_partitions, self.by_size)


@dataclass(frozen=True)
class _Layout:
    partitions: int
    files: int
    skew: float
    small_files: int | None


def build_path_to_a(snapshot: TableSnapshot) -> PathToA:
    projection = _Projection(snapshot.deductions)
    notes: list[str] = []
    steps: list[Step] = []
    parts = _parts(snapshot)
    target = _target_bytes(snapshot)
    scale = _Scale.of(snapshot, parts)
    if parts and scale.partial:
        notes.append(
            f"Estimates use the {len(parts):,} partitions recorded by the last scan, "
            f"scaled to the table's {snapshot.total_partitions:,} partitions."
        )
    elif not parts and snapshot.total_partitions > 1:
        notes.append(
            "The last scan recorded no partition rows, so compaction and layout "
            "estimates are not available."
        )
    if snapshot.commits_per_day is None and projection.has("snapshot_count"):
        notes.append(
            "Cryo couldn't measure this table's commit rate from its scan history, so "
            "snapshot counts after expiry aren't projected."
        )

    def add(step: Step | None) -> None:
        if step is not None:
            steps.append(step)

    archived = is_archived(snapshot.lifecycle)
    if archived:
        notes.append(
            "This table is archived (read-only). Compaction, delete rewrites, layout and "
            "table-property steps are skipped; snapshot expiry, orphan cleanup and manifest "
            "rewrites still run."
        )
        for builder in (_expire_step, _manifest_step, _orphan_step):
            add(builder(snapshot, projection))
        add(_retention_step(snapshot, projection))
    else:
        for builder in (_expire_step, _manifest_step, _orphan_step, _delete_step):
            add(builder(snapshot, projection))
        add(_compaction_step(snapshot, projection, parts, target, scale, routine=True))
        add(_retention_step(snapshot, projection))
        add(_properties_step(snapshot, projection))
        add(_compaction_step(snapshot, projection, parts, target, scale, routine=False))
        add(_spec_migration_step(snapshot, projection))
        layout = _partition_step(snapshot, projection, parts, target, scale)
        add(layout)
        dropped = list(layout.action.get("dropped", [])) if layout and layout.action else []
        add(_sort_step(snapshot, projection, dropped))
        _mark_joint_property_fix(snapshot, steps)
    add(_archive_step(snapshot, projection))
    cryo_score = projection.score()
    steps.extend(_external_steps(snapshot, projection))
    return PathToA(
        score=snapshot.score,
        grade=snapshot.grade,
        deductions=[dict(d) for d in snapshot.deductions],
        steps=steps,
        cryo_score=cryo_score,
        cryo_grade=grade_for_score(cryo_score),
        reachable_score=projection.score(),
        reachable_grade=grade_for_score(projection.score()),
        notes=notes,
        design_findings=[dict(f) for f in compute_design_findings(_health_stats(snapshot))],
        next_orphan_cleanup_due=snapshot.next_orphan_cleanup_due,
    )


def _target_bytes(snapshot: TableSnapshot) -> int:
    explicit = snapshot.params.get("target_file_size_bytes")
    if explicit:
        return int(explicit)
    if snapshot.table_target_bytes and snapshot.params.get("use_table_target_size") is not False:
        return int(snapshot.table_target_bytes)
    return int(snapshot.params.get("file_size_mb", 256)) * MIB


def _time_field(fields: Sequence[PartitionFieldSpec]) -> PartitionFieldSpec | None:
    for spec in fields:
        if spec.transform in _TIME_TRANSFORMS:
            return spec
    return None


def _parts(snapshot: TableSnapshot) -> list[_Part]:
    time_field = _time_field(snapshot.partition_fields)
    time_keys = set(time_field.value_keys) if time_field else set()
    today = snapshot.now.date()
    out: list[_Part] = []
    for row in snapshot.partitions:
        values = row.get("partition_values")
        rest: tuple[tuple[str, str], ...] = ()
        if isinstance(values, Mapping):
            rest = tuple(sorted((str(k), str(v)) for k, v in values.items() if k not in time_keys))
        out.append(
            _Part(
                start=partition_date(time_field, row) if time_field else None,
                rest=rest,
                files=int(row.get("files_per_partition") or 0),
                below_target=int(row.get("small_files_in_partition_count") or 0),
                size=int(float(row.get("partition_size_gb") or 0) * GIB),
                open=is_open_time_partition(snapshot.partition_fields, row, today),
            )
        )
    return out


def _files_for(size: int, target: int) -> int:
    return max(1, math.ceil(size / target)) if size > 0 else 1


def _skew(files: Sequence[int]) -> float:
    if not files:
        return 0.0
    mid = median(files)
    return float(max(files) / mid) if mid else 0.0


def _projected_skew(snapshot: TableSnapshot, before: Sequence[int], after: Sequence[int]) -> float:
    """Scale the scanner's skew by how much the scanned rows' skew changes."""
    sample_before = _skew(before)
    if not snapshot.partition_skew_ratio or not sample_before:
        return _skew(after)
    return snapshot.partition_skew_ratio * _skew(after) / sample_before


def _apply_layout(snapshot: TableSnapshot, projection: _Projection, layout: _Layout) -> None:
    limits = _thresholds(
        snapshot, total_files=layout.files, total_partitions=layout.partitions or None
    )
    projection.set("total_files", points_for("total_files", layout.files, limits))
    if layout.partitions:
        excess = excess_files_per_partition(
            _health_stats(
                snapshot,
                total_files=layout.files,
                total_partitions=layout.partitions,
                avg_files_per_partition=layout.files / layout.partitions,
            )
        )
        projection.set(
            "avg_files_per_partition", points_for("avg_files_per_partition", excess, limits)
        )
    if layout.small_files is not None:
        projection.set(
            "small_file_pct", small_file_points(layout.small_files, layout.files, limits)
        )


def _plural(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {plural or noun + 's'}"


def _automatic(
    snapshot: TableSnapshot,
    projection: _Projection,
    before: Mapping[str, int],
    *,
    step_id: str,
    operation: str,
    title: str,
    detail: str,
    confidence: Confidence,
    evidence: list[str],
    runnable: bool = True,
) -> Step:
    scheduled = operation in snapshot.scheduled_operations
    when = (
        "In this table's workflow, so it runs on schedule."
        if scheduled
        else "Not in this table's workflow: run it now, or add it to the workflow."
    )
    return Step(
        id=step_id,
        kind="automatic",
        title=title,
        detail=f"{detail} {when}" if runnable else detail,
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence=confidence,
        evidence=evidence,
        action={"type": "run_operations", "operations": [operation]} if runnable else None,
        required_role="operator" if runnable else None,
        in_schedule=scheduled,
    )


def _expire_window_hours(snapshot: TableSnapshot) -> tuple[float, int]:
    params = _expire_params(
        snapshot.params,
        {
            "is_streaming": snapshot.is_streaming,
            "commits_per_day": snapshot.commits_per_day or 0.0,
            "total_snapshots": snapshot.total_snapshots,
        },
    )
    hours = params.get("retention_hours") or params["retention_days"] * 24
    return float(hours), int(params.get("retain_last", 1))


def _snapshots_kept(rate: float, hours: float, retain_last: int) -> int:
    return math.ceil(rate * hours / 24) + retain_last


def _window(hours: float) -> str:
    if hours >= 24 and hours % 24 == 0:
        return _plural(int(hours // 24), "day")
    return _plural(int(hours), "hour")


def _expire_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    if not projection.has("snapshot_count"):
        return None
    before = dict(projection.points)
    hours, retain_last = _expire_window_hours(snapshot)
    rate = snapshot.commits_per_day
    evidence = [_plural(snapshot.total_snapshots, "snapshot")]
    if rate is None:
        detail = f"Keeps the last {_window(hours)} of history."
        confidence: Confidence = "assumed"
    else:
        kept = min(snapshot.total_snapshots, _snapshots_kept(rate, hours, retain_last))
        projection.set("snapshot_count", points_for("snapshot_count", kept, _thresholds(snapshot)))
        detail = (
            f"Keeps the last {_window(hours)} of history. At {rate:.0f} commits/day that "
            f"leaves about {_plural(kept, 'snapshot')} (now {snapshot.total_snapshots:,})."
        )
        confidence = "estimated"
        evidence.append(f"{rate:.0f} commits/day")
    return _automatic(
        snapshot,
        projection,
        before,
        step_id="expire_snapshots",
        operation="expire_snapshots",
        title="Expire old snapshots",
        detail=detail,
        confidence=confidence,
        evidence=evidence,
    )


def _manifest_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    if not projection.has("manifest_count"):
        return None
    before = dict(projection.points)
    projection.set("manifest_count", 0)
    return _automatic(
        snapshot,
        projection,
        before,
        step_id="optimize_manifests",
        operation="optimize_manifests",
        title="Rewrite manifests",
        detail=(
            f"Merges the table's {snapshot.manifest_count:,} manifests. The count afterwards "
            "depends on how much metadata each partition carries, so the projection assumes "
            "it drops under the limit."
        ),
        confidence="assumed",
        evidence=[_plural(snapshot.manifest_count, "manifest")],
    )


def _orphan_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    if not projection.has("metadata_to_data_ratio"):
        return None
    before = dict(projection.points)
    projection.set("metadata_to_data_ratio", 0)
    return _automatic(
        snapshot,
        projection,
        before,
        step_id="remove_orphan_files",
        operation="remove_orphan_files",
        title="Remove orphan files",
        detail="Deletes files no snapshot references, which shrinks metadata overhead."
        + (
            f" Next orphan cleanup due {snapshot.next_orphan_cleanup_due}."
            if snapshot.next_orphan_cleanup_due
            else ""
        ),
        confidence="assumed",
        evidence=[],
    )


def _delete_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    if not (projection.has("position_deletes") or projection.has("equality_deletes")):
        return None
    before = dict(projection.points)
    projection.set("position_deletes", 0)
    projection.set("equality_deletes", 0)
    return _automatic(
        snapshot,
        projection,
        before,
        step_id="compact_deletes",
        operation="optimize",
        title="Fold delete files into data files",
        detail=(
            "Compaction rewrites the data files that delete files point at, so the "
            "delete files can be dropped."
        ),
        confidence="assumed",
        evidence=[],
    )


def _compaction_step(
    snapshot: TableSnapshot,
    projection: _Projection,
    parts: Sequence[_Part],
    target: int,
    scale: _Scale,
    *,
    routine: bool,
) -> Step | None:
    if not parts or not any(projection.has(key) for key in _FILE_KEYS):
        return None
    min_small = int(snapshot.params.get("min_small_files", 128)) if routine else 2
    skip_open = snapshot.params.get("skip_open_time_partition", True) is not False
    before_files = [part.files for part in parts]
    after_files: list[int] = []
    removed = compacted = small = 0
    for part in parts:
        if part.below_target >= min_small and not (skip_open and part.open):
            files = min(part.files, _files_for(part.size, target))
            compacted += 1
            removed += part.files - files
            small += 1 if part.size < HEALTH_SMALL_FILE_BYTES else 0
            after_files.append(files)
        else:
            after_files.append(part.files)
    target_mb = target // MIB
    if not compacted:
        if not routine:
            return None
        return _automatic(
            snapshot,
            projection,
            dict(projection.points),
            step_id="optimize",
            operation="optimize",
            title="Compact small files",
            detail=(
                f"No partition has {min_small}+ files under {target_mb} MB, so routine "
                "compaction has nothing to rewrite."
            ),
            confidence="measured",
            evidence=[f"{len(parts):,} partitions checked"],
            runnable=False,
        )
    before = dict(projection.points)
    partitions = max(snapshot.total_partitions, len(parts))
    files = max(partitions, math.ceil(snapshot.total_files - removed * scale.low))
    every_part = compacted == len(parts) and not scale.partial
    layout = _Layout(
        partitions=partitions,
        files=files,
        skew=_projected_skew(snapshot, before_files, after_files),
        small_files=small if every_part else None,
    )
    _apply_layout(snapshot, projection, layout)
    if not routine and projection.score() <= _score_of(before):
        projection.points = before
        return None
    evidence = [
        f"{compacted:,} of {len(parts):,} scanned partitions rewritten",
        f"about {files:,} files afterwards (now {snapshot.total_files:,})",
        f"skew about {layout.skew:.0f}x afterwards",
    ]
    if routine:
        return _automatic(
            snapshot,
            projection,
            before,
            step_id="optimize",
            operation="optimize",
            title="Compact small files",
            detail=(
                f"Rewrites partitions with {min_small}+ files under {target_mb} MB into "
                f"{target_mb} MB files. Partitions still taking writes are skipped."
            ),
            confidence="estimated",
            evidence=evidence,
        )
    return Step(
        id="optimize_all",
        kind="approval",
        title="Compact every fragmented partition",
        detail=(
            f"Lowers this table's min_small_files to 2 so compaction also rewrites partitions "
            f"with only a few files under {target_mb} MB. That is a larger rewrite, and Cryo "
            "keeps applying it on every scheduled run."
        ),
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="estimated",
        evidence=evidence,
        action={"type": "set_overrides", "overrides": {"min_small_files": 2}},
        required_role="admin",
    )


def _retention_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    rate = snapshot.commits_per_day
    if not projection.has("snapshot_count") or not rate:
        return None
    hours, retain_last = _expire_window_hours(snapshot)
    # The scorer's limit for this table. It is judged against the configured retention days,
    # which a retention_hours override doesn't change, so the limit holds as the window shrinks.
    limits = _thresholds(snapshot)
    limit = int(limits["snapshot_count_warn"]) - 1
    needed = math.floor((limit - retain_last) / rate * 24)
    if needed < 1 or needed >= hours:
        return None
    before = dict(projection.points)
    kept = _snapshots_kept(rate, needed, retain_last)
    projection.set("snapshot_count", points_for("snapshot_count", kept, limits))
    return Step(
        id="retention_hours",
        kind="approval",
        title=f"Keep {_window(needed)} of snapshot history",
        detail=(
            f"At {rate:.0f} commits/day, keeping {_window(hours)} of history still leaves "
            f"more than {limit} snapshots. A {needed}-hour window keeps about {kept}. "
            f"Time travel and rollback on this table then reach back {_window(needed)}. "
            "Cryo saves this as a table override and applies it on every run."
        ),
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="estimated",
        evidence=[f"current window {_window(hours)}", f"{rate:.0f} commits/day"],
        action={"type": "set_overrides", "overrides": {"retention_hours": needed}},
        required_role="admin",
    )


def _property_issues(
    snapshot: TableSnapshot, *, sort_order: bool | None = None, with_changes: bool = True
) -> int:
    """The scorer's property-gap count after the recommended changes (and a sort order).

    Delegates to the scorer so the two can't disagree: an unrecorded distribution mode is not
    a gap to the scorer, and counting it here kept the deduction in every projection (#520).
    """
    from cryo.scanner.property_recommendations import count_suboptimal_property_issues

    props = snapshot.property_changes if with_changes else {}
    return count_suboptimal_property_issues(
        {
            "write_distribution_mode": props.get("write.distribution-mode")
            or snapshot.write_distribution_mode,
            "has_sort_order": snapshot.has_sort_order if sort_order is None else sort_order,
            "target_file_size_bytes": props.get("write.target-file-size-bytes")
            or snapshot.table_target_bytes,
        }
    )


def _mark_joint_property_fix(snapshot: TableSnapshot, steps: list[Step]) -> None:
    """Say so when the property deduction clears only with both the properties and sort steps.

    The projection is cumulative, so the later sort step gets the credit and the properties
    step would otherwise read "clears no deduction" although it is half the fix.
    """
    by_id = {step.id: step for step in steps}
    properties, sort = by_id.get("table_properties"), by_id.get("sort_order")
    label = "Suboptimal properties"
    if properties is None or sort is None or label not in sort.fixes:
        return
    if not _property_issues(snapshot, sort_order=True, with_changes=False):
        return  # the sort order alone clears it
    properties.fixes_with = [label]
    properties.fixes_with_steps = [sort.title]
    sort.fixes = [f for f in sort.fixes if f != label]
    sort.fixes_with = [label]
    sort.fixes_with_steps = [properties.title]


def _properties_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    if not snapshot.property_changes:
        return None
    before = dict(projection.points)
    if projection.has("properties"):
        projection.set("properties", 8 if _property_issues(snapshot) else 0)
    changes = ", ".join(f"{k} = {v}" for k, v in sorted(snapshot.property_changes.items()))
    detail = f"Sets {changes}."
    if not snapshot.has_sort_order and projection.has("properties"):
        detail += " The property deduction also needs a sort order (see the sort step)."
    return Step(
        id="table_properties",
        kind="approval",
        title="Apply recommended table properties",
        detail=detail,
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="measured",
        action={"type": "apply_properties", "properties": dict(snapshot.property_changes)},
        required_role="operator",
    )


@dataclass(frozen=True)
class _LayoutOption:
    transform: PartitionTransform | None
    dropped: tuple[PartitionFieldSpec, ...]
    layout: _Layout
    groups: int
    score: int
    findings: Mapping[str, int] = field(default_factory=dict)

    @property
    def changes(self) -> int:
        return len(self.dropped) + (1 if self.transform else 0)

    def describe(self, time_field: PartitionFieldSpec | None) -> str:
        parts: list[str] = []
        if self.transform and time_field:
            parts.append(
                f"{time_field.transform.value}({time_field.base_column}) → "
                f"{self.transform.value}({time_field.base_column})"
            )
        parts.extend(f"drop {spec.base_column}" for spec in self.dropped)
        return ", ".join(parts)


def _period(start: date, transform: PartitionTransform) -> date:
    if transform is PartitionTransform.MONTH:
        return start.replace(day=1)
    if transform is PartitionTransform.YEAR:
        return start.replace(month=1, day=1)
    return start


def _option_layout(
    snapshot: TableSnapshot,
    parts: Sequence[_Part],
    transform: PartitionTransform | None,
    dropped: Sequence[PartitionFieldSpec],
    target: int,
    scale: _Scale,
) -> tuple[_Layout, int]:
    drop_keys = {key for spec in dropped for key in spec.value_keys}
    sizes: dict[tuple[Any, ...], int] = defaultdict(int)
    for index, part in enumerate(parts):
        if part.start is None and transform is not None:
            key: tuple[Any, ...] = ("unparsed", index)
        else:
            start = _period(part.start, transform) if part.start and transform else part.start
            key = (start, tuple(item for item in part.rest if item[0] not in drop_keys))
        sizes[key] += part.size
    group_files = [_files_for(size, target) for size in sizes.values()]
    partitions = min(snapshot.total_partitions, math.ceil(len(sizes) * scale.by_partitions))
    files = max(partitions, math.ceil(sum(group_files) * scale.by_size))
    small = sum(1 for size in sizes.values() if size < HEALTH_SMALL_FILE_BYTES)
    layout = _Layout(
        partitions=partitions,
        files=files,
        skew=_projected_skew(snapshot, [part.files for part in parts], group_files),
        small_files=math.ceil(small * scale.by_partitions),
    )
    return layout, len(sizes)


def _layout_options(
    snapshot: TableSnapshot,
    projection: _Projection,
    parts: Sequence[_Part],
    target: int,
    scale: _Scale,
) -> list[_LayoutOption]:
    time_field = _time_field(snapshot.partition_fields)
    transforms: list[PartitionTransform | None] = [None]
    if time_field and time_field.transform in _COARSER and any(part.start for part in parts):
        transforms.extend(_COARSER[time_field.transform])
    identity = [
        spec for spec in snapshot.partition_fields if spec.transform is PartitionTransform.IDENTITY
    ]
    drops: list[tuple[PartitionFieldSpec, ...]] = [(), *((spec,) for spec in identity)]
    if len(snapshot.partition_fields) - 1 <= 0:
        drops = [()]
    options: list[_LayoutOption] = []
    for transform in transforms:
        for dropped in drops:
            if transform is None and not dropped:
                continue
            if len(dropped) == len(snapshot.partition_fields):
                continue
            layout, groups = _option_layout(snapshot, parts, transform, dropped, target, scale)
            trial = projection.copy()
            _apply_layout(snapshot, trial, layout)
            findings = _design_labels(
                snapshot, total_partitions=layout.partitions, partition_skew_ratio=layout.skew
            )
            options.append(
                _LayoutOption(transform, dropped, layout, groups, trial.score(), findings)
            )
    return options


def _partition_step(
    snapshot: TableSnapshot,
    projection: _Projection,
    parts: Sequence[_Part],
    target: int,
    scale: _Scale,
) -> Step | None:
    current_findings = _design_labels(snapshot)
    if not parts or not (current_findings or any(projection.has(key) for key in _LAYOUT_KEYS)):
        return None
    options = _layout_options(snapshot, projection, parts, target, scale)
    if not options:
        return None
    # Since #285 partition count and skew are design findings, not deductions. A layout
    # change earns its place by raising the grade or by clearing a design finding.
    ranked = sorted(
        options,
        key=lambda o: (
            -o.score,
            sum(o.findings.values()),
            o.changes,
            len(o.dropped),
            _rank(o.transform),
        ),
    )
    best = ranked[0]
    # Like fixed_since for deductions: a finding counts as fixed if it goes or drops a level.
    cleared = [
        label for label, weight in current_findings.items() if best.findings.get(label, 0) < weight
    ]
    if best.score <= projection.score() and not cleared:
        return None
    before = dict(projection.points)
    _apply_layout(snapshot, projection, best.layout)
    time_field = _time_field(snapshot.partition_fields)
    title, detail = _layout_text(snapshot, best, time_field, parts, target)
    remove = [spec.partition_field or spec.base_column for spec in best.dropped]
    add: list[dict[str, str]] = []
    if best.transform and time_field:
        remove.append(time_field.partition_field or time_field.base_column)
        add.append({"source_column": time_field.base_column, "transform": best.transform.value})
    evidence = [
        f"{option.describe(time_field)}: about {option.layout.partitions:,} partitions, "
        f"{option.layout.files:,} files, skew about {option.layout.skew:.0f}x "
        f"(score {option.score}"
        + (f"; still {', '.join(option.findings).lower()}" if option.findings else "")
        + ")"
        for option in ranked[:4]
    ]
    return Step(
        id="partition_spec",
        kind="design",
        title=title,
        detail=detail,
        fixes=[*projection.fixed_since(before), *cleared],
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="estimated",
        evidence=evidence,
        action={
            "type": "partition_spec",
            "remove": remove,
            "add": add,
            "dropped": [spec.base_column for spec in best.dropped],
        },
        required_role="admin",
        confirm_text=snapshot.table_name,
    )


def _rank(transform: PartitionTransform | None) -> int:
    if transform is PartitionTransform.MONTH:
        return 1
    if transform is PartitionTransform.YEAR:
        return 2
    return 0


def _layout_text(
    snapshot: TableSnapshot,
    option: _LayoutOption,
    time_field: PartitionFieldSpec | None,
    parts: Sequence[_Part],
    target: int,
) -> tuple[str, str]:
    changes: list[str] = []
    if option.transform and time_field:
        changes.append(
            f"partition by {option.transform.value} of {time_field.base_column} instead of "
            f"{time_field.transform.value}"
        )
    changes.extend(f"stop partitioning by {spec.base_column}" for spec in option.dropped)
    title = "; ".join(changes)
    title = title[0].upper() + title[1:]

    median_mb = median(part.size for part in parts) / MIB
    sentences = []
    if median_mb < target / MIB:
        sentences.append(
            f"The median partition holds {median_mb:,.0f} MB, less than one {target // MIB} MB "
            "file, so compaction alone can't bring the file and partition counts down."
        )
    else:
        sentences.append("The table has more partitions than its data volume needs.")
    for spec in option.dropped:
        sentences.append(
            f"Partition sizes vary most across {spec.base_column} values. Without it, rows "
            f"for each {spec.base_column} stay together through a sort order (the next step), "
            "so filters on it still skip files."
        )
    columns = [time_field.base_column] if option.transform and time_field else []
    columns += [spec.base_column for spec in option.dropped]
    for column in columns:
        filters = int(snapshot.query_filters.get(column, 0))
        if filters:
            queries = _plural(filters, "recorded query", "recorded queries")
            sentences.append(f"{queries} filter on {column}.")
    sentences.append(
        "New writes use the new spec at once. Routine compaction leaves existing data in the "
        "old layout, so this projection holds only after the old partitions are migrated: "
        "Migrate to current spec on the Maintain tab rewrites them, on a Spark destination "
        "and after an approval."
    )
    return title, " ".join(sentences)


def _spec_migration_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    """Old-spec partitions left over from a partition spec change that already happened.

    No score change is projected: the scorer reads table totals, and how many partitions and
    files the old data folds into depends on the new spec's layout of it.
    """
    partitions = int(snapshot.spec_migration.get("old_spec_partitions") or 0)
    if partitions <= 0:
        return None
    files = int(snapshot.spec_migration.get("old_spec_files") or 0)
    size_gb = int(snapshot.spec_migration.get("old_spec_bytes") or 0) / GIB
    default_spec_id = snapshot.spec_migration.get("default_spec_id")
    return Step(
        id="migrate_partition_spec",
        kind="approval",
        title=(
            f"Migrate {_plural(partitions, 'partition')} still written under an older "
            "partition spec"
        ),
        detail=(
            f"{_plural(files, 'data file')} ({size_gb:,.1f} GB) keep the layout they were "
            f"written with, not spec {default_spec_id}. Routine compaction picks partitions by "
            "small files and leaves well-sized old-spec files where they are. Migrate to "
            "current spec on the Maintain tab rewrites them in chunks, on a Spark destination "
            "and after an approval."
        ),
        fixes=[],
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="measured",
        evidence=[
            f"Last scan: {_plural(partitions, 'partition')}, {_plural(files, 'data file')}, "
            f"{size_gb:,.1f} GB under an older spec."
        ],
    )


def _sort_columns(snapshot: TableSnapshot, dropped: Sequence[str]) -> list[str]:
    if dropped:
        return list(dropped)[:3]
    if snapshot.sort_columns:
        return list(snapshot.sort_columns)[:3]
    partition_columns = {spec.base_column for spec in snapshot.partition_fields}
    ranked = sorted(snapshot.query_filters.items(), key=lambda item: (-item[1], item[0]))
    for column, count in ranked:
        if count > 0 and column not in partition_columns:
            return [column]
    time_field = _time_field(snapshot.partition_fields)
    return [time_field.base_column] if time_field else []


def _sort_step(
    snapshot: TableSnapshot, projection: _Projection, dropped: Sequence[str]
) -> Step | None:
    needed_for_score = not snapshot.has_sort_order and projection.has("properties")
    if not dropped and not needed_for_score:
        return None
    columns = _sort_columns(snapshot, dropped)
    if not columns:
        return None
    before = dict(projection.points)
    if needed_for_score and not _property_issues(snapshot, sort_order=True):
        projection.set("properties", 0)
    if dropped:
        reason = "Keeps pruning on the columns the partition change stops partitioning by."
    elif snapshot.sort_columns:
        reason = "Columns come from the table's layout recommendation."
    else:
        reason = "Columns come from recorded query filters."
    replaces = " This replaces the table's current sort order." if snapshot.has_sort_order else ""
    return Step(
        id="sort_order",
        kind="design",
        title=f"Sort data by {', '.join(columns)}",
        detail=(
            f"Writers and compaction sort rows on write, so files carry tight min/max ranges "
            f"and queries skip more files. {reason}{replaces}"
        ),
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="measured",
        action={"type": "sort_order", "columns": columns},
        required_role="admin",
        confirm_text=snapshot.table_name,
    )


def _archive_step(snapshot: TableSnapshot, projection: _Projection) -> Step | None:
    """Replace "write to it or retire it" with an in-app action (#519).

    Archiving removes only the stale-commit deduction; the weights are unchanged.
    """
    if not projection.has("snapshot_age_days") or is_archived(snapshot.lifecycle):
        return None
    last = snapshot.last_commit_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    days = (snapshot.now - last).days if last is not None else None
    before = dict(projection.points)
    projection.set("snapshot_age_days", 0)
    evidence: list[str] = []
    if days is not None:
        evidence.append(f"Last commit {days} days ago.")
    if snapshot.query_count_30d is not None:
        evidence.append(f"{snapshot.query_count_30d:,} recorded reads in the last 30 days.")
    candidate = is_archive_candidate(
        lifecycle=snapshot.lifecycle,
        days_since_commit=days,
        query_count_30d=snapshot.query_count_30d,
    )
    lead = ""
    if candidate:
        reads = (
            f" and no reads in {ARCHIVE_CANDIDATE_NO_READ_DAYS} days"
            if snapshot.query_count_30d is not None
            else ""
        )
        lead = (
            f"Archive candidate: no commits for {ARCHIVE_CANDIDATE_NO_COMMIT_DAYS}+ days{reads}. "
        )
    return Step(
        id="mark_archived",
        kind="approval",
        title="Mark as archived",
        detail=(
            f"{lead}If this is a backup or a retired table, mark it archived (read-only). "
            "The stale-commit check stops applying, compaction and layout changes stop, and "
            "snapshot expiry and orphan cleanup keep running. If the table is still written "
            "to, leave it active: this clears on its next write. Return it to active from "
            "the Config tab at any time."
        ),
        fixes=projection.fixed_since(before),
        projected_score=projection.score(),
        projected_grade=grade_for_score(projection.score()),
        confidence="measured",
        evidence=evidence,
        action={"type": "set_lifecycle", "lifecycle": "archived", "suggested": candidate},
        required_role="admin",
        confirm_text=snapshot.table_name,
    )


def _external_steps(snapshot: TableSnapshot, projection: _Projection) -> list[Step]:
    steps: list[Step] = []

    def add(key: str, step_id: str, title: str, detail: str) -> None:
        if not projection.has(key):
            return
        before = dict(projection.points)
        projection.set(key, 0)
        steps.append(
            Step(
                id=step_id,
                kind="external",
                title=title,
                detail=detail,
                fixes=projection.fixed_since(before),
                projected_score=projection.score(),
                projected_grade=grade_for_score(projection.score()),
                confidence="measured",
            )
        )

    rate = snapshot.commits_per_day
    if rate:
        snapshot_detail = (
            f"At {rate:.0f} commits/day even a one-hour history window keeps more snapshots "
            "than the limit. Batch the writer's commits (a longer trigger interval or larger "
            "micro-batches)."
        )
    else:
        snapshot_detail = (
            "Expiry is not projected to clear this without a measured commit rate. If the "
            "count stays high after expiry runs, the writer is committing faster than the "
            "window clears; batch its commits."
        )
    add("snapshot_count", "batch_commits", "Commit less often", snapshot_detail)
    add(
        "streaming",
        "streaming_writer",
        "Slow the writer's commit rate",
        "The table is graded down for streaming-rate commits. A longer trigger interval on "
        "the writer removes this deduction; Cryo can't change the writer.",
    )
    add(
        "v3_upgrade",
        "format_v3",
        "Upgrade to Iceberg format v3",
        "Upgrade from the table's Config tab once every reader and writer supports v3.",
    )
    add(
        "file_formats",
        "legacy_formats",
        "Rewrite non-Parquet files",
        "Some data files use a format other than Parquet or ORC. Point writers at Parquet; "
        "compaction then rewrites the old files.",
    )
    for key in sorted(k for k in projection.points if k.startswith("other:")):
        label = key.removeprefix("other:")
        add(key, f"other_{len(steps)}", label, "No automated fix is known for this deduction.")
    return steps
