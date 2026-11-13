"""Reclaimed bytes for every cleanup action, without a second Spark listing (#538)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from cryo.config import Settings
from cryo.db.models import AuditLog, S3BucketStatus, Source, Table, TableCost
from cryo.engines.spark.procedure_metrics import bytes_freed_from_procedure
from cryo.maintenance.reclaim_measure import (
    apply_reclaim_measurement,
    confirm_scan_estimates,
    cutoff_from_sql,
    estimate_expire_bytes,
    estimate_orphan_bytes,
)
from cryo.reporting.impact_signals import _daily_impact, day_bounds, realized_bytes_summary
from cryo.scanner.bucket_status import check_bucket, refresh_bucket_status
from cryo.scanner.cost import account_metadata_cost
from cryo.scanner.orphans import scan_orphans
from tests.test_scanner_cost import FakeDataFile, FakeSnapshot, FakeTable

SCAN_AT = datetime(2026, 11, 10, tzinfo=UTC)
NOW = SCAN_AT + timedelta(hours=6)


def _ms(days_ago: float) -> int:
    return int((SCAN_AT - timedelta(days=days_ago)).timestamp() * 1000)


def _expire_ledger() -> dict[str, Any]:
    """Three snapshots: s1 (20d) holds a.parquet only, s2 (10d) holds b only, s3 (1d) live."""
    table = FakeTable(
        [
            FakeSnapshot(1, _ms(20), [FakeDataFile("s3://b/t/data/a.parquet", 1000)]),
            FakeSnapshot(2, _ms(10), [FakeDataFile("s3://b/t/data/b.parquet", 300)]),
            FakeSnapshot(3, _ms(1), [FakeDataFile("s3://b/t/data/live.parquet", 5000)]),
        ],
        {},
    )
    accounting = account_metadata_cost(table, scan_at=SCAN_AT, settings=Settings(_env_file=None))
    ledger: dict[str, Any] = accounting.raw["expire_ledger"]
    return ledger


def test_scan_ledger_keys_dead_bytes_by_their_newest_snapshot() -> None:
    ledger = _expire_ledger()

    assert ledger["snapshot_ts_ms"] == [_ms(20), _ms(10), _ms(1)]
    assert ledger["dead_bytes_by_last_ref_ms"] == [[_ms(20), 1000], [_ms(10), 300]]


@pytest.mark.parametrize(
    ("older_than_days", "retain_last", "expected"),
    [
        (15, 1, 1000),  # only s1 is older than the cutoff
        (5, 1, 1300),  # s1 and s2 expire
        (5, 2, 1000),  # retain_last=2 keeps s2
        (5, 3, 0),  # every snapshot is retained
        (30, 1, 0),  # nothing old enough
    ],
)
def test_expire_bytes_follow_the_actions_own_cutoff(
    older_than_days: int, retain_last: int, expected: int
) -> None:
    freed = estimate_expire_bytes(
        _expire_ledger(),
        older_than=SCAN_AT - timedelta(days=older_than_days),
        retain_last=retain_last,
    )

    assert freed == expected


def test_orphan_bytes_come_from_the_listing_older_than_the_cutoff() -> None:
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "t/data/live.parquet",
                    "Size": 100,
                    "LastModified": SCAN_AT - timedelta(days=9),
                },
                {
                    "Key": "t/data/old.parquet",
                    "Size": 70,
                    "LastModified": SCAN_AT - timedelta(days=9),
                },
                {
                    "Key": "t/data/mid.parquet",
                    "Size": 20,
                    "LastModified": SCAN_AT - timedelta(days=4),
                },
            ]
        }
    ]

    class _Table:
        def location(self) -> str:
            return "s3://b/t/"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("cryo.scanner.orphans.boto3.client", lambda *a, **k: s3)
        result = scan_orphans(
            _Table(),
            source_config={},
            referenced_file_paths={"s3://b/t/data/live.parquet": 100},
            scan_at=SCAN_AT,
            settings=Settings(_env_file=None),
        )

    ledger = result.raw["orphan_ledger"]
    assert result.orphan_bytes == 90
    assert estimate_orphan_bytes(ledger, older_than=SCAN_AT - timedelta(days=7)) == 70
    assert estimate_orphan_bytes(ledger, older_than=SCAN_AT - timedelta(days=2)) == 90


def test_cutoff_is_read_from_either_engines_sql() -> None:
    spark = (
        "CALL c.system.expire_snapshots(table => 'c.s.t', "
        "older_than => TIMESTAMP '2026-11-03 06:00:00', retain_last => 3, stream_results => true)"
    )
    trino = (
        "ALTER TABLE s.t EXECUTE expire_snapshots(retention_threshold => '12h', retain_last => 2)"
    )

    assert cutoff_from_sql(spark, now=NOW) == (datetime(2026, 11, 3, 6, tzinfo=UTC), 3)
    assert cutoff_from_sql(trino, now=NOW) == (NOW - timedelta(hours=12), 2)
    assert cutoff_from_sql(None, now=NOW) == (None, None)


def test_spark_orphan_removal_that_deleted_nothing_is_a_measured_zero() -> None:
    assert bytes_freed_from_procedure("remove_orphan_files", {"orphan_files_deleted": 0}) == 0
    assert bytes_freed_from_procedure("remove_orphan_files", {"orphan_files_deleted": 4}) is None
    assert bytes_freed_from_procedure("remove_orphan_files", {"bytes_removed": 9}) == 9


def _table(session: Any) -> uuid.UUID:
    source = Source(name=f"src-{uuid.uuid4().hex[:6]}", type="rest", config={})
    session.add(source)
    session.flush()
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="s",
            table_name=f"t{uuid.uuid4().hex[:6]}",
            first_seen_at=SCAN_AT,
            is_active=True,
        )
    )
    session.flush()
    return table_id


def _cost(session: Any, table_id: uuid.UUID, *, dead: int = 1300, orphan: int = 90) -> None:
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=SCAN_AT,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=5000,
            dead_bytes=dead,
            metadata_bytes=0,
            orphan_bytes=orphan,
            listed_bytes=190,
            last_full_cost_scan_at=SCAN_AT,
            current_monthly_cost_usd=0.0,
            projected_monthly_cost_usd=0.0,
            projected_savings_usd_month=0.0,
            raw={
                "metadata": {"expire_ledger": _expire_ledger()},
                "orphan_ledger": {
                    "bytes_by_day": [
                        [int((SCAN_AT - timedelta(days=9)).timestamp()) // 86400 * 86400, 70, 1],
                        [int((SCAN_AT - timedelta(days=4)).timestamp()) // 86400 * 86400, 20, 1],
                    ]
                },
            },
        )
    )
    session.flush()


def _row(table_id: uuid.UUID, action: str, freed: int | None = None, **kw: Any) -> AuditLog:
    return AuditLog(
        table_id=table_id,
        job_id=uuid.uuid4(),
        action=action,
        status="OK",
        engine="spark_emr",
        actual_bytes_freed=freed,
        ts=kw.pop("ts", NOW),
        **kw,
    )


def test_spark_expire_gets_scan_estimated_bytes(session: Any) -> None:
    table_id = _table(session)
    _cost(session, table_id)
    row = _row(table_id, "expire_snapshots", result_metrics={"deleted_data_files_count": 2})
    sql = (
        "CALL c.system.expire_snapshots(table => 'c.s.t', "
        f"older_than => TIMESTAMP '{(SCAN_AT - timedelta(days=5)):%Y-%m-%d %H:%M:%S}', "
        "retain_last => 1)"
    )

    apply_reclaim_measurement(session, row, sql=sql, now=NOW)

    assert row.actual_bytes_freed == 1300
    assert row.result_metrics is not None
    assert row.result_metrics["bytes_source"] == "scan_estimate"
    assert row.result_metrics["deleted_data_files_count"] == 2


def test_orphan_removal_gets_listing_bytes_and_respects_the_flag(session: Any) -> None:
    table_id = _table(session)
    _cost(session, table_id)
    sql = (
        "CALL c.system.remove_orphan_files(table => 'c.s.t', "
        f"older_than => TIMESTAMP '{(SCAN_AT - timedelta(days=7)):%Y-%m-%d %H:%M:%S}')"
    )
    row = _row(table_id, "remove_orphan_files")
    apply_reclaim_measurement(session, row, sql=sql, now=NOW, estimate_orphans=True)
    assert row.actual_bytes_freed == 70
    assert row.result_metrics == {
        "bytes_source": "scan_estimate",
        "bytes_estimate": 70,
        "bytes_scan_at": SCAN_AT.isoformat(),
    }

    off = _row(table_id, "remove_orphan_files")
    apply_reclaim_measurement(session, off, sql=sql, now=NOW, estimate_orphans=False)
    assert off.actual_bytes_freed is None


def test_engine_bytes_are_kept_and_labelled(session: Any) -> None:
    table_id = _table(session)
    _cost(session, table_id)
    row = _row(table_id, "expire_snapshots", freed=42)

    apply_reclaim_measurement(session, row, sql=None, now=NOW)

    assert row.actual_bytes_freed == 42
    assert row.result_metrics == {"bytes_source": "engine"}


def test_next_scan_confirms_a_single_estimate(session: Any) -> None:
    table_id = _table(session)
    estimated = _row(
        table_id,
        "expire_snapshots",
        freed=1300,
        result_metrics={"bytes_source": "scan_estimate", "bytes_estimate": 1300},
    )
    session.add(estimated)
    session.flush()

    confirmed = confirm_scan_estimates(
        session,
        table_id=table_id,
        previous_scan_at=SCAN_AT,
        scan_at=SCAN_AT + timedelta(days=1),
        dead_before=1300,
        dead_after=100,
        orphan_before=None,
        orphan_after=None,
        orphan_relisted=False,
    )

    assert confirmed == 1
    assert estimated.actual_bytes_freed == 1200
    assert estimated.result_metrics is not None
    assert estimated.result_metrics["bytes_source"] == "scan_confirmed"
    assert estimated.result_metrics["bytes_estimate"] == 1300


def test_a_drop_writes_outran_leaves_the_estimate(session: Any) -> None:
    table_id = _table(session)
    estimated = _row(
        table_id, "expire_snapshots", freed=1300, result_metrics={"bytes_source": "scan_estimate"}
    )
    session.add(estimated)
    session.flush()

    confirm_scan_estimates(
        session,
        table_id=table_id,
        previous_scan_at=SCAN_AT,
        scan_at=SCAN_AT + timedelta(days=1),
        dead_before=1300,
        dead_after=2000,
        orphan_before=None,
        orphan_after=None,
        orphan_relisted=False,
    )

    assert estimated.actual_bytes_freed == 1300
    assert estimated.result_metrics is not None
    assert estimated.result_metrics["bytes_source"] == "scan_estimate"
    assert estimated.result_metrics["bytes_confirm"] == "inconclusive"


def test_coverage_and_sources_count_expire_as_a_reclaim_action(session: Any) -> None:
    table_id = _table(session)
    session.add_all(
        [
            _row(
                table_id, "expire_snapshots", 1000, result_metrics={"bytes_source": "scan_estimate"}
            ),
            _row(table_id, "remove_orphan_files", 0, result_metrics={"bytes_source": "engine"}),
            _row(table_id, "expire_snapshots", None),
            _row(table_id, "optimize", 500),
            _row(table_id, "compute_statistics", 999),
        ]
    )
    session.flush()

    summary = realized_bytes_summary(session)

    assert summary["bytes"] == 1500
    assert summary["actions_total"] == 4
    assert summary["actions_measured"] == 3
    assert summary["coverage_pct"] == 75.0
    assert summary["by_source"] == {
        "scan_estimate": {"bytes": 1000, "actions": 1},
        "engine": {"bytes": 0, "actions": 1},
        "unlabeled": {"bytes": 500, "actions": 1},
    }


def test_realized_total_equals_the_sum_of_daily_impact(session: Any) -> None:
    table_id = _table(session)
    for days_ago, freed in [(1, 100), (2, 200), (40, 4000)]:
        session.add(_row(table_id, "expire_snapshots", freed, ts=NOW - timedelta(days=days_ago)))
    session.flush()

    daily = sum(_daily_impact(session, *day_bounds(NOW, n)).bytes_freed for n in range(0, 60))

    assert realized_bytes_summary(session)["bytes"] == daily == 4300
    window = realized_bytes_summary(session, start=day_bounds(NOW, 29)[0])
    assert window["bytes"] == 300


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def test_versioned_bucket_without_noncurrent_rule_does_not_reduce_cost() -> None:
    client = MagicMock()
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.get_bucket_lifecycle_configuration.side_effect = _client_error(
        "NoSuchLifecycleConfiguration"
    )

    status = check_bucket(client, "b")

    assert (status.versioning, status.noncurrent_expiration, status.error) == (
        "Enabled",
        False,
        None,
    )


def test_noncurrent_expiration_rule_is_detected() -> None:
    client = MagicMock()
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.get_bucket_lifecycle_configuration.return_value = {
        "Rules": [{"Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 7}}]
    }

    assert check_bucket(client, "b").noncurrent_expiration is True


def test_access_denied_records_unknown(session: Any) -> None:
    client = MagicMock()
    client.get_bucket_versioning.side_effect = _client_error("AccessDenied")
    client.get_bucket_lifecycle_configuration.side_effect = _client_error("AccessDenied")

    row = refresh_bucket_status(session, client, "denied-bucket", refresh_hours=24, now=NOW)

    assert row is not None
    assert row.versioning is None and row.noncurrent_expiration is None
    assert row.error is not None and "AccessDenied" in row.error


def test_bucket_status_is_cached_and_exposed_on_the_cost_api(session: Any) -> None:
    from cryo.db.repositories.datalake import bucket_status_rows

    client = MagicMock()
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.get_bucket_lifecycle_configuration.return_value = {"Rules": []}
    refresh_bucket_status(session, client, "versioned", refresh_hours=24, now=NOW)
    refresh_bucket_status(
        session, client, "versioned", refresh_hours=24, now=NOW + timedelta(hours=1)
    )
    session.flush()

    assert client.get_bucket_versioning.call_count == 1
    assert session.get(S3BucketStatus, "versioned") is not None
    [entry] = bucket_status_rows(session)
    assert entry["bucket"] == "versioned"
    assert entry["deletes_reduce_cost"] is False
