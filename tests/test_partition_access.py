"""Tests for the partition-access-visualization feature
(docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md).

Covers cryo.scanner.s3_partition_match's pure path-matching logic, the bulk
TablePartitionContext loader, and an end-to-end ingest_bucket assertion that real
PartitionAccessDaily rows land correctly from parsed S3 log lines.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from cryo.db.models import PartitionAccessDaily, Source, Table, TablePartition
from cryo.integrations import s3_bucket_logging
from cryo.scanner import s3_request_cost_ingest as ingest_mod
from cryo.scanner.s3_partition_match import (
    match_partition_path,
    parse_partition_segments,
    partition_path_from_segments,
)
from cryo.security.crypto import encrypt_secret

# ---------------------------------------------------------------------------
# s3_partition_match: pure functions
# ---------------------------------------------------------------------------


def test_parse_partition_segments_extracts_column_value_pairs() -> None:
    assert parse_partition_segments("dt=2026-08-19/00000-0-uuid.parquet") == {"dt": "2026-08-19"}


def test_parse_partition_segments_handles_multi_column_partitioning() -> None:
    assert parse_partition_segments("dt=2026-08-19/hour=14/00000-0-uuid.parquet") == {
        "dt": "2026-08-19",
        "hour": "14",
    }


def test_parse_partition_segments_url_decodes_values() -> None:
    """A partition value containing a "/" (or other reserved char) is URL-encoded in the
    actual S3 key the way any path segment is."""
    assert parse_partition_segments("region=us%2Feast/00000-0-uuid.parquet") == {
        "region": "us/east"
    }


def test_parse_partition_segments_empty_for_unpartitioned_table() -> None:
    assert parse_partition_segments("00000-0-uuid.parquet") == {}


def test_parse_partition_segments_ignores_non_kv_directory_segments() -> None:
    """A residual path segment without "=" isn't a partition directory -- e.g. some writers
    nest an extra directory level that isn't itself a partition value."""
    assert parse_partition_segments("dt=2026-08-19/extra/00000-0-uuid.parquet") == {
        "dt": "2026-08-19"
    }


def test_partition_path_from_segments_drops_trailing_filename() -> None:
    assert partition_path_from_segments("dt=2026-08-19/00000-0-uuid.parquet") == "dt=2026-08-19/"


def test_partition_path_from_segments_multi_segment() -> None:
    assert (
        partition_path_from_segments("dt=2026-08-19/hour=14/00000-0-uuid.parquet")
        == "dt=2026-08-19/hour=14/"
    )


def test_partition_path_from_segments_empty_for_unpartitioned_table() -> None:
    assert partition_path_from_segments("00000-0-uuid.parquet") == ""


def test_match_partition_path_matches_known_partition() -> None:
    known = [{"partition_values": {"dt": "2026-08-19"}}]
    result = match_partition_path("dt=2026-08-19/00000-0-uuid.parquet", known)
    assert result.matched is True
    assert result.partition_path == "dt=2026-08-19/"


def test_match_partition_path_no_match_when_value_differs() -> None:
    known = [{"partition_values": {"dt": "2026-08-18"}}]
    result = match_partition_path("dt=2026-08-19/00000-0-uuid.parquet", known)
    assert result.matched is False
    # Raw parsed path is still recorded even when unmatched -- see PartitionAccessDaily
    # docstring: unmatched is a real, visible state, not a dropped row.
    assert result.partition_path == "dt=2026-08-19/"


def test_match_partition_path_no_match_when_no_known_partitions() -> None:
    result = match_partition_path("dt=2026-08-19/00000-0-uuid.parquet", [])
    assert result.matched is False
    assert result.partition_path == "dt=2026-08-19/"


def test_match_partition_path_subset_match_is_not_a_match() -> None:
    """A key that only parses one of two partition columns must not match -- crediting it to
    the coarser value would misattribute the access to the wrong partition."""
    known = [{"partition_values": {"dt": "2026-08-19", "hour": "14"}}]
    result = match_partition_path("dt=2026-08-19/00000-0-uuid.parquet", known)
    assert result.matched is False


def test_match_partition_path_unpartitioned_table_no_match() -> None:
    result = match_partition_path("00000-0-uuid.parquet", [{"partition_values": {}}])
    assert result.matched is False
    assert result.partition_path == ""


def test_match_partition_path_ignores_candidates_without_partition_values() -> None:
    known = [{"some_other_key": "x"}, {"partition_values": {"dt": "2026-08-19"}}]
    result = match_partition_path("dt=2026-08-19/00000-0-uuid.parquet", known)
    assert result.matched is True


# ---------------------------------------------------------------------------
# _load_table_partition_context
# ---------------------------------------------------------------------------


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str, location: str) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=name,
        is_active=True,
        location=location,
    )
    session.add(table)
    session.flush()
    return table


