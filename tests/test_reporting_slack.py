"""Tests for cryo/reporting/slack.py's Block Kit builders, focused on table_block -- the real
Slack table block used by the daily digest for a more visual, less verbose report."""

from __future__ import annotations

from cryo.reporting.slack import table_block


def test_table_block_has_correct_type() -> None:
    block = table_block(["A", "B"], [["1", "2"]])
    assert block.to_dict()["type"] == "table"


def test_table_block_header_row_is_bold() -> None:
    block = table_block(["Metric", "Value"], [["Total Tables", "10"]])
    rows = block.to_dict()["rows"]

    header_cell = rows[0][0]
    assert header_cell["elements"][0]["elements"][0]["style"] == {"bold": True}


def test_table_block_data_rows_are_not_bold() -> None:
    block = table_block(["Metric", "Value"], [["Total Tables", "10"]])
    rows = block.to_dict()["rows"]

    data_cell = rows[1][0]
    assert "style" not in data_cell["elements"][0]["elements"][0]


def test_table_block_preserves_row_order_and_content() -> None:
    block = table_block(["Grade", "Tables"], [["A", "5"], ["B", "12"], ["D", "3"]])
    rows = block.to_dict()["rows"]

    texts = [[cell["elements"][0]["elements"][0]["text"] for cell in row] for row in rows]
    assert texts == [["Grade", "Tables"], ["A", "5"], ["B", "12"], ["D", "3"]]


def test_table_block_with_only_header_has_one_row() -> None:
    block = table_block(["Metric", "Value"], [])
    assert len(block.to_dict()["rows"]) == 1
