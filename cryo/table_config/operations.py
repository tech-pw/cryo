"""Per-table maintenance operation control: what each operation does, what excluding it
costs, and whether it would run for this table today."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cryo.domain.enums import Mode, OperationType
from cryo.planner.guardrails import CORRECT_ORDER
from cryo.table_config.risk import Risk

OperationPhase = Literal["compaction", "cleanup"]
OperationEngine = Literal["trino", "spark", "native"]
OperationStatus = Literal[
    "excluded",
    "held",
    "blocked",
    "not_in_mode",
    "off",
    "when_needed",
    "runs",
    "not_scheduled",
    "unsupported",
]

_ICEBERG_MAINTENANCE = "https://iceberg.apache.org/docs/latest/maintenance/"
_SPARK_PROCEDURES = "https://iceberg.apache.org/docs/latest/spark-procedures/"


@dataclass(frozen=True, slots=True)
class OperationSpec:
    operation: OperationType
    label: str
    phase: OperationPhase
    summary: str
    when_planned: str
    exclude_consequence: str
    exclude_risk: Risk
    reasons_to_exclude: tuple[str, ...]
    engines: tuple[tuple[OperationEngine, str], ...]
    doc_url: str
    companion_hint: str | None = None
    companion_operation: OperationType | None = None


OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        operation=OperationType.CONVERT_DELETES,
        label="Apply delete files",
        phase="compaction",
        summary=(
            "Rewrites data files that carry merge-on-read delete files, folding the deletes "
            "into new data files so readers stop merging them at query time."
        ),
        when_planned=(
            "Standard and deep modes, when the table has delete files and their share passes "
            "convert_deletes_threshold_pct (default 5%), and no optimize already rewrites "
            "the same data."
        ),
        exclude_consequence=(
            "Delete files keep piling up and every read pays to merge them, so queries on "
            "tables with frequent updates or deletes get slower over time."
        ),
        exclude_risk="medium",
        reasons_to_exclude=(
            "The writer (for example a Flink or Spark job) already compacts its own deletes.",
            "Data rewrites on this table must happen only in an owner-controlled window.",
        ),
        engines=(
            ("spark", "rewrite_data_files with a delete-file threshold."),
            ("trino", "Runs as ALTER TABLE ... EXECUTE optimize, which rewrites data files."),
        ),
        doc_url=f"{_SPARK_PROCEDURES}#rewrite_data_files",
    ),
    OperationSpec(
        operation=OperationType.REWRITE_POSITION_DELETES,
        label="Merge position deletes",
        phase="compaction",
        summary=(
            "Merges many small position-delete files into fewer ones without rewriting the "
            "data files. Cheaper than a full rewrite."
        ),
        when_planned=(
            "Standard and deep modes, when position-delete files pile up but are below the "
            "threshold for a full data rewrite."
        ),
        exclude_consequence=(
            "Small position-delete files accumulate; readers open more files per scan until "
            "the next full rewrite folds them in."
        ),
        exclude_risk="low",
        reasons_to_exclude=(
            "The table runs on a Trino destination, which cannot run this procedure.",
            "Another job already maintains this table's delete files.",
        ),
        engines=(
            ("spark", "CALL system.rewrite_position_delete_files."),
            ("trino", "Not supported; Cryo fails the operation instead of running a rewrite."),
        ),
        doc_url=f"{_SPARK_PROCEDURES}#rewrite_position_delete_files",
    ),
    OperationSpec(
        operation=OperationType.OPTIMIZE,
        label="Compact small files",
        phase="compaction",
        summary=(
            "Rewrites small data files into files near the target size, partition by "
            "partition, within Cryo's GB budget per run."
        ),
        when_planned=(
            "Standard and deep modes, when the last scan found partitions with enough small "
            "files (min_small_files). Large tables are compacted in budgeted chunks."
        ),
        exclude_consequence=(
            "Small files keep accumulating, so query planning and scans get slower and S3 "
            "request costs rise. Streaming and frequently appended tables degrade fastest."
        ),
        exclude_risk="medium",
        reasons_to_exclude=(
            "The table is fully rewritten by its own pipeline on every load.",
            "The writer already produces well-sized files or runs its own compaction.",
            "Data rewrites on this table must happen only in an owner-controlled window.",
        ),
        engines=(
            ("trino", "ALTER TABLE ... EXECUTE optimize(file_size_threshold => ...)."),
            ("spark", "CALL system.rewrite_data_files (bin-pack or sort)."),
            ("native", "Cryo's Go compaction engine."),
        ),
        doc_url=f"{_ICEBERG_MAINTENANCE}#compact-data-files",
        companion_hint=(
            "Apply delete files also rewrites data files, and on Trino it runs as an OPTIMIZE. "
            "Exclude it too to stop every data rewrite on this table."
        ),
        companion_operation=OperationType.CONVERT_DELETES,
    ),
    OperationSpec(
        operation=OperationType.COMPUTE_STATISTICS,
        label="Column statistics",
        phase="compaction",
        summary=(
            "Computes column NDV statistics into a Puffin file so cost-based optimizers can "
            "pick better join orders."
        ),
        when_planned=(
            "Only right after an optimize in the same run, and only when the preset enables "
            "compute_statistics with NDV or bloom-filter columns."
        ),
        exclude_consequence=(
            "Statistics go stale as data changes, so engines plan joins on old estimates. "
            "Data and storage are unaffected."
        ),
        exclude_risk="low",
        reasons_to_exclude=(
            "The table is never joined, so the full-column scan is wasted work.",
            "Another job already runs ANALYZE on this table.",
        ),
        engines=(
            ("trino", "ANALYZE, a full scan of the analysed columns."),
            ("spark", "CALL system.compute_table_stats."),
        ),
        doc_url=f"{_SPARK_PROCEDURES}#compute_table_stats",
    ),
    OperationSpec(
        operation=OperationType.OPTIMIZE_MANIFESTS,
        label="Rewrite manifests",
        phase="cleanup",
        summary=(
            "Rewrites the table's manifest files so they line up with partitions, which makes "
            "query planning read fewer manifests."
        ),
        when_planned="Deep and manifest-only modes, on every run.",
        exclude_consequence=(
            "Manifests stay fragmented, so planning gets slower on tables with many commits. "
            "Data is unaffected."
        ),
        exclude_risk="low",
        reasons_to_exclude=(
            "The writer already merges manifests on commit (commit.manifest-merge.enabled).",
        ),
        engines=(
            ("trino", "ALTER TABLE ... EXECUTE optimize_manifests."),
            ("spark", "CALL system.rewrite_manifests."),
        ),
        doc_url=f"{_ICEBERG_MAINTENANCE}#rewrite-manifests",
    ),
    OperationSpec(
        operation=OperationType.EXPIRE_DATA,
        label="Expire data",
        phase="cleanup",
        summary=(
            "Deletes partitions or rows older than a retention cutoff. The default cutoff "
            "is measured from the last commit, so a stalled pipeline does not wipe its tail."
        ),
        when_planned=(
            "Light, standard and deep modes, only when the preset sets expire_data_enabled, "
            "a field, and a retention such as 7d."
        ),
        exclude_consequence=(
            "Old partitions stay in the table and keep costing storage and scan time. "
            "Snapshot expiry does not remove live data."
        ),
        exclude_risk="high",
        reasons_to_exclude=(
            "Downstream jobs still read partitions older than the retention window.",
            "The timestamp field is not the partition column and a row delete is too expensive.",
        ),
        engines=(
            ("trino", "DELETE FROM, metadata-only when the predicate matches the partition spec."),
            ("spark", "DELETE FROM, metadata-only when the predicate matches the partition spec."),
        ),
        doc_url=f"{_ICEBERG_MAINTENANCE}#delete",
    ),
    OperationSpec(
        operation=OperationType.EXPIRE_SNAPSHOTS,
        label="Expire snapshots",
        phase="cleanup",
        summary=(
            "Removes snapshots older than the retention window and deletes the data and "
            "metadata files only they referenced. Snapshots you protect in Cryo are kept."
        ),
        when_planned="Light, standard and deep modes, on every run.",
        exclude_consequence=(
            "Snapshots and their files are never removed, so storage grows with every commit, "
            "compaction stops freeing space, and metadata keeps growing. Deleted rows stay "
            "recoverable through time travel, which may conflict with data-retention rules."
        ),
        exclude_risk="high",
        reasons_to_exclude=(
            "Consumers need time travel beyond any retention you can configure.",
            "A legal or audit hold requires keeping every snapshot.",
        ),
        engines=(
            ("trino", "ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => ...)."),
            ("spark", "CALL system.expire_snapshots."),
        ),
        doc_url=f"{_ICEBERG_MAINTENANCE}#expire-snapshots",
    ),
    OperationSpec(
        operation=OperationType.REMOVE_ORPHAN_FILES,
        label="Remove orphan files",
        phase="cleanup",
        summary=(
            "Lists the table location and deletes files no snapshot references, such as "
            "leftovers from failed writes, once they are older than the retention threshold."
        ),
        when_planned="Deep mode, on every run.",
        exclude_consequence=(
            "Files from failed or aborted writes stay in S3 and keep costing storage. "
            "Queries are unaffected."
        ),
        exclude_risk="low",
        reasons_to_exclude=(
            "Another table or tool writes under this table's location.",
            "Writers run jobs longer than the orphan retention threshold.",
        ),
        engines=(
            ("trino", "ALTER TABLE ... EXECUTE remove_orphan_files(retention_threshold => ...)."),
            ("spark", "CALL system.remove_orphan_files."),
        ),
        doc_url=f"{_ICEBERG_MAINTENANCE}#delete-orphan-files",
    ),
)

SPECS: dict[str, OperationSpec] = {spec.operation.value: spec for spec in OPERATIONS}

_MODE_OPERATIONS: dict[Mode, frozenset[OperationType]] = {
    Mode.LIGHT: frozenset({OperationType.EXPIRE_SNAPSHOTS, OperationType.EXPIRE_DATA}),
    Mode.STANDARD: frozenset(
        {
            OperationType.EXPIRE_SNAPSHOTS,
            OperationType.EXPIRE_DATA,
            OperationType.OPTIMIZE,
            OperationType.CONVERT_DELETES,
            OperationType.REWRITE_POSITION_DELETES,
            OperationType.COMPUTE_STATISTICS,
        }
    ),
    Mode.DEEP: frozenset(CORRECT_ORDER),
    Mode.MANIFEST_ONLY: frozenset({OperationType.OPTIMIZE_MANIFESTS}),
}
_WHEN_NEEDED: dict[OperationType, str] = {
    OperationType.OPTIMIZE: "Runs when the last scan finds partitions with enough small files.",
    OperationType.CONVERT_DELETES: (
        "Runs when delete files pass the threshold and no optimize already rewrites them."
    ),
    OperationType.REWRITE_POSITION_DELETES: (
        "Runs when position deletes pile up but stay below the full-rewrite threshold."
    ),
    OperationType.COMPUTE_STATISTICS: "Runs right after an optimize in the same run.",
}


@dataclass(frozen=True, slots=True)
class OperationState:
    status: OperationStatus
    detail: str


@dataclass(frozen=True, slots=True)
class TableOperationFacts:
    mode: Mode | None
    preset_name: str | None
    params: Mapping[str, Any]
    excluded: Collection[str]
    held: bool
    gc_disabled: bool
    shared_location: bool
    engine: str | None = None


def operation_state(spec: OperationSpec, facts: TableOperationFacts) -> OperationState:
    op = spec.operation
    if op.value in facts.excluded:
        return OperationState("excluded", "Excluded for this table. No run plans it.")
    if facts.held:
        return OperationState(
            "held", "The whole table is on hold (Runs → Holds), so nothing runs on it."
        )
    if facts.gc_disabled and op in {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
    }:
        return OperationState(
            "blocked", "Blocked: the table sets gc.enabled=false, which forbids deleting files."
        )
    if facts.shared_location and op is OperationType.REMOVE_ORPHAN_FILES:
        return OperationState(
            "blocked",
            "Blocked: the table's S3 location overlaps another table's, so deleting "
            "unreferenced files could delete that table's data.",
        )
    if facts.mode is None:
        return OperationState(
            "not_scheduled",
            "No preset schedules this table, so this runs only in runs started for it.",
        )
    if op not in _MODE_OPERATIONS[facts.mode]:
        return OperationState(
            "not_in_mode",
            f"Not planned in {facts.mode.value} mode, which preset {facts.preset_name} uses.",
        )
    if op is OperationType.EXPIRE_DATA and not bool(facts.params.get("expire_data_enabled")):
        return OperationState("off", f"Preset {facts.preset_name} does not enable expire_data.")
    if op is OperationType.COMPUTE_STATISTICS and not bool(
        facts.params.get("compute_statistics", False)
    ):
        return OperationState(
            "off", f"Preset {facts.preset_name} does not enable compute_statistics."
        )
    if op is OperationType.REWRITE_POSITION_DELETES and (facts.engine or "").lower() == "trino":
        return OperationState(
            "unsupported",
            f"Preset {facts.preset_name} runs on Trino, which has no procedure for this, so the "
            "planner skips it and the plan notes why. Route the table to Spark to run it.",
        )
    if op in _WHEN_NEEDED:
        return OperationState("when_needed", _WHEN_NEEDED[op])
    return OperationState("runs", f"Runs on every {facts.preset_name} run.")


def preset_mode(value: Any) -> Mode | None:
    if value is None:
        return None
    try:
        return Mode(str(value))
    except ValueError:
        return None
