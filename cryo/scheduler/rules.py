from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryo.domain.enums import Mode
from cryo.domain.predicates import evaluate_predicate

# PDAE-8347 Pattern 1: Spark-on-K8s burned driver slots on ephemeral temp/test tables
# that a broad preset rule had matched. Live fix on 2026-08-11 was preset predicates;
# this default skip keeps *new* presets from reintroducing the same tables. Pinned
# assignments still win (operator intent). `testpress` is a real product schema/prefix,
# not a scratch namespace -- carve it out. Do not match `_test` in the middle of a name
# (`gold_test_student_mappings` is a live exam-product table).
_EPHEMERAL_SCHEMAS = frozenset({"temp", "tmp", "scratch"})
_EPHEMERAL_SUFFIXES = ("_temp", "_tmp", "_scratch", "_test", "_dtmp")
_EPHEMERAL_PREFIXES = ("temp_", "tmp_", "scratch_")
_TESTPRESS_TOKEN = "testpress"


@dataclass(frozen=True)
class PresetConfig:
    id: str
    name: str
    destination_id: str
    mode: Mode | str = Mode.STANDARD
    params: Mapping[str, Any] = field(default_factory=dict)
    guardrails: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True
    schedule_cron: str | None = None
    is_default: bool = False


@dataclass(frozen=True)
class PresetRule:
    id: str
    preset_id: str
    predicate: Mapping[str, Any]
    enabled: bool = True


@dataclass(frozen=True)
class TableCandidate:
    id: str
    schema_name: str
    table_name: str
    stats: Mapping[str, Any] = field(default_factory=dict)
    is_active: bool = True
    last_maintained_at: Any | None = None
    # Critical vitals action plan, 2026-09-20: set (via cryo.db.repositories.sources.
    # record_missing_metadata) when a maintenance job confirmed this table's registered
    # location's metadata.json/manifest is missing in S3. tables_for_preset excludes it from
    # scheduling until this passes -- same is_active-style gate, but reversible and narrower
    # (does not deactivate the table, just pauses scheduling for a bounded window).
    maintenance_retry_after: Any | None = None

    @property
    def row(self) -> dict[str, Any]:
        merged = dict(self.stats)
        merged.setdefault("table_id", self.id)
        merged.setdefault("schema_name", self.schema_name)
        merged.setdefault("table_name", self.table_name)
        merged.setdefault("is_active", self.is_active)
        return merged


@dataclass(frozen=True)
class TableAssignment:
    table_id: str
    preset_id: str
    pinned: bool = True


@dataclass(frozen=True)
class EffectivePreset:
    preset: PresetConfig
    provenance: str
    rule_id: str | None = None


def is_ephemeral_maintenance_table(schema_name: str, table_name: str) -> bool:
    """True when this table should not be auto-matched by preset rules (PDAE-8347)."""
    schema = (schema_name or "").strip()
    name = (table_name or "").strip()
    combined = f"{schema}.{name}".lower()
    if _TESTPRESS_TOKEN in combined:
        return False
    schema_l = schema.lower()
    name_l = name.lower()
    if schema_l in _EPHEMERAL_SCHEMAS:
        return True
    if "__dbt_tmp" in name_l:
        return True
    if name_l.endswith(_EPHEMERAL_SUFFIXES):
        return True
    return name_l.startswith(_EPHEMERAL_PREFIXES)


def effective_preset(
    table: TableCandidate,
    presets: Iterable[PresetConfig],
    rules: Iterable[PresetRule],
    assignments: Iterable[TableAssignment] = (),
) -> EffectivePreset | None:
    presets_by_id = {preset.id: preset for preset in presets if preset.enabled}

    stale_pin = False
    for assignment in assignments:
        if assignment.table_id == table.id and assignment.pinned:
            preset = presets_by_id.get(assignment.preset_id)
            if preset:
                return EffectivePreset(preset=preset, provenance="assignment")
            # The pin exists but points at a preset that no longer exists or has been
            # disabled since the table was pinned (e.g. a preset retired in favor of a
            # replacement). A hard stop here silently blackholes the table forever --
            # found live in production: 21 tables pinned to disabled presets, including
            # a 24TB table, sitting with needs_compaction=True and never rescheduled.
            # Falling through to the rule engine is strictly safer than returning None:
            # worst case a table gets the generic rule-matched preset instead of its
            # stale pin, which still maintains it; returning None guarantees it never
            # gets maintained until someone manually notices and re-pins it.
            stale_pin = True
            break

    matches: list[tuple[int, str, PresetRule, PresetConfig]] = []
    for rule in rules:
        if not rule.enabled:
            continue
        preset = presets_by_id.get(rule.preset_id)
        if preset and evaluate_predicate(rule.predicate, table.row):
            matches.append((preset.priority, preset.name, rule, preset))

    if matches:
        if is_ephemeral_maintenance_table(table.schema_name, table.table_name):
            return None
        _, _, winning_rule, winning_preset = max(matches, key=lambda item: (item[0], item[1]))
        return EffectivePreset(
            preset=winning_preset,
            provenance="rule_after_stale_pin" if stale_pin else "rule",
            rule_id=winning_rule.id,
        )

    # Product decision 2026-09-29: tables with no explicit assignment and no matching rule
    # fall through to the tenant's is_default preset (Spark-on-K8s compaction) rather than
    # staying unmaintained. Ephemeral tables are still excluded, same as the rule-match path
    # above -- a fleet-wide default must not sweep up temp/scratch tables just because they
    # never matched an explicit rule. Ties (should not happen: DB partial unique index
    # enforces at most one is_default=true per tenant) resolve to the first one found.
    if is_ephemeral_maintenance_table(table.schema_name, table.table_name):
        return None
    for preset in presets_by_id.values():
        if preset.is_default:
            return EffectivePreset(preset=preset, provenance="default")
    return None


def tables_for_preset(
    preset: PresetConfig,
    tables: Iterable[TableCandidate],
    presets: Iterable[PresetConfig],
    rules: Iterable[PresetRule],
    assignments: Iterable[TableAssignment] = (),
    *,
    now: datetime | None = None,
) -> list[TableCandidate]:
    current = now or datetime.now(UTC)
    matched: list[TableCandidate] = []
    for table in tables:
        if not table.is_active:
            continue
        # Critical vitals action plan, 2026-09-20: same shape as the is_active gate above, but
        # reversible and bounded -- see TableCandidate.maintenance_retry_after's own docstring.
        # A naive/None-tzinfo value (SQLite test backend, matching this repo's other tz-aware
        # comparisons -- e.g. run_finalizer.py's grace-window check) is treated as UTC.
        retry_after = table.maintenance_retry_after
        if retry_after is not None:
            if retry_after.tzinfo is None:
                retry_after = retry_after.replace(tzinfo=UTC)
            if retry_after > current:
                continue
        effective = effective_preset(table, presets, rules, assignments)
        if effective and effective.preset.id == preset.id:
            matched.append(table)
    return matched
