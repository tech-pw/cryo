from __future__ import annotations

from cryo.ai.assistant.grounding import build_chart_block, check_numbers


def test_check_numbers_rejects_invented_and_accepts_evidence() -> None:
    evidence = [{"overall_score": 42, "bytes": 1024, "scan_at": "2026-09-28T00:00:00+00:00"}]
    assert check_numbers("score is 42", evidence) == {"status": "passed", "missing": []}
    missed = check_numbers("score is 99", evidence)
    assert missed["status"] == "fallback"
    assert "99" in missed["missing"]
    assert check_numbers("size is 1 KB", evidence)["status"] == "passed"
    assert check_numbers("as of 2026-09-28", evidence)["status"] == "passed"


def test_chart_builder_rejects_unknown_fields_and_keeps_null() -> None:
    evidence = {
        "rows": [{"name": "a", "value": None}],
        "units": {"value": "count"},
        "columns": [{"key": "name"}, {"key": "value"}],
    }
    block = build_chart_block(
        "ranked_bars",
        evidence,
        {"label": "name", "value": "value", "unit": "count"},
    )
    assert block is not None
    assert block["series"][0]["points"][0]["y"] is None
    assert block["answer_line"] is None
    assert (
        build_chart_block("ranked_bars", evidence, {"label": "missing", "value": "value"}) is None
    )
    assert build_chart_block("not_a_chart", evidence, {"label": "name", "value": "value"}) is None
