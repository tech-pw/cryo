"""Audit evidence status is not the same as a verified Iceberg commit."""

from cryo.api.schemas import AuditLogRead


def test_success_without_file_counts_is_unverified() -> None:
    row = AuditLogRead(
        id=1,
        ts="2026-09-28T00:00:00Z",
        action="optimize",
        status="succeeded",
    )
    assert row.effect_status == "unverified"
    assert row.actual_bytes_freed is None


def test_file_counts_are_reported_not_called_a_commit() -> None:
    row = AuditLogRead(
        id=1,
        ts="2026-09-28T00:00:00Z",
        action="optimize",
        status="succeeded",
        input_files_count=10,
        output_files_count=2,
    )
    assert row.effect_status == "files_reported"


def test_failed_status_stays_failed() -> None:
    row = AuditLogRead(
        id=1,
        ts="2026-09-28T00:00:00Z",
        action="optimize",
        status="FAILED",
        input_files_count=10,
        output_files_count=2,
    )
    assert row.effect_status == "failed"
