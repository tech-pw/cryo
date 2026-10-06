"""Daily HTML email renders recorded facts and does not invent a zero reclaim."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.reporting.daily_report import DailyReportData
from cryo.reporting.html_report import render_daily_report_html
from cryo.reporting.impact_signals import DailyImpact, ImpactSignal

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _impact(*, succeeded: int, measured: int, bytes_freed: int) -> ImpactSignal:
    today = DailyImpact(
        day_start=NOW,
        day_end=NOW,
        bytes_freed=bytes_freed,
        actions_succeeded=succeeded,
        tables_touched=1,
        optimize_files_in=0,
        optimize_files_out=0,
        actions_with_bytes_evidence=measured,
        actions_without_bytes_evidence=succeeded - measured,
    )
    return ImpactSignal(
        direction="unknown",
        dod_pct=-90.0,
        wow_pct=None,
        primary_signal="dod",
        today=today,
        dod_baseline_gb=10.0,
        wow_baseline_gb=None,
        measurement_state=today.measurement_state,
    )


def test_html_escapes_narration_and_lists_fleet_counts() -> None:
    data = DailyReportData(
        report_date=NOW,
        status="WATCH",
        total_tables=12,
        healthy_tables=10,
        unhealthy_tables=2,
        tables_needing_compaction=3,
        total_anomalies=0,
        action_items=[{"severity": "high", "text": "<script>", "owner": "a@b"}],
    )

    html = render_daily_report_html(data, "score < 60 & falling")

    assert "Active tables" in html
    assert "12" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "score &lt; 60" in html


def test_unmeasured_reclaim_is_not_rendered_as_zero_gb() -> None:
    data = DailyReportData(
        report_date=NOW,
        status="CLEAR",
        total_tables=1,
        healthy_tables=1,
        unhealthy_tables=0,
        tables_needing_compaction=0,
        total_anomalies=0,
        impact_signal=_impact(succeeded=4, measured=0, bytes_freed=0),
    )

    html = render_daily_report_html(data, "nothing notable")

    assert "unmeasured" in html
    assert "0.0 GB" not in html
    assert "Trend comparison" in html
    assert "-90.0%" not in html
