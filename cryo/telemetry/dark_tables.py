"""Detect tables with sustained zero query activity -- real, quantified storage waste.

Built 2026-08-14 after mining trino_logs.trino_queries alongside cryo.table_stats: 801 active
tables, 66TB, ~$1,622/month estimated S3 cost, zero queries in the last 30 days -- roughly 30%
of the fleet's total estimated storage cost sitting in tables nobody has read. That number is a
single snapshot though, and TableStats.query_count_30d is a single rolling window -- a table
legitimately queried once a quarter (a compliance report, an annual reconciliation) would show
query_count_30d=0 on most days without actually being dead. A single zero-query day is not
evidence of a dark table; only *sustained* zero activity is.

detect_dark_table_candidates is meant to run once a day (see cryo.scheduler.service's telemetry
tick) and increments DarkTableCandidate.consecutive_zero_query_windows by one for every table
whose query_count_30d is currently 0, resetting the counter (and, if a row existed, deleting the
candidate) the moment a table shows real activity again. A table only gets *surfaced* --
included in the candidate list an operator would act on -- once its counter crosses
DARK_TABLE_CONFIRMATION_WINDOWS (3 consecutive daily checks, each independently confirming the
table's whole trailing 30-day window was silent). Detection/surfacing only: nothing here deletes
data, moves it to cold storage, or drops a table. status stays 'candidate' until a separate,
explicit, future action moves it to 'archived' -- this module never writes that value.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import DarkTableCandidate, Table, TableStats

logger = logging.getLogger(__name__)

# How many consecutive daily checks (each checking the table's own trailing 30-day
# query_count_30d) must show zero activity before a table is confirmed dark and included in
# results returned by list_confirmed_dark_table_candidates. Chosen deliberately small (not 7,
# not 30) because query_count_30d is itself already a 30-day rolling window -- 3 consecutive
# confirmations of "the trailing 30 days had zero queries" is already strong evidence, and a
# longer confirmation delay only slows down surfacing real waste without meaningfully reducing
# false positives (a table used quarterly will fail this check on ~90 of its ~91 non-query
# days regardless of whether the threshold is 3 or 30).
DARK_TABLE_CONFIRMATION_WINDOWS = 3

# Below this size, a table's storage cost is unlikely to be worth an operator's attention even
# if it is genuinely dark -- keeps the candidate list focused on real money, matching the >1GB
# floor used in the initial 2026-08-14 investigation.
MIN_SIZE_GB_TO_TRACK = 1.0


@dataclass(frozen=True)
class DarkTableDetectionResult:
    checked: int
    newly_zero: int
    confirmed_this_run: int
    reset_to_active: int


def detect_dark_table_candidates(
    session: Session,
    *,
    now: datetime | None = None,
) -> DarkTableDetectionResult:
    """Run one detection pass: for every active table >= MIN_SIZE_GB_TO_TRACK, advance or reset
    its DarkTableCandidate counter based on the table's current query_count_30d.

    Idempotent per calendar day is NOT enforced here -- callers (the scheduler tick) are
    responsible for not calling this more than once per day, since each call advances the
    counter by exactly one regardless of how much wall-clock time passed since the last call.
    """
    now = now or datetime.now(UTC)

    rows = session.execute(
        select(
            Table.id,
            TableStats.query_count_30d,
            TableStats.total_size_gb,
            TableStats.estimated_monthly_s3_cost_usd,
        )
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True), TableStats.total_size_gb >= MIN_SIZE_GB_TO_TRACK)
    ).all()

    existing = {
        candidate.table_id: candidate
        for candidate in session.execute(select(DarkTableCandidate)).scalars().all()
    }

    checked = 0
    newly_zero = 0
    confirmed_this_run = 0
    reset_to_active = 0

    seen_table_ids: set[uuid.UUID] = set()
    for table_id, query_count_30d, total_size_gb, estimated_cost in rows:
        checked += 1
        seen_table_ids.add(table_id)
        candidate = existing.get(table_id)
        is_zero = (query_count_30d or 0) == 0

        if not is_zero:
            if candidate is not None:
                # Real activity returned -- this table is no longer a dark-table candidate at
                # all, not just "reset to window 1". Deleting (not zeroing) keeps the table
                # list_confirmed_dark_table_candidates callers is reasoning about limited to
                # tables that are currently silent, and re-detection starts the confirmation
                # count fresh if it goes quiet again later.
                session.delete(candidate)
                reset_to_active += 1
            continue

        if candidate is None:
            candidate = DarkTableCandidate(
                table_id=table_id,
                consecutive_zero_query_windows=1,
                first_zero_query_at=now,
                last_checked_at=now,
                total_size_gb=total_size_gb,
                estimated_monthly_cost_usd=estimated_cost,
                status="candidate",
            )
            session.add(candidate)
            newly_zero += 1
        else:
            candidate.consecutive_zero_query_windows += 1
            candidate.last_checked_at = now
            candidate.total_size_gb = total_size_gb
            candidate.estimated_monthly_cost_usd = estimated_cost
            candidate.updated_at = now

        if candidate.consecutive_zero_query_windows >= DARK_TABLE_CONFIRMATION_WINDOWS:
            confirmed_this_run += 1

    # A table that dropped below is_active (deleted/deactivated) or below the size floor since
    # its last check should not linger as a stale candidate -- it's neither confirmable nor
    # dismissible by an operator who can no longer see it in the table list.
    for table_id, candidate in existing.items():
        if table_id not in seen_table_ids:
            session.delete(candidate)

    session.commit()

    logger.info(
        "Dark table detection: checked=%d newly_zero=%d confirmed_this_run=%d reset_to_active=%d",
        checked,
        newly_zero,
        confirmed_this_run,
        reset_to_active,
    )
    return DarkTableDetectionResult(
        checked=checked,
        newly_zero=newly_zero,
        confirmed_this_run=confirmed_this_run,
        reset_to_active=reset_to_active,
    )


def list_confirmed_dark_table_candidates(
    session: Session,
    *,
    status: str = "candidate",
) -> list[DarkTableCandidate]:
    """Candidates that have crossed the confirmation threshold and are still awaiting review --
    what an operator-facing UI/API should actually show, as opposed to every row in the table
    (which includes tables only 1-2 daily checks into being silent, not yet confirmed)."""
    return list(
        session.execute(
            select(DarkTableCandidate)
            .where(
                DarkTableCandidate.status == status,
                DarkTableCandidate.consecutive_zero_query_windows
                >= DARK_TABLE_CONFIRMATION_WINDOWS,
            )
            .order_by(DarkTableCandidate.total_size_gb.desc())
        )
        .scalars()
        .all()
    )
