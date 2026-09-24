from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pyiceberg.table.snapshots import Operation, Snapshot, Summary

from cryo.scanner.commit_signals import (
    Commit,
    commit_signals,
    commits_from_payload,
    commits_from_snapshots,
    commits_to_payload,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def _daily(days: int, *, added: int = 1000, last_at: datetime | None = None) -> list[Commit]:
    last_at = last_at or NOW - timedelta(hours=2)
    return [
        Commit(
            committed_at=last_at - timedelta(days=days - 1 - i),
            operation="append",
            added_records=added,
            total_records=added * (i + 1),
        )
        for i in range(days)
    ]


def test_compaction_commits_never_count_as_data_arrival() -> None:
    upstream = _daily(8, last_at=NOW - timedelta(days=3))
    compactions = [
        Commit(committed_at=NOW - timedelta(hours=h), operation="replace") for h in (1, 25, 49)
    ]

    signals = commit_signals(upstream + compactions, now=NOW)

    assert signals["system_commits"] == 3 and signals["data_commits"] == 8
    assert signals["last_data_commit_at"] == upstream[-1].committed_at.isoformat()
    assert signals["hours_since_data_commit"] == 72.0
    assert signals["arrival_gap_p90_hours"] == 24.0
    assert signals["arrival_overdue"] is True


def test_on_time_daily_table_is_not_overdue() -> None:
    signals = commit_signals(_daily(8), now=NOW)

    assert signals["arrival_overdue"] is False
    assert signals["row_count_anomaly"] is None


def test_arrival_abstains_without_enough_history() -> None:
    signals = commit_signals(_daily(5, last_at=NOW - timedelta(days=30)), now=NOW)

    assert signals["arrival_gap_p50_hours"] == 24.0
    assert signals["arrival_overdue"] is None


def test_a_commit_that_drops_rows_against_steady_appends_is_flagged() -> None:
    steady = [
        Commit(
            committed_at=NOW - timedelta(days=10 - i),
            operation="append",
            added_records=1000 + (i % 3) * 10,
        )
        for i in range(10)
    ]
    bad = Commit(
        committed_at=NOW,
        operation="overwrite",
        added_records=970_000,
        deleted_records=1_000_000,
    )

    signals = commit_signals([*steady, bad], now=NOW)

    assert signals["last_commit_net_rows"] == -30_000
    assert signals["row_count_anomaly"] == "drop"


def test_constant_history_tolerates_small_wobble_but_flags_a_break() -> None:
    history = [
        Commit(committed_at=NOW - timedelta(days=10 - i), operation="overwrite", added_records=500)
        for i in range(9)
    ]
    wobble = Commit(committed_at=NOW, operation="overwrite", added_records=530)
    spike = Commit(committed_at=NOW, operation="overwrite", added_records=900)

    assert commit_signals([*history, wobble], now=NOW)["row_count_anomaly"] is None
    assert commit_signals([*history, spike], now=NOW)["row_count_anomaly"] == "spike"


def test_live_rows_subtract_position_deletes() -> None:
    commit = Commit(
        committed_at=NOW,
        operation="overwrite",
        total_records=1_000_000,
        total_position_deletes=50_000,
    )

    assert commit_signals([commit], now=NOW)["live_rows"] == 950_000


def test_pyiceberg_snapshots_and_go_payload_produce_the_same_commits() -> None:
    snapshots = [
        Snapshot(
            snapshot_id=i,
            sequence_number=i,
            timestamp_ms=1_700_000_000_000 + i * 1000,
            manifest_list="s3://m",
            summary=Summary(
                Operation.REPLACE if i == 2 else Operation.APPEND,
                **{"added-records": "10", "total-records": str(10 * i)},
            ),
        )
        for i in (3, 1, 2)
    ]

    commits = commits_from_snapshots(snapshots)

    assert [c.operation for c in commits] == ["append", "replace", "append"]
    assert commits_from_payload(commits_to_payload(commits)) == commits
