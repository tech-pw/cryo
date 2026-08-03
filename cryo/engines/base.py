from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan

# Regression, 2026-07-27: a Trino GENERIC_INTERNAL_ERROR on reports.gold_dbt_video_stats_reports_unified
# (a 1.5 TB, 44k-file table) serialized its full DataFileWithDeleteFiles split list into the
# exception message -- str(exc) alone landed a 2,962,267-character error in MaintenanceJob.error.
# Every engine's except-block must cap this before it reaches ExecResult.error, or one adversarial
# upstream error message bloats the jobs table row, the audit log, every API response that
# includes it, and the log line right next to it.
ERROR_MESSAGE_TRUNCATE = 4000


def truncate_error_message(
    message: str, *, limit: int = ERROR_MESSAGE_TRUNCATE, tail: int = 500
) -> str:
    """Cap an engine error message so one runaway exception can't bloat storage/API responses.

    Keeps both the head and the tail, not just the head. A single Trino exception puts its
    root cause up front, so head-only truncation is fine there -- but a Spark-on-K8s job's
    error is often the tail of a whole stdout dump (INFO-level executor/block-manager
    registration chatter first, the real exception and stack trace last). Head-only truncation
    silently hid the actual cause behind megabytes of boilerplate; confirmed live, 2026-07-28,
    on a Spark remove_orphan_files ClassNotFoundException that the stored error never showed.
    `tail` is skipped (falls back to head-only truncation, matching pre-2026-07-28 behavior)
    when it would leave too little room for the head to be useful.
    """
    if len(message) <= limit:
        return message
    total = len(message)
    if tail <= 0 or tail >= limit:
        return f"{message[:limit]}... [truncated, {total:,} chars total]"
    head = message[: limit - tail]
    return f"{head}\n...[truncated, {total:,} chars total, tail below]...\n{message[-tail:]}"


# Signatures each engine/catalog uses when a table Cryo still thinks is live has actually been
# dropped out from under it. Confirmed live, 2026-07-31: a bulk maintenance run against tables
# with no writes in 60+ days hit two of these -- the table had been dropped from the catalog
# sometime after Cryo's last scan, but Cryo kept scheduling (and failing) maintenance against it
# indefinitely since nothing ever reconciled `Table.is_active` against real catalog state.
#
# TABLE_NOT_FOUND_MARKER is a second layer: a driver's raw log tail is a full stack trace that
# can run past 15KB, with "Caused by: NoSuchTableException" often well past the ~4000-char
# head+tail window truncate_error_message keeps -- confirmed live the same day, the signature
# was present in the untruncated log but never reached the stored error. An engine that has the
# raw, untruncated text should check it with this function *before* truncating and, if positive,
# prepend TABLE_NOT_FOUND_MARKER so head-first truncation always preserves a detectable signature
# regardless of where the original one landed.
TABLE_NOT_FOUND_MARKER = "[table not found in catalog]"
_TABLE_NOT_FOUND_SIGNATURES = (
    "NoSuchTableException",
    "TABLE_OR_VIEW_NOT_FOUND",
    "TableNotFoundException",
    TABLE_NOT_FOUND_MARKER,
)

# Confirmed live, 2026-08-03: a native-engine cold-table sweep hit a different flavor of the
# same "table effectively gone" problem -- the catalog's current pointer references a
# metadata.json or manifest file that no longer exists in S3 (NoSuchKey), rather than the
# catalog lacking the table entry entirely. Iceberg can't load any state from a missing
# metadata.json/manifest, so there's nothing a maintenance retry can fix -- same practical
# outcome as NoSuchTableException. Matched narrowly (NoSuchKey plus a metadata/manifest file
# reference) so a missing *data* file (the phantom-manifest-entry class of bug, fixable via
# repair) doesn't get misclassified as an unrecoverable dead table.
_CATALOG_METADATA_MISSING_PATTERNS = (
    ("NoSuchKey", "metadata.json"),
    ("NoSuchKey", "could not open manifest file"),
)


def is_table_not_found_error(message: str | None) -> bool:
    """True when an engine failure means the table no longer exists in the catalog (dropped
    externally), as opposed to a transient/infra failure that a retry might resolve."""
    if not message:
        return False
    if any(sig in message for sig in _TABLE_NOT_FOUND_SIGNATURES):
        return True
    return any(
        all(part in message for part in pattern) for pattern in _CATALOG_METADATA_MISSING_PATTERNS
    )


@dataclass(frozen=True)
class RenderedStatement:
    action: OperationType
    sql: str
    engine: EngineType
    metadata: dict[str, object] = field(default_factory=dict)
    payload: dict[str, object] = field(default_factory=dict)


ProgressCallback = Callable[[dict[str, Any]], None]
EngineRefCallback = Callable[[str], None]


@dataclass(frozen=True)
class ExecCtx:
    job_id: UUID | None = None
    table_id: UUID | None = None
    replica: str | None = None
    run_id: str | None = None
    on_progress: ProgressCallback | None = None
    on_engine_ref: EngineRefCallback | None = None
    statement_index: int | None = None
    session_factory: Any = None
    chunk_checkpointing: bool = False


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    engine_ref: str | None = None
    detail: str | None = None
    rows: tuple[tuple[object, ...], ...] = ()
    error: str | None = None
    duration_ms: int | None = None
    input_files_count: int | None = None
    output_files_count: int | None = None
    actual_bytes_freed: int | None = None
    snapshots_expired: int | None = None
    manifests_rewritten: int | None = None
    skipped: bool = False


class MaintenanceEngine(Protocol):
    type: EngineType

    def probe_capacity(self) -> CapacitySnapshot: ...

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...

    def session_setup(self) -> list[str]: ...

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult: ...

    def execute_plan(
        self, plan: MaintenancePlan, ctx: ExecCtx | None = None
    ) -> list[ExecResult]: ...
