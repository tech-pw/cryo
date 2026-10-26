"""Render the configuration reference from the settings class and the Helm chart schema.

The reference has to be generated, not hand-written: a variable that exists in the code but
not on the page is the exact failure mode the issue calls out (#219). ``Settings`` is the
single source of truth for every ``CRYO_*`` environment variable, and
``deploy/helm/cryo/values.schema.json`` is the single source of truth for the chart's
values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryo.config import Settings

# Settings whose value is a credential. Their name is listed, their value never is.
SECRET_HINTS = (
    "secret",
    "password",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "access_key",
)

# Settings that carry a secret in their *value* even though the name does not say so.
SECRET_VALUE_SETTINGS = frozenset(
    {
        "database_url",
        "redis_url",
        "oidc_client_secret",
        "secret_key",
        "jwt_secret",
    }
)

# Settings whose *name* contains a secret-ish word but which hold a flag or a list, not a
# credential. Matching on the name alone would label them wrongly.
NON_SECRET_OVERRIDES = frozenset(
    {
        "allow_dev_token_in_prod",
        "google_admin_emails",
        "oidc_audience",
    }
)

SECURITY_NOTE = "Holds a credential. Set it from a secret store, never from a committed file."

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "helm" / "cryo" / "values.schema.json"
)
DEFAULT_VALUES_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "helm" / "cryo" / "values.yaml"
)


def _is_secret(name: str) -> bool:
    if name in NON_SECRET_OVERRIDES:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def _default_repr(name: str, default: Any, factory: Any) -> str:
    """A printable default that never leaks a secret."""
    if name in SECRET_VALUE_SETTINGS:
        return "*(set from a secret)*"
    if factory is not None:
        return "*(computed)*"
    if default is None:
        return "*(unset)*"
    if default is Ellipsis:
        return "*(required)*"
    if callable(default):
        return "*(computed)*"
    if isinstance(default, bool):
        return f"`{str(default).lower()}`"
    if isinstance(default, str):
        return f"`{default}`" if default else '`""`'
    if isinstance(default, (int, float)):
        return f"`{default}`"
    if isinstance(default, (list, tuple)):
        return f"`{list(default)}`"
    return f"`{default}`"


def _description(description: str | None) -> str:
    text = (description or "").strip()
    if not text:
        return "-"
    return text.replace("|", "\\|").replace("\n", " ")


def _type_name(annotation: Any) -> str:
    if annotation is None:
        return "any"
    name = getattr(annotation, "__name__", None)
    if name:
        return str(name)
    return str(annotation).replace("typing.", "")


def _cell(value: str) -> str:
    """Escape a value so it cannot break out of a markdown table cell."""
    return value.replace("|", "\\|").replace("\n", " ")


def render_settings_table() -> list[str]:
    """One row per environment variable, read from the settings class."""
    lines = [
        "| Variable | Type | Default | Description |",
        "|---|---|---|---|",
    ]
    for name, field in Settings.model_fields.items():
        variable = f"`CRYO_{name.upper()}`"
        description = _description(field.description)
        if _is_secret(name) or name in SECRET_VALUE_SETTINGS:
            description = f"{SECURITY_NOTE} {description}".strip()
        default = _default_repr(name, field.default, field.default_factory)
        lines.append(
            f"| {variable} | `{_cell(_type_name(field.annotation))}` | {default} | {description} |"
        )
    return lines


def load_helm_values(path: Path | None = None) -> dict[str, Any]:
    """Read the chart's committed defaults.

    ``values.yaml`` is what a real install gets, so it is the source for defaults. The
    JSON schema validates the shape but carries no defaults or descriptions of its own.
    """
    values_path = path or DEFAULT_VALUES_PATH
    if not values_path.exists():
        return {}
    import yaml  # imported lazily: only this generator needs it

    loaded = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "*(none)*"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, str):
        return f"`{value}`" if value else '`""`'
    if isinstance(value, (int, float)):
        return f"`{value}`"
    if isinstance(value, (list, tuple)):
        return f"`{list(value)}`"
    if isinstance(value, dict):
        return "*(object)*"
    return f"`{value}`"


def render_helm_table(schema: dict[str, Any], values: dict[str, Any] | None = None) -> list[str]:
    """One row per top-level Helm value.

    Defaults come from the chart's ``values.yaml``. The JSON schema contributes the type
    and the required-ness.
    """
    properties = (schema.get("properties") or {}) if schema else {}
    chart_values = values if values is not None else load_helm_values()
    required = set(schema.get("required") or []) if schema else set()

    lines = [
        "| Value | Type | Default | Required | Description |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(chart_values):
        spec = properties.get(name) or {}
        value = chart_values[name]
        type_name = spec.get("type") or ("object" if isinstance(value, dict) else "any")
        if isinstance(type_name, list):
            type_name = " \\| ".join(str(item) for item in type_name)
        description = _description(spec.get("description"))
        lines.append(
            f"| `{name}` | `{_cell(type_name)}` | {_yaml_scalar(value)} | "
            f"{'yes' if name in required else 'no'} | {description} |"
        )
    # Schema properties with no default in values.yaml still belong on the page.
    for name in sorted(set(properties) - set(chart_values)):
        spec = properties[name] or {}
        type_name = spec.get("type") or "any"
        if isinstance(type_name, list):
            type_name = " \\| ".join(str(item) for item in type_name)
        description = _description(spec.get("description"))
        lines.append(
            f"| `{name}` | `{_cell(type_name)}` | *(not in values.yaml)* | "
            f"{'yes' if name in required else 'no'} | {description} |"
        )
    return lines


def load_helm_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or DEFAULT_SCHEMA_PATH
    if not schema_path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))
    return loaded


def render_configuration_markdown(schema: dict[str, Any] | None = None) -> str:
    helm_schema = load_helm_schema() if schema is None else schema
    lines = [
        "# Configuration reference",
        "",
        "<!-- Generated by `python -m cryo.capabilities.generate_config_reference`;",
        "do not edit. -->",
        "",
        "Every environment variable Cryo reads, and every top-level Helm value the chart",
        "accepts.",
        "",
        "## Environment variables",
        "",
        "Cryo reads these from the environment, with the `CRYO_` prefix. A default of",
        "*(set from a secret)*, *(unset)* or *(computed)* means there is no safe default to",
        "print.",
        "",
    ]
    lines.extend(render_settings_table())
    lines.extend(
        [
            "",
            "## Helm values",
            "",
            "Top-level values in `deploy/helm/cryo/values.yaml`. The chart's own schema,",
            "`deploy/helm/cryo/values.schema.json`, validates them at install time.",
            "",
        ]
    )
    lines.extend(render_helm_table(helm_schema))
    lines.append("")
    return "\n".join(lines)
