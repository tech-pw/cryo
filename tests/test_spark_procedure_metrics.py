"""Spark procedure results become bytes only when the procedure reported a size."""

from cryo.engines.spark.procedure_metrics import (
    bytes_freed_from_procedure,
    bytes_removed_from_log,
    with_orphan_dry_run,
)


def test_rewrite_position_deletes_nets_added_bytes() -> None:
    assert (
        bytes_freed_from_procedure(
            "rewrite_position_deletes",
            {"rewritten_bytes_count": 100, "added_bytes_count": 40},
        )
        == 60
    )


def test_expire_with_deleted_files_is_not_invented_as_zero() -> None:
    assert (
        bytes_freed_from_procedure(
            "expire_snapshots",
            {
                "deleted_data_files_count": 3,
                "deleted_position_delete_files_count": 0,
                "deleted_equality_delete_files_count": 0,
                "deleted_manifest_files_count": 1,
                "deleted_manifest_lists_count": 1,
            },
        )
        is None
    )


def test_expire_that_deleted_nothing_is_a_measured_zero() -> None:
    assert (
        bytes_freed_from_procedure(
            "expire_snapshots",
            {
                "deleted_data_files_count": 0,
                "deleted_position_delete_files_count": 0,
                "deleted_equality_delete_files_count": 0,
                "deleted_manifest_files_count": 0,
                "deleted_manifest_lists_count": 0,
            },
        )
        == 0
    )


def test_log_parser_keeps_the_last_measured_result() -> None:
    text = "\n".join(
        [
            'noise [cryo-result] {"ok": true, "bytes_removed": 10}',
            "spark shutdown",
            '[cryo-result] {"ok": true, "bytes_removed": 25}',
        ]
    )
    assert bytes_removed_from_log(text) == 25


def test_orphan_dry_run_is_appended_once() -> None:
    sql = (
        "CALL cdp.system.remove_orphan_files("
        "table => 'cdp.s.t', older_than => TIMESTAMP '2026-09-01 00:00:00')"
    )
    once = with_orphan_dry_run(sql)
    assert once.endswith(", dry_run => true)")
    assert with_orphan_dry_run(once) == once
