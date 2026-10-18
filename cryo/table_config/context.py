"""Inputs shared by value resolution, recommendations and risk assessment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from cryo.table_config.settings import (
    BLOOM_FILTER_PREFIX,
    MIB,
    SettingSpec,
    cryo_param_name,
)

ValueSource = Literal[
    "table",
    "iceberg_default",
    "engine_default",
    "metadata",
    "cryo_preset",
    "cryo_override",
    "cryo_default",
]

# Spark's and the native engine's optimize fall back to this when the preset sets no size.
CRYO_DEFAULT_TARGET_BYTES = 256 * MIB
CRYO_PERSISTING_ENGINES = frozenset({"spark", "spark_emr", "native"})
CRYO_DEFAULTS: dict[str, int] = {"retention_days": 7, "retain_last": 1}


@dataclass(frozen=True)
class RefInfo:
    name: str
    type: Literal["branch", "tag"]
    snapshot_id: int | None
    max_ref_age_ms: int | None = None
    max_snapshot_age_ms: int | None = None
    min_snapshots_to_keep: int | None = None


@dataclass(frozen=True)
class ConfigContext:
    properties: Mapping[str, str]
    format_version: int | None = None
    partitioned: bool = False
    has_sort_order: bool = False
    identifier_fields: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    column_count: int | None = None
    refs: tuple[RefInfo, ...] = ()
    cryo_engine: str | None = None
    cryo_preset_name: str | None = None
    preset_params: Mapping[str, Any] = field(default_factory=dict)
    param_overrides: Mapping[str, Any] = field(default_factory=dict)
    stats: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cryo_params(self) -> dict[str, Any]:
        return {**self.preset_params, **self.param_overrides}

    @property
    def cryo_target_bytes(self) -> int | None:
        """The size Cryo compacts toward, or None when no preset governs this table."""
        if self.cryo_preset_name is None:
            return None
        params = self.cryo_params
        explicit = _positive_int(params.get("target_file_size_bytes"))
        if explicit is not None:
            return explicit
        mb = _positive_int(params.get("file_size_mb"))
        return mb * MIB if mb is not None else CRYO_DEFAULT_TARGET_BYTES

    @property
    def cryo_persists_layout(self) -> bool:
        """Spark and native optimize write target size + hash distribution back to the table."""
        if self.cryo_engine not in CRYO_PERSISTING_ENGINES:
            return False
        if self.cryo_engine == "native":
            return True
        return self.cryo_params.get("persist_target_file_size", True) is not False

    @property
    def merge_on_read_in_use(self) -> bool:
        return any(
            self.properties.get(key) == "merge-on-read"
            for key in ("write.delete.mode", "write.update.mode", "write.merge.mode")
        )


def bloom_columns(properties: Mapping[str, str]) -> list[str]:
    return sorted(
        key.removeprefix(BLOOM_FILTER_PREFIX)
        for key, value in properties.items()
        if key.startswith(BLOOM_FILTER_PREFIX) and str(value).strip().lower() == "true"
    )


def current_value(spec: SettingSpec, ctx: ConfigContext) -> tuple[str | None, ValueSource]:
    """The value in force today and where it comes from."""
    if spec.kind == "format_version":
        return (str(ctx.format_version) if ctx.format_version else None), "metadata"
    if spec.kind == "bloom_columns":
        columns = bloom_columns(ctx.properties)
        return (",".join(columns), "table") if columns else ("", "iceberg_default")
    if spec.kind == "cryo_param":
        name = cryo_param_name(spec.key)
        if name in ctx.param_overrides:
            return _param_str(ctx.param_overrides[name]), "cryo_override"
        if name in ctx.preset_params:
            return _param_str(ctx.preset_params[name]), "cryo_preset"
        if name == "orphan_retention_days":
            retention, _ = current_value(_retention_spec(), ctx)
            return retention, "cryo_default"
        return str(CRYO_DEFAULTS.get(name, spec.default or "")) or None, "cryo_default"
    if spec.key in ctx.properties:
        return str(ctx.properties[spec.key]), "table"
    if spec.default is None:
        return None, (
            "engine_default" if spec.key == "write.distribution-mode" else "iceberg_default"
        )
    return spec.default, "iceberg_default"


def effective(spec: SettingSpec, value: str | None) -> str | None:
    """A value as engines see it: unset falls back to the spec default."""
    if value is None or value == "":
        return spec.default if spec.kind != "bloom_columns" else ""
    return value


def _retention_spec() -> SettingSpec:
    from cryo.table_config.settings import SETTINGS_BY_KEY

    return SETTINGS_BY_KEY["cryo.retention_days"]


def _param_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not text.lstrip("-").isdigit():
        return None
    return int(text)


def is_true(value: str | None) -> bool:
    """Iceberg parses booleans with Java Boolean.parseBoolean: only "true" is true."""
    return value is not None and str(value).strip().lower() == "true"
