"""Coverage drop and missing sizes must show up even when jobs succeed."""

from datetime import date

from cryo.reporting.work_picture import ActionPicture, WorkPicture, work_picture_deductions


def _picture(**overrides: object) -> WorkPicture:
    base = dict(
        day=date(2026, 9, 24),
        tables_maintained=800,
        tables_baseline_median=1900,
        byte_actions_succeeded=1500,
        byte_actions_measured=0,
        measured_bytes=0,
        by_action=(ActionPicture("remove_orphan_files", 1500, 2, 0, 0),),
        by_engine=(("spark_emr", 1500),),
    )
    base.update(overrides)
    return WorkPicture(**base)  # type: ignore[arg-type]


def test_half_coverage_and_unmeasured_bytes_are_both_called_out() -> None:
    hits = work_picture_deductions(_picture())
    reasons = " ".join(hit.reason for hit in hits)
    assert any(hit.severe for hit in hits)
    assert "800 tables" in reasons
    assert "measurement" in reasons


def test_small_fleet_is_not_scored_as_a_coverage_collapse() -> None:
    hits = work_picture_deductions(
        _picture(tables_maintained=2, tables_baseline_median=10, byte_actions_succeeded=0)
    )
    assert hits == []
