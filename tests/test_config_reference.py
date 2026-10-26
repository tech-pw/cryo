from __future__ import annotations

import pytest

from cryo import config_reference
from cryo.config import Settings


def test_committed_reference_is_current() -> None:
    assert config_reference.main(["--check"]) == 0


def test_every_settings_field_is_listed() -> None:
    rendered = config_reference.render_env()
    for name in Settings.model_fields:
        assert f"<code>CRYO_{name.upper()}</code>" in rendered


def test_every_top_level_helm_value_is_listed() -> None:
    values = config_reference.helm_values(config_reference.VALUES_FILE.read_text())
    rendered = config_reference.render_helm()
    assert values
    for key, _children in values:
        assert f"<td><code>{key}</code></td>" in rendered


def test_unknown_env_read_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config_reference.env_reads_outside_settings
    monkeypatch.setattr(
        config_reference,
        "env_reads_outside_settings",
        lambda: real() | {"CRYO_SOMETHING_NEW"},
    )
    assert any("CRYO_SOMETHING_NEW" in issue for issue in config_reference.problems())


def test_new_undescribed_helm_value_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(config_reference.HELM_DESCRIPTIONS, "backup")
    assert any("Helm value backup" in issue for issue in config_reference.problems())


def test_helm_values_parses_top_level_and_children() -> None:
    text = (
        "# comment\nimage:\n  repository: cryo\n  # inner\n  tag: latest\nservice:\n  port: 8000\n"
    )
    assert config_reference.helm_values(text) == [
        ("image", ["repository", "tag"]),
        ("service", ["port"]),
    ]


def test_env_read_pattern_matches_common_forms() -> None:
    sample = (
        'os.environ.get("CRYO_A")\nos.getenv("CRYO_B", "1")\n'
        'os.environ[\'CRYO_C\']\nos.environ.get(\n    "CRYO_D", "x"\n)\n'
    )
    assert {m.group(1) for m in config_reference._ENV_READ.finditer(sample)} == {
        "CRYO_A",
        "CRYO_B",
        "CRYO_C",
        "CRYO_D",
    }
