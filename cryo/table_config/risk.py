"""Validation and direction-aware risk for a proposed setting change.

Risk depends on direction: shortening retention can delete history for good, lengthening it
cannot; turning GC off only pauses cleanup, turning it on can delete files another table
still uses. ``high`` changes need an explicit confirmation and a written reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from cryo.table_config.context import (
    ConfigContext,
    bloom_columns,
    current_value,
    effective,
    is_true,
    parse_int,
)
from cryo.table_config.settings import (
    DAY_MS,
    MIB,
    SettingSpec,
    cryo_param_name,
)

Risk = Literal["low", "medium", "high"]
_RANK: dict[Risk, int] = {"low": 0, "medium": 1, "high": 2}
_TRUNCATE = re.compile(r"^truncate\((\d{1,4})\)$")
_MAX_TEXT = 2_000
# pyiceberg 0.11 Transaction.upgrade_table_version accepts only {1, 2}.
PYICEBERG_MAX_UPGRADE_VERSION = 2


@dataclass
class Assessment:
    risk: Risk = "low"
    warnings: list[str] = field(default_factory=list)
    blocked: str | None = None

    def raise_to(self, risk: Risk, warning: str | None = None) -> None:
        if _RANK[risk] > _RANK[self.risk]:
            self.risk = risk
        if warning:
            self.warnings.append(warning)


def max_risk(risks: list[Risk]) -> Risk:
    return max(risks, key=lambda r: _RANK[r], default="low")


def normalize(
    spec: SettingSpec, value: str | None, ctx: ConfigContext
) -> tuple[str | None, str | None]:
    """Canonical form of an edited value, or an error. ``None`` means "unset"."""
    if value is None:
        if not spec.allow_unset:
            return None, f"{spec.label} cannot be unset"
        return None, None
    text = str(value).strip()
    if spec.value_type in {"bytes", "int", "duration_ms", "duration_days"}:
        number = parse_int(text)
        if number is None:
            return None, f"{spec.label} must be a whole number"
        if spec.minimum is not None and number < spec.minimum:
            return None, f"{spec.label} must be at least {_display(spec, spec.minimum)}"
        if spec.maximum is not None and number > spec.maximum:
            return None, f"{spec.label} must be at most {_display(spec, spec.maximum)}"
        return str(number), None
    if spec.value_type == "bool":
        lowered = text.lower()
        if lowered not in {"true", "false"}:
            return None, f"{spec.label} must be true or false"
        return lowered, None
    if spec.value_type == "enum":
        lowered = text.lower()
        if spec.key == "write.metadata.metrics.default":
            match = _TRUNCATE.match(lowered)
            if lowered in {"none", "counts", "full"} or (match and int(match.group(1)) > 0):
                return lowered, None
            return None, "Column metrics must be none, counts, full or truncate(N)"
        if lowered not in spec.options:
            return None, f"{spec.label} must be one of {', '.join(spec.options)}"
        return lowered, None
    if spec.value_type == "columns":
        names = sorted({part.strip() for part in text.split(",") if part.strip()})
        if ctx.columns:
            unknown = [name for name in names if name not in ctx.columns]
            if unknown:
                return None, f"Unknown column(s): {', '.join(unknown)}"
        return ",".join(names), None
    if len(text) > _MAX_TEXT:
        return None, f"{spec.label} must be at most {_MAX_TEXT} characters"
    return text, None


def assess(spec: SettingSpec, old: str | None, new: str | None, ctx: ConfigContext) -> Assessment:
    """``old`` is the value in force; ``new`` is the normalized proposal (None = unset)."""
    result = Assessment()
    old_eff = effective(spec, old)
    new_eff = effective(spec, new)
    if spec.kind == "cryo_param" and new is None:
        name = cryo_param_name(spec.key)
        without = {k: v for k, v in ctx.param_overrides.items() if k != name}
        new_eff, _ = current_value(spec, replace(ctx, param_overrides=without))
    rule = _RULES.get(spec.key)
    if rule is not None:
        rule(result, old_eff, new_eff, ctx)
    elif spec.value_type in {"duration_ms", "duration_days"} or spec.kind == "cryo_param":
        _lowering_is_high(result, old_eff, new_eff, spec)
    if spec.kind == "cryo_param" and new is None:
        result.warnings.append("Removes the override; the preset's value applies again.")
    return result


def _lowering_is_high(
    result: Assessment, old: str | None, new: str | None, spec: SettingSpec
) -> None:
    old_n, new_n = parse_int(old), parse_int(new)
    if new_n is None:
        return
    if old_n is None or new_n < old_n:
        result.raise_to(
            "high",
            f"Lowering {spec.label.lower()} lets the next run delete history that is kept "
            "today. Deleted snapshots and files cannot be recovered.",
        )


def _target_file_size(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    new_n = parse_int(new)
    target = ctx.cryo_target_bytes
    if new_n is None or target is None:
        return
    if new_n < target:
        result.raise_to(
            "medium",
            f"New files would be smaller than the {_mb(target)} Cryo compacts toward under "
            f"preset {ctx.cryo_preset_name}, so they count as small and get compacted again.",
        )
    if ctx.cryo_persists_layout and new_n != target:
        result.warnings.append(
            f"Cryo's {ctx.cryo_engine} compaction resets this to {_mb(target)} after each "
            "optimize. Change the preset's file size to make it stick."
        )


def _distribution(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if new == "none" and ctx.partitioned:
        result.raise_to(
            "medium",
            "Without a shuffle, every writer task opens a file per partition it touches, which "
            "multiplies small files on a partitioned table.",
        )
    elif new is None and ctx.partitioned:
        result.warnings.append(
            "Unset leaves it to each engine: Spark shuffles by hash, Flink does not shuffle."
        )
    if new == "range" and not ctx.has_sort_order:
        result.warnings.append(
            "range distribution orders by the sort order; this table has none, so Spark ranges "
            "by partition only."
        )
    if ctx.cryo_persists_layout and new != "hash":
        result.warnings.append(
            f"Cryo's {ctx.cryo_engine} compaction resets this to hash after each optimize."
        )


def _file_format(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if new != "parquet":
        result.raise_to(
            "medium",
            "The table will hold mixed formats until compacted, and Cryo's bloom-filter and "
            "row-group analysis only reads Parquet.",
        )


def _codec(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if new == "uncompressed":
        result.raise_to("medium", "Uncompressed Parquet typically takes several times the storage.")


def _row_mode(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if new == "merge-on-read":
        if ctx.format_version is not None and ctx.format_version < 2:
            result.blocked = "merge-on-read needs format version 2 or later."
            return
        result.raise_to(
            "medium",
            "Each row-level commit adds delete files that every read must apply until "
            "rewrite_position_deletes or convert_deletes folds them in. Make sure the preset "
            "runs delete compaction.",
        )
    elif new == "copy-on-write":
        result.raise_to(
            "medium",
            "Each row-level commit rewrites every data file it touches. Frequent small updates "
            "become expensive and slow.",
        )


def _upsert(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if is_true(new):
        if ctx.format_version is not None and ctx.format_version < 2:
            result.blocked = "Upsert writes need format version 2 or later."
            return
        if not ctx.identifier_fields:
            result.blocked = "Upsert writes need identifier fields on the table schema."
            return
        result.raise_to(
            "medium",
            "Every write produces equality deletes, which slow reads until compacted.",
        )
    else:
        result.raise_to(
            "medium",
            "Writers that relied on upsert will append duplicate rows instead of replacing them.",
        )


def _commit_retries(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    if parse_int(new) == 0:
        result.raise_to(
            "medium",
            "Any concurrent commit, including Cryo's compaction, fails the write outright.",
        )


def _manifest_merge(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    if not is_true(new):
        result.raise_to(
            "medium",
            "Every append adds a manifest; planning slows until optimize_manifests runs.",
        )


def _delete_after_commit(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    if is_true(new) and not is_true(old):
        keep = parse_int(ctx.properties.get("write.metadata.previous-versions-max")) or 100
        result.raise_to(
            "medium",
            f"Writers start deleting metadata.json files beyond the newest {keep}. Those files "
            "cannot be restored.",
        )


def _previous_versions(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    old_n, new_n = parse_int(old), parse_int(new)
    if new_n is None or old_n is None or new_n >= old_n:
        return
    if is_true(ctx.properties.get("write.metadata.delete-after-commit.enabled")):
        result.raise_to(
            "medium",
            f"With delete-after-commit on, the next commit deletes all but the newest {new_n} "
            "metadata.json files.",
        )


def _metrics(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if new in {"none", "counts"}:
        result.raise_to(
            "medium",
            "New files lose min/max bounds, so engines can no longer skip them by filter.",
        )
    elif new == "full":
        result.warnings.append("Full bounds on long strings make manifests larger.")


def _max_inferred(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    new_n = parse_int(new)
    if new_n is not None and ctx.column_count and new_n < ctx.column_count:
        result.raise_to(
            "medium",
            f"Columns after the first {new_n} of {ctx.column_count} get no min/max statistics.",
        )


def _gc(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    if is_true(new) and not is_true(old):
        result.raise_to(
            "high",
            "GC is usually off because another table shares these files (for example one created "
            "by a snapshot or migrate procedure). Expiry and orphan removal would then delete "
            "files that table still reads. Confirm nothing else references this location.",
        )
    elif not is_true(new) and is_true(old):
        result.raise_to(
            "medium",
            "Cryo stops expiring snapshots and removing orphan files here, so storage only grows. "
            "Trino before 484 ignores this flag if someone runs those procedures directly.",
        )


def _max_ref_age(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    old_n, new_n = parse_int(old), parse_int(new)
    if new_n is None:
        return
    if old_n is None or new_n < old_n:
        expiring = [
            ref.name for ref in ctx.refs if ref.name != "main" and ref.max_ref_age_ms is None
        ]
        detail = f" Affects: {', '.join(expiring[:5])}." if expiring else ""
        result.raise_to(
            "high",
            "Branches and tags without their own limit are removed once older than this, and the "
            f"snapshots only they kept are expired.{detail}",
        )


def _min_snapshots(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    _lowering_is_high(result, old, new, _spec("history.expire.min-snapshots-to-keep"))


def _cryo_retention(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    _lowering_is_high(result, old, new, _spec("cryo.retention_days"))
    new_n = parse_int(new)
    if new_n is not None and new_n < 7 and ctx.cryo_engine == "trino":
        result.warnings.append(
            "Trino rejects expire_snapshots below iceberg.expire-snapshots.min-retention, 7 days "
            "unless the catalog lowers it."
        )


def _cryo_orphan(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    _lowering_is_high(result, old, new, _spec("cryo.orphan_retention_days"))
    new_n = parse_int(new)
    if new_n is None:
        return
    if new_n < 3:
        result.raise_to(
            "high",
            "Shorter than Iceberg's 3-day guidance: files from a write still in progress look "
            "orphaned and get deleted, corrupting that write.",
        )
    if new_n < 7 and ctx.cryo_engine == "trino":
        result.warnings.append(
            "Trino rejects remove_orphan_files below iceberg.remove-orphan-files.min-retention, "
            "7 days unless the catalog lowers it."
        )


def _format_version(
    result: Assessment, old: str | None, new: str | None, ctx: ConfigContext
) -> None:
    old_n, new_n = parse_int(old), parse_int(new)
    if new_n is None:
        return
    if old_n is not None and new_n < old_n:
        result.blocked = "Iceberg has no format downgrade."
        return
    if new_n > PYICEBERG_MAX_UPGRADE_VERSION:
        result.blocked = (
            f"Cryo writes table metadata with PyIceberg, which upgrades only to "
            f"v{PYICEBERG_MAX_UPGRADE_VERSION}. Upgrade to v{new_n} with Spark "
            f"(ALTER TABLE … SET TBLPROPERTIES ('format-version'='{new_n}')) or Trino "
            f"(ALTER TABLE … SET PROPERTIES format_version = {new_n})."
        )
    result.raise_to(
        "high",
        f"Upgrading to v{new_n} is permanent. Every engine that reads or writes this table must "
        f"support v{new_n} first.",
    )
    if new_n >= 3:
        result.warnings.append(
            "Trino's v3 support is experimental, and v3 tables cannot use add_files."
        )


def _bloom(result: Assessment, old: str | None, new: str | None, ctx: ConfigContext) -> None:
    added = set(filter(None, (new or "").split(","))) - set(filter(None, (old or "").split(",")))
    if len(bloom_columns(ctx.properties)) + len(added) > 5:
        result.warnings.append(
            "Each bloom filter adds up to 1 MB per row group; more than a handful rarely pays off."
        )


def _spec(key: str) -> SettingSpec:
    from cryo.table_config.settings import SETTINGS_BY_KEY

    return SETTINGS_BY_KEY[key]


def _mb(num_bytes: int) -> str:
    return format_bytes(num_bytes)


def format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 * MIB and num_bytes % (1024 * MIB) == 0:
        return f"{num_bytes // (1024 * MIB)} GiB"
    if num_bytes % MIB == 0:
        return f"{num_bytes // MIB} MiB"
    return f"{num_bytes / MIB:.1f} MiB"


def _display(spec: SettingSpec, number: int) -> str:
    if spec.value_type == "bytes":
        return format_bytes(number)
    if spec.value_type == "duration_ms":
        if number % DAY_MS == 0:
            return f"{number // DAY_MS} days"
        return f"{number / 3_600_000:g} hours"
    if spec.value_type == "duration_days":
        return f"{number} days"
    return f"{number}{' ' + spec.unit if spec.unit else ''}"


_RULES = {
    "write.target-file-size-bytes": _target_file_size,
    "write.distribution-mode": _distribution,
    "write.format.default": _file_format,
    "write.parquet.compression-codec": _codec,
    "write.delete.mode": _row_mode,
    "write.update.mode": _row_mode,
    "write.merge.mode": _row_mode,
    "write.upsert.enabled": _upsert,
    "commit.retry.num-retries": _commit_retries,
    "commit.manifest-merge.enabled": _manifest_merge,
    "write.metadata.delete-after-commit.enabled": _delete_after_commit,
    "write.metadata.previous-versions-max": _previous_versions,
    "write.metadata.metrics.default": _metrics,
    "write.metadata.metrics.max-inferred-column-defaults": _max_inferred,
    "gc.enabled": _gc,
    "history.expire.max-ref-age-ms": _max_ref_age,
    "history.expire.min-snapshots-to-keep": _min_snapshots,
    "cryo.retention_days": _cryo_retention,
    "cryo.orphan_retention_days": _cryo_orphan,
    "format-version": _format_version,
    "write.parquet.bloom-filter-columns": _bloom,
}
