"""The configuration reference must not drift from the code (#219).

The page lists every ``CRYO_*`` environment variable and every top-level Helm value. If it
is hand-written it will fall behind the code, which is the failure the issue calls out, so
it is generated from the settings class and the chart's values, and CI checks that the
committed file matches.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryo.capabilities.config_reference import (
    load_helm_values,
    render_configuration_markdown,
    render_helm_table,
    render_settings_table,
)
from cryo.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_settings_field_appears_in_the_reference() -> None:
    """A variable that exists in the code but not on the page is the bug this guards."""
    rendered = "\n".join(render_settings_table())

    missing = [name for name in Settings.model_fields if f"`CRYO_{name.upper()}`" not in rendered]
    assert missing == [], f"settings missing from the configuration reference: {missing}"


def test_the_reference_covers_the_whole_settings_class() -> None:
    """The table has one row per setting, plus its header."""
    rows = render_settings_table()
    assert len(rows) == len(Settings.model_fields) + 2  # header + separator


def test_secret_values_are_never_printed() -> None:
    """A default that is a credential is not rendered, however it is named."""
    rendered = "\n".join(render_settings_table())

    assert "dev-only-change-me" not in rendered
    assert "postgresql+psycopg://" not in rendered
    assert "redis://localhost" not in rendered


def test_the_reference_does_not_break_the_markdown_table() -> None:
    """A type or description containing a pipe would split the cell."""
    for line in render_settings_table():
        # Every row is: | cell | cell | cell | cell |
        assert line.count("|") - line.count("\\|") == 5, line


def test_helm_values_come_from_the_chart() -> None:
    """The Helm table lists every top-level key in the chart's values.yaml."""
    values = load_helm_values()
    assert values, "the chart's values.yaml could not be read"

    rendered = "\n".join(render_helm_table({}, values))
    for name in values:
        assert f"| `{name}` |" in rendered, f"helm value {name} missing from the reference"


def test_the_committed_reference_is_up_to_date() -> None:
    """CI runs the same check: the committed file must match what the code generates."""
    committed = REPO_ROOT / "docs" / "CONFIGURATION.md"
    assert committed.exists(), "docs/CONFIGURATION.md is missing"

    schema_path = REPO_ROOT / "deploy" / "helm" / "cryo" / "values.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else {}

    assert committed.read_text(encoding="utf-8") == render_configuration_markdown(schema)


def test_the_reference_names_the_security_note_for_credentials() -> None:
    """Credential-bearing settings carry the security note."""
    rendered = "\n".join(render_settings_table())

    assert "CRYO_SECRET_KEY" in rendered
    assert "CRYO_DATABASE_URL" in rendered
    assert "Holds a credential" in rendered
