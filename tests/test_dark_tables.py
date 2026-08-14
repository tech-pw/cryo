from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cryo.db.models import DarkTableCandidate, Source, Table, TableStats
from cryo.telemetry.dark_tables import (
    DARK_TABLE_CONFIRMATION_WINDOWS,
    MIN_SIZE_GB_TO_TRACK,
    detect_dark_table_candidates,
    list_confirmed_dark_table_candidates,
)


def _seed_table_with_stats(
    session,
    *,
    query_count_30d: int = 0,
    total_size_gb: float = 10.0,
    estimated_cost: float | None = 5.0,
    is_active: bool = True,
) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="hive", config={"uri": "x"})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name=f"t_{uuid4().hex[:8]}",
        is_active=is_active,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime(2026, 1, 1, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=10,
            small_files_count=1,
            total_size_gb=total_size_gb,
            query_count_30d=query_count_30d,
            estimated_monthly_s3_cost_usd=estimated_cost,
        )
    )
    session.commit()
    return table


class TestDetectDarkTableCandidates:
    def test_zero_query_table_creates_a_candidate_with_window_one(self, session) -> None:
        table = _seed_table_with_stats(session, query_count_30d=0)
        now = datetime(2026, 1, 2, tzinfo=UTC)

        result = detect_dark_table_candidates(session, now=now)

        assert result.checked == 1
        assert result.newly_zero == 1
        assert result.confirmed_this_run == 0

        candidate = session.query(DarkTableCandidate).filter_by(table_id=table.id).one()
        assert candidate.consecutive_zero_query_windows == 1
        assert candidate.status == "candidate"
        # sqlite's TIMESTAMP round-trip drops tzinfo (Postgres wouldn't) -- compare naively.
        assert candidate.first_zero_query_at == now.replace(tzinfo=None)

    def test_table_with_real_activity_is_never_flagged(self, session) -> None:
        _seed_table_with_stats(session, query_count_30d=42)

        result = detect_dark_table_candidates(session, now=datetime(2026, 1, 2, tzinfo=UTC))

        assert result.checked == 1
        assert result.newly_zero == 0
        assert session.query(DarkTableCandidate).count() == 0

    def test_below_size_floor_is_not_tracked(self, session) -> None:
        _seed_table_with_stats(
            session, query_count_30d=0, total_size_gb=MIN_SIZE_GB_TO_TRACK - 0.1
        )

        result = detect_dark_table_candidates(session, now=datetime(2026, 1, 2, tzinfo=UTC))

        assert result.checked == 0
        assert session.query(DarkTableCandidate).count() == 0

    def test_repeated_zero_query_checks_increment_the_counter(self, session) -> None:
        table = _seed_table_with_stats(session, query_count_30d=0)
        day1 = datetime(2026, 1, 1, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, tzinfo=UTC)
        day3 = datetime(2026, 1, 3, tzinfo=UTC)

        detect_dark_table_candidates(session, now=day1)
        detect_dark_table_candidates(session, now=day2)
        result = detect_dark_table_candidates(session, now=day3)

        candidate = session.query(DarkTableCandidate).filter_by(table_id=table.id).one()
        assert candidate.consecutive_zero_query_windows == 3
        assert candidate.first_zero_query_at == day1.replace(tzinfo=None)
        assert candidate.last_checked_at == day3.replace(tzinfo=None)
        assert result.confirmed_this_run == 1

    def test_activity_returning_deletes_the_candidate_not_just_resets_it(self, session) -> None:
        """A table that goes quiet then becomes active again shouldn't linger as a stale
        candidate with a frozen counter -- and if it goes quiet again later, confirmation
        should restart from window 1, not resume from wherever it left off."""
        table = _seed_table_with_stats(session, query_count_30d=0)
        detect_dark_table_candidates(session, now=datetime(2026, 1, 1, tzinfo=UTC))
        detect_dark_table_candidates(session, now=datetime(2026, 1, 2, tzinfo=UTC))
        assert session.query(DarkTableCandidate).count() == 1

        # Real activity shows up.
        stats = session.get(TableStats, table.id)
        stats.query_count_30d = 5
        session.commit()

        result = detect_dark_table_candidates(session, now=datetime(2026, 1, 3, tzinfo=UTC))
        assert result.reset_to_active == 1
        assert session.query(DarkTableCandidate).count() == 0

        # Goes quiet again -- must restart at window 1, not resume at 3.
        stats.query_count_30d = 0
        session.commit()
        detect_dark_table_candidates(session, now=datetime(2026, 1, 4, tzinfo=UTC))
        candidate = session.query(DarkTableCandidate).filter_by(table_id=table.id).one()
        assert candidate.consecutive_zero_query_windows == 1

    def test_table_deactivated_since_last_check_is_removed_from_candidates(self, session) -> None:
        table = _seed_table_with_stats(session, query_count_30d=0)
        detect_dark_table_candidates(session, now=datetime(2026, 1, 1, tzinfo=UTC))
        assert session.query(DarkTableCandidate).count() == 1

        table.is_active = False
        session.commit()

        detect_dark_table_candidates(session, now=datetime(2026, 1, 2, tzinfo=UTC))
        assert session.query(DarkTableCandidate).count() == 0

    def test_size_and_cost_are_refreshed_on_each_check(self, session) -> None:
        table = _seed_table_with_stats(
            session, query_count_30d=0, total_size_gb=10.0, estimated_cost=5.0
        )
        detect_dark_table_candidates(session, now=datetime(2026, 1, 1, tzinfo=UTC))

        stats = session.get(TableStats, table.id)
        stats.total_size_gb = 20.0
        stats.estimated_monthly_s3_cost_usd = 12.0
        session.commit()

        detect_dark_table_candidates(session, now=datetime(2026, 1, 2, tzinfo=UTC))

        candidate = session.query(DarkTableCandidate).filter_by(table_id=table.id).one()
        assert candidate.total_size_gb == 20.0
        assert candidate.estimated_monthly_cost_usd == 12.0