def test_load_table_partition_context_returns_latest_scan_only(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://bucket/warehouse/db/a")
    session.add_all(
        [
            TablePartition(
                table_id=table.id,
                scan_at=datetime(2026, 8, 1, tzinfo=UTC),
                partition_values={"dt": "2026-08-01"},
                partition_fields=[],
            ),
            TablePartition(
                table_id=table.id,
                scan_at=datetime(2026, 8, 19, tzinfo=UTC),
                partition_values={"dt": "2026-08-19"},
                partition_fields=[],
            ),
        ]
    )
    session.commit()

    context = ingest_mod._load_table_partition_context(
        session, [table.id], {str(table.id): "warehouse/db/a/data/"}
    )

    assert str(table.id) in context
    ctx = context[str(table.id)]
    assert ctx.data_prefix == "warehouse/db/a/data/"
    # Only the 2026-08-19 scan's row -- the stale 2026-08-01 one must not appear.
    assert ctx.known_partitions == [{"partition_values": {"dt": "2026-08-19"}}]


def test_load_table_partition_context_does_not_cross_match_shared_timestamps(session) -> None:
    """Regression test: two tables scanned in the same scheduler tick can share an identical
    scan_at. A naive table_id IN (...) AND scan_at IN (...) query would then also pull in
    table A's OTHER (older) scan rows if that older scan_at happened to equal table B's latest
    -- the tuple_() IN match this function uses must not do that."""
    source = _seed_source(session)
    table_a = _seed_table(session, source, name="a", location="s3://bucket/warehouse/db/a")
    table_b = _seed_table(session, source, name="b", location="s3://bucket/warehouse/db/b")
    shared_scan_at = datetime(2026, 8, 19, tzinfo=UTC)
    session.add_all(
        [
            # table_a's latest scan happens to share a timestamp with table_b's latest scan.
            TablePartition(
                table_id=table_a.id,
                scan_at=shared_scan_at,
                partition_values={"dt": "a-latest"},
                partition_fields=[],
            ),
            # table_a's OWN older scan at a distinct timestamp -- must never appear in the
            # result, even though shared_scan_at exists elsewhere in the overall IN-set.
            TablePartition(
                table_id=table_a.id,
                scan_at=datetime(2026, 8, 1, tzinfo=UTC),
                partition_values={"dt": "a-stale"},
                partition_fields=[],
            ),
            TablePartition(
                table_id=table_b.id,
                scan_at=shared_scan_at,
                partition_values={"dt": "b-latest"},
                partition_fields=[],
            ),
        ]
    )
    session.commit()

    context = ingest_mod._load_table_partition_context(
        session,
        [table_a.id, table_b.id],
        {str(table_a.id): "warehouse/db/a/data/", str(table_b.id): "warehouse/db/b/data/"},
    )

    assert context[str(table_a.id)].known_partitions == [{"partition_values": {"dt": "a-latest"}}]
    assert context[str(table_b.id)].known_partitions == [{"partition_values": {"dt": "b-latest"}}]


def test_load_table_partition_context_empty_for_never_scanned_table(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://bucket/warehouse/db/a")
    session.commit()

    context = ingest_mod._load_table_partition_context(
        session, [table.id], {str(table.id): "warehouse/db/a/data/"}
    )

    assert context == {}


def test_load_table_partition_context_empty_table_ids_short_circuits(session) -> None:
    assert ingest_mod._load_table_partition_context(session, [], {}) == {}


# ---------------------------------------------------------------------------
# end-to-end: ingest_bucket writes PartitionAccessDaily
# ---------------------------------------------------------------------------


def _log_line(operation: str, key: str, *, requester: str = "arn:aws:iam::123:user/etl") -> str:
    return (
        "79a59df900b949e55d96a1e698fbacedfd6e09d98eacf8f8d5218e7cd47ef2be bucket-name "
        f"[06/Jul/2026:12:00:00 +0000] 192.0.2.1 {requester} "
        f"3E57427F3EXAMPLE {operation} {key} "
        f'"GET /bucket-name/{key} HTTP/1.1" 200 - 2662992 2662992 70 10 "-" "S3Console/0.4" - '
        "tx00000000000000000001-00000000-1234abcd-us-east-1"
    )


class _FakeLogBucketClient:
    def __init__(self, *, tree=None, objects=None, bodies=None):
        self._tree = tree or {}
        self._objects = objects or {}
        self._bodies = bodies or {}

    def list_objects_v2(self, *, Bucket, Prefix, Delimiter=None):  # noqa: N803
        if Delimiter:
            children = self._tree.get(Prefix, [])
            return {"CommonPrefixes": [{"Prefix": c} for c in children]}
        keys = self._objects.get(Prefix, [])
        return {"Contents": [{"Key": k} for k in keys]}

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        return _FakePaginator(self)

    def get_object(self, *, Bucket, Key):  # noqa: N803
        return {"Body": _FakeBody(self._bodies.get(Key, b""))}


class _FakePaginator:
    def __init__(self, client: _FakeLogBucketClient) -> None:
        self._client = client

    def paginate(self, *, Bucket, Prefix):  # noqa: N803
        yield self._client.list_objects_v2(Bucket=Bucket, Prefix=Prefix)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _config_with_creds(session, source, *, bucket="lake-bucket"):
    from cryo.db.models import S3BucketLogConfig

    config = S3BucketLogConfig(
        source_id=source.id,
        bucket=bucket,
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_ref=encrypt_secret("shh"),
    )
    session.add(config)
    session.flush()
    return config


def test_ingest_bucket_writes_partition_access_rows_for_matched_get_requests(
    session, monkeypatch
) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TablePartition(
            table_id=table.id,
            scan_at=datetime(2026, 7, 1, tzinfo=UTC),
            partition_values={"dt": "2026-07-07"},
            partition_fields=[],
        )
    )
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    session.commit()

    today = date(2026, 7, 7)
    prefix = f"logs/{today.year}/{today.month:02d}/{today.day:02d}/"
    body = "\n".join(
        [
            _log_line(
                "REST.GET.OBJECT",
                "warehouse/db/a/data/dt=2026-07-07/f1.parquet",
                requester="arn:aws:iam::123:user/alice",
            ),
            _log_line(
                "REST.GET.OBJECT",
                "warehouse/db/a/data/dt=2026-07-07/f2.parquet",
                requester="arn:aws:iam::123:user/alice",
            ),
            # A PUT to the same partition must NOT show up in PartitionAccessDaily -- that
            # table only tracks read access (see its docstring).
            _log_line("REST.PUT.OBJECT", "warehouse/db/a/data/dt=2026-07-07/f3.parquet"),
        ]
    ).encode()
    fake_client = _FakeLogBucketClient(
        tree={
            "logs/": [f"logs/{today.year}/"],
            f"logs/{today.year}/": [f"logs/{today.year}/{today.month:02d}/"],
            f"logs/{today.year}/{today.month:02d}/": [prefix],
        },
        objects={prefix: [f"{prefix}log1.txt"]},
        bodies={f"{prefix}log1.txt": body},
    )
    monkeypatch.setattr(s3_bucket_logging, "s3_client_for_config", lambda cfg: fake_client)
    monkeypatch.setattr(
        ingest_mod,
        "fetch_s3_request_prices",
        lambda region, creds, **kwargs: ingest_mod.S3RequestPriceInfo(
            region=region,
            storage_class="General Purpose",
            price_per_1k_put=0.005,
            price_per_1k_get=0.0004,
            source="aws_price_list_api",
        ),
    )

    result = ingest_mod.ingest_bucket(session, config, now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
    assert result.error is None

    session.flush()
    rows = session.query(PartitionAccessDaily).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.table_id == table.id
    assert row.partition_path == "dt=2026-07-07/"
    assert row.requester_role == "arn:aws:iam::123:user/alice"
    assert row.request_count == 2
    assert row.day == today


def test_ingest_bucket_records_unmatched_partition_path_when_no_scan_yet(
    session, monkeypatch
) -> None:
    """A table with no TablePartition rows yet (never scanned) gets no partition context, so
    no PartitionAccessDaily rows are written for it -- not an error, just nothing to match
    against (see _load_table_partition_context's docstring)."""
    source = _seed_source(session)
    _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    session.commit()

    today = date(2026, 7, 7)
    prefix = f"logs/{today.year}/{today.month:02d}/{today.day:02d}/"
    body = _log_line("REST.GET.OBJECT", "warehouse/db/a/data/dt=2026-07-07/f1.parquet").encode()
    fake_client = _FakeLogBucketClient(
        tree={
            "logs/": [f"logs/{today.year}/"],
            f"logs/{today.year}/": [f"logs/{today.year}/{today.month:02d}/"],
            f"logs/{today.year}/{today.month:02d}/": [prefix],
        },
        objects={prefix: [f"{prefix}log1.txt"]},
        bodies={f"{prefix}log1.txt": body},
    )
    monkeypatch.setattr(s3_bucket_logging, "s3_client_for_config", lambda cfg: fake_client)
    monkeypatch.setattr(
        ingest_mod,
        "fetch_s3_request_prices",
        lambda region, creds, **kwargs: ingest_mod.S3RequestPriceInfo(
            region=region,
            storage_class="General Purpose",
            price_per_1k_put=0.005,
            price_per_1k_get=0.0004,
            source="aws_price_list_api",
        ),
    )

    result = ingest_mod.ingest_bucket(session, config, now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
    assert result.error is None

    session.flush()
    assert session.query(PartitionAccessDaily).count() == 0


def test_prune_partition_access_daily_deletes_rows_past_retention(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://bucket/warehouse/db/a")
    session.add_all(
        [
            PartitionAccessDaily(
                table_id=table.id,
                day=date(2026, 1, 1),
                partition_path="dt=2026-01-01/",
                request_count=5,
                first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
                last_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            PartitionAccessDaily(
                table_id=table.id,
                day=date(2026, 8, 18),
                partition_path="dt=2026-08-18/",
                request_count=5,
                first_seen_at=datetime(2026, 8, 18, tzinfo=UTC),
                last_seen_at=datetime(2026, 8, 18, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    deleted = ingest_mod.prune_partition_access_daily(
        session, retention_days=35, now=datetime(2026, 8, 19, tzinfo=UTC)
    )
    session.commit()

    assert deleted == 1
    remaining = session.query(PartitionAccessDaily).all()
    assert len(remaining) == 1
    assert remaining[0].day == date(2026, 8, 18)
