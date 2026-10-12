"""Tests for cryo/reporting/query_cost_slack.py's Block Kit rendering.

No test file existed for this module before -- covers the "Top users" block added for
docs/QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md, plus a couple of general smoke tests for the
no-data and top-clients paths that were previously only exercised indirectly through
test_query_cost_report.py's end-to-end notify_query_cost_report tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.reporting.query_cost_signals import (
    ClientQueryCost,
    DailyQueryCostTotals,
    QueryCostSignal,
    UserQueryCost,
)
from cryo.reporting.query_cost_slack import (
    build_query_cost_slack_blocks,
    query_cost_fallback_text,
)

NOW = datetime(2026, 8, 19, 10, 15, tzinfo=UTC)


def _signal(
    *,
    current: DailyQueryCostTotals | None = None,
    direction: str = "stable",
    top_clients: list[ClientQueryCost] | None = None,
    top_users: list[UserQueryCost] | None = None,
) -> QueryCostSignal:
    return QueryCostSignal(
        direction=direction,
        dod_pct=None,
        wow_pct=None,
        primary_signal=None,
        current=current,
        dod_baseline=None,
        wow_baseline=None,
        top_clients_current=top_clients or [],
        top_users_current=top_users or [],
        failure_signal=None,
        unknown_client_share_pct=None,
    )


def _totals(**overrides) -> DailyQueryCostTotals:
    defaults = dict(
        day=NOW.date(),
        query_count=100,
        failed_query_count=2,
        physical_bytes_scanned=10 * 1024**3,
        estimated_scan_cost_usd=25.0,
    )
    defaults.update(overrides)
    return DailyQueryCostTotals(**defaults)


def _block_texts(blocks) -> list[str]:
    texts = []
    for b in blocks:
        if b.text:
            texts.append(b.text.get("text", ""))
    return texts


def test_no_data_renders_a_single_explanatory_message() -> None:
    blocks = build_query_cost_slack_blocks(_signal(current=None), generated_at=NOW)

    texts = " ".join(_block_texts(blocks))
    assert "No Trino query-cost data" in texts


def test_top_users_block_renders_when_present() -> None:
    signal = _signal(
        current=_totals(),
        top_users=[
            UserQueryCost(
                user_name="alice",
                query_count=40,
                physical_bytes_scanned=5 * 1024**3,
                estimated_scan_cost_usd=15.0,
            ),
            UserQueryCost(
                user_name="unknown",
                query_count=10,
                physical_bytes_scanned=1 * 1024**3,
                estimated_scan_cost_usd=2.0,
            ),
        ],
    )

    blocks = build_query_cost_slack_blocks(signal, generated_at=NOW)

    texts = _block_texts(blocks)
    assert any("Top users by scan cost" in t for t in texts)
    # The table block itself is a "table" type, not a section -- confirm it exists and carries
    # both rows via its rows attribute rather than text.
    table_blocks = [
        b
        for b in blocks
        if b.type == "table"
        and b.rows
        and b.rows[0][0].get("elements", [{}])[0].get("elements", [{}])[0].get("text") == "User"
    ]
    assert table_blocks
    rendered_users = [
        row[0]["elements"][0]["elements"][0]["text"] for row in table_blocks[0].rows[1:]
    ]
    assert rendered_users == ["alice", "unknown"]


def test_top_users_block_omitted_when_empty() -> None:
    signal = _signal(current=_totals(), top_users=[])

    blocks = build_query_cost_slack_blocks(signal, generated_at=NOW)

    texts = " ".join(_block_texts(blocks))
    assert "Top users by scan cost" not in texts


def test_top_clients_and_top_users_both_render_independently() -> None:
    signal = _signal(
        current=_totals(),
        top_clients=[
            ClientQueryCost(
                client="Metabase",
                query_count=80,
                physical_bytes_scanned=8 * 1024**3,
                estimated_scan_cost_usd=20.0,
            )
        ],
        top_users=[
            UserQueryCost(
                user_name="bob",
                query_count=20,
                physical_bytes_scanned=2 * 1024**3,
                estimated_scan_cost_usd=5.0,
            )
        ],
    )

    blocks = build_query_cost_slack_blocks(signal, generated_at=NOW)

    texts = " ".join(_block_texts(blocks))
    assert "Top clients by scan cost" in texts
    assert "Top users by scan cost" in texts


def test_fallback_text_no_data() -> None:
    text = query_cost_fallback_text(_signal(current=None), NOW)
    assert "no data ingested yet" in text


def test_fallback_text_with_data() -> None:
    text = query_cost_fallback_text(_signal(current=_totals(), direction="spike"), NOW)
    assert "spike" in text
    assert "$25.00" in text