class TestListConfirmedDarkTableCandidates:
    def test_below_confirmation_threshold_is_excluded(self, session) -> None:
        _seed_table_with_stats(session, query_count_30d=0)
        for i in range(DARK_TABLE_CONFIRMATION_WINDOWS - 1):
            detect_dark_table_candidates(session, now=datetime(2026, 1, 1 + i, tzinfo=UTC))

        assert list_confirmed_dark_table_candidates(session) == []

    def test_at_confirmation_threshold_is_included(self, session) -> None:
        table = _seed_table_with_stats(session, query_count_30d=0, total_size_gb=50.0)
        for i in range(DARK_TABLE_CONFIRMATION_WINDOWS):
            detect_dark_table_candidates(session, now=datetime(2026, 1, 1 + i, tzinfo=UTC))

        confirmed = list_confirmed_dark_table_candidates(session)
        assert len(confirmed) == 1
        assert confirmed[0].table_id == table.id

    def test_ordered_by_size_descending(self, session) -> None:
        small = _seed_table_with_stats(session, query_count_30d=0, total_size_gb=5.0)
        large = _seed_table_with_stats(session, query_count_30d=0, total_size_gb=500.0)
        for i in range(DARK_TABLE_CONFIRMATION_WINDOWS):
            detect_dark_table_candidates(session, now=datetime(2026, 1, 1 + i, tzinfo=UTC))

        confirmed = list_confirmed_dark_table_candidates(session)
        assert [c.table_id for c in confirmed] == [large.id, small.id]

    def test_dismissed_candidates_are_excluded_by_default(self, session) -> None:
        table = _seed_table_with_stats(session, query_count_30d=0)
        for i in range(DARK_TABLE_CONFIRMATION_WINDOWS):
            detect_dark_table_candidates(session, now=datetime(2026, 1, 1 + i, tzinfo=UTC))

        candidate = session.query(DarkTableCandidate).filter_by(table_id=table.id).one()
        candidate.status = "dismissed"
        candidate.dismissed_by = "operator@example.com"
        candidate.dismissed_at = datetime(2026, 1, 5, tzinfo=UTC)
        candidate.dismissed_reason = "quarterly report table"
        session.commit()

        assert list_confirmed_dark_table_candidates(session) == []
        dismissed = list_confirmed_dark_table_candidates(session, status="dismissed")
        assert len(dismissed) == 1
        assert dismissed[0].table_id == table.id
