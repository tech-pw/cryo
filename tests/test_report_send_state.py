from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.reporting.scheduler import ReportScheduler, last_sent_at, record_sent
from cryo.scheduler.cron import cron_is_due

DAILY_AT_09_UTC = "0 9 * * *"


def test_last_sent_at_is_none_before_anything_is_recorded(session) -> None:
    assert last_sent_at(session, "daily_report:unknown") is None


def test_record_sent_round_trips(session) -> None:
    sent = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

    record_sent(session, "daily_report:abc", sent)

    assert last_sent_at(session, "daily_report:abc") == sent


def test_record_sent_overwrites_the_previous_delivery(session) -> None:
    first = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
    second = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

    record_sent(session, "daily_report:abc", first)
    record_sent(session, "daily_report:abc", second)

    assert last_sent_at(session, "daily_report:abc") == second


def test_a_scheduler_restart_does_not_resend_a_report_already_sent_today(session) -> None:
    """The 2026-09-23 regression: the daily report went to Slack 5 times in one day.

    Send state used to live on the ReportScheduler instance, so a new pod saw no last
    run and cron_is_due() treated an already-past cron time as due. Building a second
    scheduler stands in for that restart.
    """
    sent_at = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
    later_same_day = sent_at + timedelta(hours=4)
    record_sent(session, "daily_report:abc", sent_at)

    ReportScheduler()  # a "restarted" pod holds no send history of its own

    assert not cron_is_due(
        DAILY_AT_09_UTC,
        now=later_same_day,
        last_run_at=last_sent_at(session, "daily_report:abc"),
    )


def test_the_report_still_fires_on_the_next_day(session) -> None:
    record_sent(session, "daily_report:abc", datetime(2026, 9, 23, 9, 0, tzinfo=UTC))

    assert cron_is_due(
        DAILY_AT_09_UTC,
        now=datetime(2026, 9, 24, 9, 0, tzinfo=UTC),
        last_run_at=last_sent_at(session, "daily_report:abc"),
    )


def test_each_report_kind_is_tracked_independently(session) -> None:
    record_sent(session, "daily_report:abc", datetime(2026, 9, 23, 9, 0, tzinfo=UTC))

    assert last_sent_at(session, "production_health_check") is None
    assert last_sent_at(session, "query_cost_report") is None
    assert last_sent_at(session, "shared_s3_report") is None
