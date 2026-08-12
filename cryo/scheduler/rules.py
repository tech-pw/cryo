from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import Mode
from cryo.domain.predicates import evaluate_predicate


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
        _, _, winning_rule, winning_preset = max(matches, key=lambda item: (item[0], item[1]))
        return EffectivePreset(
            preset=winning_preset,
            provenance="rule_after_stale_pin" if stale_pin else "rule",
            rule_id=winning_rule.id,
        )

    # No platform-default fallback by design. Tables without an explicit assignment or
    # matching rule resolve to None (provenance="none"); the UI/API must require an
    # explicit preset selection before maintenance can run.
    return None


def tables_for_preset(
    preset: PresetConfig,
    tables: Iterable[TableCandidate],
    presets: Iterable[PresetConfig],
    rules: Iterable[PresetRule],
    assignments: Iterable[TableAssignment] = (),
) -> list[TableCandidate]:
    matched: list[TableCandidate] = []
    for table in tables:
        if not table.is_active:
            continue
        effective = effective_preset(table, presets, rules, assignments)
        if effective and effective.preset.id == preset.id:
            matched.append(table)
    return matched
