from __future__ import annotations

from cryo.ai.filter_translate import (
    build_filter_chips,
    deterministic_translate,
    validate_filter_spec,
)


def test_deterministic_translate_extracts_common_filters() -> None:
    result = deterministic_translate(
        'grade d needs compaction savings above $250 size bigger than 80gb name contains "orders"'
    )
    assert result.filter["activeOnly"] is True
    assert result.filter["grades"] == ["D"]
    assert result.filter["needsCompaction"] is True
    assert result.filter["minMonthlySavings"] == 250.0
    assert result.filter["minSizeGb"] == 80.0
    assert result.filter["schemaSearch"] == "orders"


def test_validate_filter_spec_drops_unknown_fields() -> None:
    cleaned, errors = validate_filter_spec(
        {
            "grades": ["A", "B"],
            "minSmallFiles": 120,
            "not_allowed": "x",
        }
    )
    assert cleaned["grades"] == ["A", "B"]
    assert cleaned["minSmallFiles"] == 120.0
    assert any("Unsupported filter field: not_allowed" in item for item in errors)


def test_build_filter_chips_formats_human_labels() -> None:
    chips = build_filter_chips(
        {
            "activeOnly": False,
            "grades": ["C", "D"],
            "minMonthlySavings": 300.0,
            "minReclaimableGb": 120.0,
        }
    )
    assert "All tables" in chips
    assert "Grade: C, D" in chips
    assert "Savings >= $300/mo" in chips
    assert "Reclaimable >= 120 GB" in chips
