from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.ai.copilot import search_copilot
from cryo.ai.tools.verified import VERIFIED_HANDLERS, match_verified
from cryo.db.models import Source, Table, TableStats


def test_at_least_40_verified_handlers() -> None:
    assert len(VERIFIED_HANDLERS) >= 40
    assert "worst_tables" in VERIFIED_HANDLERS
    assert "compaction_backlog" in VERIFIED_HANDLERS
    for handler_id, entry in VERIFIED_HANDLERS.items():
        assert entry["prompt"]
        assert entry["patterns"]
        assert callable(entry["handler"])
        assert handler_id


def test_match_verified_does_not_label_table_name_as_worst_tables() -> None:
    unrelated = match_verified("orders_fact", {})
    assert unrelated is None or unrelated["handler"] != "worst_tables"
    unhealthy = match_verified("which tables are the unhealthiest", {})
    assert unhealthy is not None
    assert unhealthy["handler"] == "worst_tables"


def test_search_copilot_does_not_label_table_hit_as_worst_tables(session, monkeypatch) -> None:
    source = Source(id=uuid.uuid4(), name="copilot-src", type="rest", config={})
    session.add(source)
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="raw",
            table_name="orders_fact",
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=90,
            total_files=4,
            small_files_count=0,
            total_size_gb=1.0,
        )
    )
    session.commit()

    def fake_embed(text: str, dim: int = 128) -> tuple[float, ...]:
        if "orders_fact" in text:
            return (1.0,) + (0.0,) * (dim - 1)
        return (0.0,) * dim

    monkeypatch.setattr("cryo.ai.copilot.embed_text", fake_embed)
    monkeypatch.setattr("cryo.ai.copilot.cosine_similarity", lambda left, right: left[0] * right[0])
    match = search_copilot("orders_fact", session, min_similarity=0.5)
    assert match is not None
    assert match["question_id"] != "worst_tables"
    assert match["kind"] == "table_metadata"
