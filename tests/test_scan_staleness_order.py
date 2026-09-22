"""Tests for cryo/scanner/run.py::order_by_staleness.

The scanner cannot finish 12.6k tables in one pass, so the order candidates are considered in
decides which tables actually get scanned. Catalog order (source, schema, table) made that an
alphabetical accident: live 2026-09-22, 686 tables were scanned all 7 days of the week while
687 were scanned on exactly one, and 11% of the fleet had drifted past 30 days without a scan.
Stalest-first makes an unfinished run self-correcting -- whatever it misses is nearer the front
next time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableStats
from cryo.scanner.run import ScanCandidate, deterministic_chunk, order_by_staleness

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


class _FakeCatalog:
    def load_table(self, schema: str, table: str) -> None:  # pragma: no cover - unused
        raise NotImplementedError


def _seed_source(session, *, name: str = "cdp") -> Source:
    source = Source(id=uuid4(), name=name, type="rest", config={}, enabled=True)
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str, scanned_ago_days: float | None) -> Table:
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    if scanned_ago_days is not None:
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=NOW - timedelta(days=scanned_ago_days),
                needs_compaction=False,
                compaction_priority="LOW",
                grade="B",
                overall_score=80,
                total_files=10,
                small_files_count=1,
                total_size_gb=1.0,
            )
        )
        session.flush()
    return table


def _candidate(source: Source, name: str) -> ScanCandidate:
    return ScanCandidate(source=source, catalog=_FakeCatalog(), schema_name="s", table_name=name)


def _names(candidates: list[ScanCandidate]) -> list[str]:
    return [c.table_name for c in candidates]


def test_stalest_table_is_ordered_first(session):
    source = _seed_source(session)
    _seed_table(session, source, name="fresh", scanned_ago_days=1)
    _seed_table(session, source, name="stale", scanned_ago_days=40)
    _seed_table(session, source, name="middling", scanned_ago_days=8)
    session.commit()

    ordered = order_by_staleness(
        session,
        [_candidate(source, n) for n in ("fresh", "middling", "stale")],
    )

    assert _names(ordered) == ["stale", "middling", "fresh"]


def test_never_scanned_tables_come_before_every_scanned_table(session):
    source = _seed_source(session)
    _seed_table(session, source, name="ancient", scanned_ago_days=365)
    _seed_table(session, source, name="virgin", scanned_ago_days=None)
    session.commit()

    ordered = order_by_staleness(session, [_candidate(source, n) for n in ("ancient", "virgin")])

    assert _names(ordered) == ["virgin", "ancient"]


def test_a_candidate_with_no_table_row_is_treated_as_never_scanned(session):
    source = _seed_source(session)
    _seed_table(session, source, name="known", scanned_ago_days=2)
    session.commit()

    ordered = order_by_staleness(session, [_candidate(source, n) for n in ("known", "brand_new")])

    assert _names(ordered) == ["brand_new", "known"]


def test_ordering_is_total_so_chunk_assignment_is_reproducible(session):
    source = _seed_source(session)
    for name in ("a", "b", "c"):
        _seed_table(session, source, name=name, scanned_ago_days=5)
    session.commit()

    candidates = [_candidate(source, n) for n in ("c", "a", "b")]
    first = order_by_staleness(session, candidates)
    second = order_by_staleness(session, list(reversed(candidates)))

    # Equal staleness falls through to catalog order, so two runs over the same fleet state
    # produce identical chunks regardless of how discovery happened to enumerate them.
    assert _names(first) == _names(second) == ["a", "b", "c"]


def test_empty_candidate_list_short_circuits(session):
    assert order_by_staleness(session, []) == []


def test_chunks_together_cover_every_candidate_exactly_once(session):
    source = _seed_source(session)
    for index in range(10):
        _seed_table(session, source, name=f"t{index}", scanned_ago_days=index)
    session.commit()

    ordered = order_by_staleness(session, [_candidate(source, f"t{i}") for i in range(10)])
    covered = [
        name
        for chunk_id in range(4)
        for name in _names(deterministic_chunk(ordered, chunk_id=chunk_id, num_chunks=4))
    ]

    assert sorted(covered) == sorted(f"t{i}" for i in range(10))
    assert len(covered) == 10


def test_stale_tables_spread_across_chunks_rather_than_piling_into_one(session):
    source = _seed_source(session)
    # Five very stale, five fresh.
    for index in range(5):
        _seed_table(session, source, name=f"stale{index}", scanned_ago_days=90 + index)
    for index in range(5):
        _seed_table(session, source, name=f"fresh{index}", scanned_ago_days=index)
    session.commit()

    names = [f"stale{i}" for i in range(5)] + [f"fresh{i}" for i in range(5)]
    ordered = order_by_staleness(session, [_candidate(source, n) for n in names])

    # Round-robin over a stalest-first ordering means no single chunk owns all the backlog --
    # every parallel pod does a comparable share of the urgent work.
    per_chunk_stale = [
        sum(
            1
            for name in _names(deterministic_chunk(ordered, chunk_id=chunk_id, num_chunks=4))
            if name.startswith("stale")
        )
        for chunk_id in range(4)
    ]
    assert max(per_chunk_stale) - min(per_chunk_stale) <= 1
