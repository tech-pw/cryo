"""What Cryo recommends for each setting, and why, from this table's own scan signals.

A recommendation is only made when the signal is unambiguous. Settings that depend on the
owner's intent (GC, retention, row-level mode) get a contextual note instead of a value.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryo.table_config.context import (
    ConfigContext,
    current_value,
    effective,
    is_true,
    parse_int,
)
from cryo.table_config.risk import format_bytes
from cryo.table_config.settings import SETTINGS_BY_KEY

_HIGH_COMMITS_PER_DAY = 24.0
_METADATA_BUILDUP_SNAPSHOTS = 50
_METADATA_BUILDUP_MANIFESTS = 100
_HOT_TABLE_RETRIES = 10
_HOT_TABLE_PREVIOUS_VERSIONS = 10


@dataclass(frozen=True)
class Advice:
    recommended: str | None = None
    reason: str | None = None
    note: str | None = None


def advise(key: str, ctx: ConfigContext) -> Advice:
    handler = _HANDLERS.get(key)
    return handler(ctx) if handler else Advice()


def _current(key: str, ctx: ConfigContext) -> str | None:
    spec = SETTINGS_BY_KEY[key]
    value, _ = current_value(spec, ctx)
    return effective(spec, value)


def _commits_per_day(ctx: ConfigContext) -> float:
    try:
        return float(ctx.stats.get("commits_per_day") or 0)
    except (TypeError, ValueError):
        return 0.0


def _hot(ctx: ConfigContext) -> bool:
    return bool(ctx.stats.get("is_streaming")) or _commits_per_day(ctx) >= _HIGH_COMMITS_PER_DAY


def _metadata_buildup(ctx: ConfigContext) -> bool:
    snapshots = int(ctx.stats.get("total_snapshots") or 0)
    manifests = int(ctx.stats.get("manifest_count") or 0)
    return (
        _hot(ctx)
        or snapshots >= _METADATA_BUILDUP_SNAPSHOTS
        or manifests >= _METADATA_BUILDUP_MANIFESTS
    )


def _target_size(ctx: ConfigContext) -> Advice:
    target = ctx.cryo_target_bytes
    if target is None:
        return Advice(note="No Cryo preset governs this table, so there is no compaction target.")
    reason = (
        f"Matches the {format_bytes(target)} Cryo compacts toward under preset "
        f"{ctx.cryo_preset_name}. A smaller write target leaves new files below that size, so "
        "every run compacts them again."
    )
    note = None
    if ctx.cryo_persists_layout:
        note = f"Cryo's {ctx.cryo_engine} compaction writes this value after every optimize."
    return Advice(str(target), reason, note)


def _distribution(ctx: ConfigContext) -> Advice:
    if not ctx.partitioned:
        return Advice(
            note="Unpartitioned table: distribution mode has little effect on file count."
        )
    current = _current("write.distribution-mode", ctx)
    if current in {None, "none"}:
        return Advice(
            "hash",
            "Partitioned table without an explicit shuffle. hash groups each partition's rows "
            "into one writer, so every engine (Flink included) writes fewer, larger files.",
        )
    if current == "hash":
        return Advice(
            "hash",
            "Rows are shuffled by partition before writing, so each partition gets few, "
            "large files.",
        )
    return Advice(note="range also clusters rows by partition; keep it if the table is sorted.")


def _codec(ctx: ConfigContext) -> Advice:
    if "write.parquet.compression-codec" not in ctx.properties:
        return Advice(
            "zstd",
            "Not set on the table: Iceberg 1.4+ writers use zstd but older writers fall back to "
            "gzip. Setting it makes every engine write the same codec.",
        )
    current = str(ctx.properties.get("write.parquet.compression-codec") or "").lower()
    if current and current != "zstd":
        return Advice(
            "zstd",
            f"zstd usually compresses better than {current} at similar read cost; it is the "
            "Iceberg and Trino default.",
        )
    return Advice("zstd", "Set explicitly, so every engine writes the same codec.")


def _format(ctx: ConfigContext) -> Advice:
    return Advice(
        "parquet",
        "Cryo's bloom-filter, row-group and column-statistics analysis only reads Parquet.",
    )


def _granularity(ctx: ConfigContext) -> Advice:
    if not ctx.merge_on_read_in_use or (ctx.format_version or 2) >= 3:
        return Advice()
    return Advice(
        "file",
        "This table writes merge-on-read deletes. File-scoped deletes are read only with "
        "their data file and disappear when that file is compacted.",
    )


def _row_mode_note(ctx: ConfigContext) -> Advice:
    delete_pct = float(ctx.stats.get("delete_file_pct") or 0)
    if ctx.format_version == 1:
        return Advice(note="Format v1 supports copy-on-write only.")
    if delete_pct > 0:
        return Advice(
            note=(
                f"{delete_pct:.1f}% of files are delete files. Merge-on-read suits frequent small "
                "updates; copy-on-write suits read-heavy tables that change rarely."
            )
        )
    return Advice(
        note=(
            "No recommendation: merge-on-read suits frequent small updates, copy-on-write suits "
            "read-heavy tables that change rarely."
        )
    )


def _retries(ctx: ConfigContext) -> Advice:
    if not _hot(ctx):
        return Advice()
    current = parse_int(_current("commit.retry.num-retries", ctx)) or 0
    if current < _HOT_TABLE_RETRIES:
        return Advice(
            str(_HOT_TABLE_RETRIES),
            f"{_commits_per_day(ctx):.0f} commits a day: concurrent writers and Cryo's own "
            "compaction often race, and 4 retries is not enough headroom.",
        )
    return Advice()


def _manifest_merge(ctx: ConfigContext) -> Advice:
    return Advice(
        "true",
        "With merging off, every append adds a manifest and query planning slows down.",
    )


def _delete_after_commit(ctx: ConfigContext) -> Advice:
    if not _metadata_buildup(ctx):
        return Advice()
    snapshots = int(ctx.stats.get("total_snapshots") or 0)
    return Advice(
        "true",
        f"{snapshots:,} snapshots and frequent commits: without cleanup, every commit leaves "
        "another metadata.json on storage.",
    )


def _previous_versions(ctx: ConfigContext) -> Advice:
    if not _metadata_buildup(ctx):
        return Advice()
    current = parse_int(_current("write.metadata.previous-versions-max", ctx)) or 100
    if current > 2 * _HOT_TABLE_PREVIOUS_VERSIONS:
        return Advice(
            str(_HOT_TABLE_PREVIOUS_VERSIONS),
            "A frequently committed table rarely needs more than the last 10 metadata versions; "
            "with delete-after-commit on, the rest are removed.",
        )
    return Advice()


def _max_inferred(ctx: ConfigContext) -> Advice:
    count = ctx.column_count or 0
    current = parse_int(_current("write.metadata.metrics.max-inferred-column-defaults", ctx))
    if count > (current or 100):
        return Advice(
            str(count),
            f"The table has {count} columns, so filters on the last {count - (current or 100)} "
            "cannot prune files by min/max.",
        )
    return Advice()


def _gc(ctx: ConfigContext) -> Advice:
    if not is_true(_current("gc.enabled", ctx)):
        return Advice(
            note="Cryo skips snapshot expiry and orphan removal on this table while GC is off."
        )
    return Advice()


def _cryo_controls_expiry(ctx: ConfigContext) -> Advice:
    if ctx.cryo_preset_name is None:
        return Advice()
    return Advice(
        note="Cryo passes its own retention to every engine; this applies to expiry run elsewhere."
    )


def _ref_age(ctx: ConfigContext) -> Advice:
    pinned = [ref.name for ref in ctx.refs if ref.name != "main" and ref.max_ref_age_ms is None]
    if pinned and "history.expire.max-ref-age-ms" not in ctx.properties:
        names = ", ".join(pinned[:3]) + ("…" if len(pinned) > 3 else "")
        return Advice(
            note=(
                f"{len(pinned)} branch(es)/tag(s) ({names}) never expire and keep their snapshots' "
                "files alive."
            )
        )
    return Advice()


def _format_version(ctx: ConfigContext) -> Advice:
    if ctx.format_version == 1:
        return Advice(
            "2",
            "v2 adds row-level deletes and sequence numbers and is supported by every engine "
            "Cryo drives.",
        )
    if ctx.format_version == 2:
        return Advice(
            note=(
                "v3 adds deletion vectors and row lineage; Trino's v3 support is still "
                "experimental."
            )
        )
    return Advice()


def _cryo_retention(ctx: ConfigContext) -> Advice:
    if _hot(ctx) or int(ctx.stats.get("total_snapshots") or 0) >= 100:
        return Advice(
            note=(
                "The planner may shorten this run by run, because the table commits often or "
                "carries a snapshot backlog."
            )
        )
    return Advice()


_HANDLERS = {
    "write.target-file-size-bytes": _target_size,
    "write.distribution-mode": _distribution,
    "write.parquet.compression-codec": _codec,
    "write.format.default": _format,
    "write.delete.granularity": _granularity,
    "write.delete.mode": _row_mode_note,
    "write.update.mode": _row_mode_note,
    "write.merge.mode": _row_mode_note,
    "commit.retry.num-retries": _retries,
    "commit.manifest-merge.enabled": _manifest_merge,
    "write.metadata.delete-after-commit.enabled": _delete_after_commit,
    "write.metadata.previous-versions-max": _previous_versions,
    "write.metadata.metrics.max-inferred-column-defaults": _max_inferred,
    "gc.enabled": _gc,
    "history.expire.max-snapshot-age-ms": _cryo_controls_expiry,
    "history.expire.min-snapshots-to-keep": _cryo_controls_expiry,
    "history.expire.max-ref-age-ms": _ref_age,
    "format-version": _format_version,
    "cryo.retention_days": _cryo_retention,
}
