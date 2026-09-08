"""Tests for the P0-remainder discovery layer + P1 ingestion pipeline
(docs/s3-request-cost-tracking.md §4, §5, §6.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from botocore.exceptions import ClientError

from cryo.config import Settings
from cryo.db.models import (
    AWSIntegration,
    S3BucketLogConfig,
    S3RequestCostDaily,
    Source,
    Table,
    TableCost,
)
from cryo.integrations import s3_bucket_logging
from cryo.scanner import s3_request_cost_ingest as ingest_mod
from cryo.security.crypto import encrypt_secret


def _seed_source(session, *, with_integration: bool = False) -> Source:
    source = Source(
        id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"catalog_name": "lake"}
    )
    session.add(source)
    session.flush()
    if with_integration:
        session.add(
            AWSIntegration(
                source_id=source.id,
                bucket="lake-bucket",
                region="us-east-1",
                access_key_id="AKIAEXAMPLE",
                secret_ref=encrypt_secret("shh"),
                enabled=True,
            )
        )
        session.flush()
    return source


def _seed_table(session, source: Source, *, name: str, location: str | None) -> Table:
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


# ---------------------------------------------------------------------------
# §4.1 discovery
# ---------------------------------------------------------------------------


def test_discover_buckets_creates_one_config_row_per_distinct_bucket(session) -> None:
    source = _seed_source(session)
    _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    _seed_table(session, source, name="b", location="s3://lake-bucket/warehouse/db/b")
    _seed_table(session, source, name="c", location="s3://other-bucket/warehouse/db/c")

    configs = s3_bucket_logging.discover_buckets_for_source(session, source.id)

    assert {c.bucket for c in configs} == {"lake-bucket", "other-bucket"}
    assert all(c.logging_status == "unknown" for c in configs)


def test_discover_buckets_is_idempotent(session) -> None:
    source = _seed_source(session)
    _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")

    first = s3_bucket_logging.discover_buckets_for_source(session, source.id)
    session.commit()
    second = s3_bucket_logging.discover_buckets_for_source(session, source.id)

    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id


def test_discover_buckets_seeds_credentials_from_aws_integration(session) -> None:
    source = _seed_source(session, with_integration=True)
    _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")

    configs = s3_bucket_logging.discover_buckets_for_source(session, source.id)

    assert configs[0].access_key_id == "AKIAEXAMPLE"
    assert configs[0].secret_ref is not None


def test_discover_buckets_ignores_tables_with_no_location(session) -> None:
    source = _seed_source(session)
    _seed_table(session, source, name="a", location=None)

    configs = s3_bucket_logging.discover_buckets_for_source(session, source.id)

    assert configs == []


# ---------------------------------------------------------------------------
# §4.2 / §4.4 logging-status check + manual probe
# ---------------------------------------------------------------------------


class _FakeS3Client:
    def __init__(self, *, logging_response=None, list_response=None, raise_error=None):
        self._logging_response = logging_response or {}
        self._list_response = list_response or {}
        self._raise_error = raise_error

    def get_bucket_logging(self, Bucket):  # noqa: N803 - matches boto3's signature
        if self._raise_error:
            raise self._raise_error
        return self._logging_response

    def list_objects_v2(self, **kwargs):
        return self._list_response


class _FakeBotoSession:
    def __init__(self, client):
        self._client = client

    def client(self, name, **kwargs):
        return self._client


def _config_with_creds(session, source, *, bucket="lake-bucket") -> S3BucketLogConfig:
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


def test_check_bucket_logging_with_no_credentials_is_unknown(session) -> None:
    source = _seed_source(session)
    config = S3BucketLogConfig(source_id=source.id, bucket="lake-bucket")
    session.add(config)
    session.flush()

    s3_bucket_logging.check_bucket_logging(config)

    assert config.logging_status == "unknown"
    assert "credentials" in (config.last_error or "")


def test_check_bucket_logging_enabled_autofills_target(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    fake = _FakeS3Client(
        logging_response={
            "LoggingEnabled": {"TargetBucket": "log-bucket", "TargetPrefix": "logs/lake/"}
        }
    )
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    s3_bucket_logging.check_bucket_logging(config)

    assert config.logging_status == "enabled"
    assert config.log_target_bucket == "log-bucket"
    assert config.log_target_prefix == "logs/lake/"


def test_check_bucket_logging_disabled(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    fake = _FakeS3Client(logging_response={})
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    s3_bucket_logging.check_bucket_logging(config)

    assert config.logging_status == "disabled"


def test_check_bucket_logging_access_denied_names_the_permission(session, monkeypatch) -> None:
    from botocore.exceptions import ClientError

    source = _seed_source(session)
    config = _config_with_creds(session, source)
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetBucketLogging"
    )
    fake = _FakeS3Client(raise_error=error)
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    s3_bucket_logging.check_bucket_logging(config)

    assert config.logging_status == "unknown"
    assert "s3:GetBucketLogging" in (config.last_error or "")


def test_check_bucket_logging_respects_manual_override(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.manual_override = True
    config.log_target_bucket = "user-picked-bucket"
    config.log_target_prefix = "user/prefix/"
    fake = _FakeS3Client(
        logging_response={"LoggingEnabled": {"TargetBucket": "aws-reported", "TargetPrefix": "x/"}}
    )
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    s3_bucket_logging.check_bucket_logging(config)

    assert config.log_target_bucket == "user-picked-bucket"
    assert config.log_target_prefix == "user/prefix/"


def test_probe_manual_target_reports_files_found(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/lake/"
    fake = _FakeS3Client(list_response={"KeyCount": 42, "IsTruncated": False})
    monkeypatch.setattr(s3_bucket_logging, "_session", lambda creds: _FakeBotoSession(fake))

    result = s3_bucket_logging.probe_manual_target(config)

    assert result.ok is True
    assert result.files_found == 42


# ---------------------------------------------------------------------------
# Native log-line parsing, operation-tier mapping, and longest-prefix matching
# ---------------------------------------------------------------------------


_SAMPLE_LOG_LINE = (
    "79a59df900b949e55d96a1e698fbacedfd6e09d98eacf8f8d5218e7cd47ef2be bucket-name "
    "[06/Jul/2026:12:00:00 +0000] 192.0.2.1 arn:aws:iam::123456789012:user/test "
    "3E57427F3EXAMPLE {operation} {key} "
    '"GET /bucket-name/{key} HTTP/1.1" 200 - 2662992 2662992 70 10 "-" "S3Console/0.4" - '
    "tx00000000000000000001-00000000-1234abcd-us-east-1"
)


def _log_line(operation: str, key: str) -> str:
    return _SAMPLE_LOG_LINE.format(operation=operation, key=key)


def test_parse_log_line_extracts_operation_and_key() -> None:
    line = _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/data/file1.parquet")
    assert ingest_mod._parse_log_line(line) == (
        "REST.GET.OBJECT",
        "warehouse/db1/table_a/data/file1.parquet",
        "arn:aws:iam::123456789012:user/test",
    )


def test_parse_log_line_returns_none_for_malformed_line() -> None:
    assert ingest_mod._parse_log_line("this is not a valid s3 log line at all") is None


def test_parse_log_line_returns_none_for_dash_placeholders() -> None:
    line = _log_line("-", "-")
    assert ingest_mod._parse_log_line(line) is None


def test_parse_log_line_handles_multi_word_quoted_user_agent() -> None:
    """Regression test: a prior version of S3_LOG_LINE_PATTERN modeled every trailing field
    (referrer, user-agent, version-id, host-id, ...) with a fixed count of optional groups
    anchored at end-of-line. AWS's user-agent field is quoted but frequently contains spaces
    (Hadoop's S3A client, this platform's own primary write path, is a real-world example) --
    that broke the *entire* match for every such line in production, silently producing zero
    parsed rows despite the ingest tick logging success. This is a real captured production
    line (values scrubbed) that reproduced the bug."""
    line = (
        "09de0c77906a1cbd51835983b81c08a5f3250ebe2c378c46d3d2d1ef3b992c21 acme-lake-temp "
        "[09/Jul/2026:21:47:39 +0000] 172.30.145.127 arn:aws:iam::123456789012:user/etl-user "
        "W6KMHGY9G70R8BXD REST.GET.OBJECT warehouse/db1/table_a/data/file1.parquet "
        '"GET /warehouse/db1/table_a/data/file1.parquet HTTP/1.1" 200 - - 12370 14 - '
        '"https://audit.example.org/hadoop/1/op_is_file" '
        '"Hadoop 3.3.6-amzn-3, aws-sdk-java/2.23.18 Linux/6.12.79 '
        "OpenJDK_64-Bit_Server_VM/17.0.19+10-LTS Java/17.0.19 scala/2.12.17 "
        'vendor/Amazon.com_Inc. io/sync http/Apache cfg/retry-mode/legacy ft/s3-transfer" '
        "- hostid123 SigV4 TLS_AES_128_GCM_SHA256 AuthHeader "
        "acme-lake-temp.s3.ap-south-1.amazonaws.com TLSv1.3 - - ap-south-1"
    )
    assert ingest_mod._parse_log_line(line) == (
        "REST.GET.OBJECT",
        "warehouse/db1/table_a/data/file1.parquet",
        "arn:aws:iam::123456789012:user/etl-user",
    )


def test_parse_log_line_returns_none_requester_for_anonymous_dash() -> None:
    """"-" is AWS's placeholder for "not applicable" -- unlike a missing operation/key, a
    missing requester doesn't invalidate the line (anonymous/unsigned requests are routine),
    it just means the requester is unknown."""
    line = _SAMPLE_LOG_LINE.replace(
        "arn:aws:iam::123456789012:user/test", "-"
    ).format(operation="REST.GET.OBJECT", key="warehouse/db1/table_a/data/file1.parquet")
    assert ingest_mod._parse_log_line(line) == (
        "REST.GET.OBJECT",
        "warehouse/db1/table_a/data/file1.parquet",
        None,
    )


def test_operation_tier_maps_get_and_select_to_get() -> None:
    assert ingest_mod._operation_tier("REST.GET.OBJECT") == "get"
    assert ingest_mod._operation_tier("REST.SELECT.OBJECT") == "get"


def test_operation_tier_maps_head() -> None:
    assert ingest_mod._operation_tier("REST.HEAD.OBJECT") == "head"


def test_operation_tier_maps_list() -> None:
    assert ingest_mod._operation_tier("REST.LIST.OBJECTS") == "list"


def test_operation_tier_maps_delete_to_its_own_tier_not_list() -> None:
    """2026-09-08 fix (docs/s3-total-cost-optimization.md P0#1): DELETE and abort-multipart
    are free S3 operations -- they were previously folded into "list" and mispriced at the
    Tier-1 LIST rate, silently combining paid LIST work with free cleanup work into one
    number. DELETE now gets its own tier, priced at $0 (see _PRICING_TIER)."""
    assert ingest_mod._operation_tier("REST.DELETE.OBJECT") == "delete"
    assert ingest_mod._operation_tier("BATCH.DELETE.OBJECT") == "delete"
    assert ingest_mod._operation_tier("REST.DELETE.OBJECT") != "list"


def test_operation_tier_maps_put_post_copy() -> None:
    assert ingest_mod._operation_tier("REST.PUT.OBJECT") == "put"
    assert ingest_mod._operation_tier("REST.POST.OBJECT") == "put"
    assert ingest_mod._operation_tier("REST.COPY.OBJECT") == "put"


def test_operation_tier_falls_back_to_other() -> None:
    assert ingest_mod._operation_tier("WEBSITE.GET.OBJECT") == "other"


def test_match_table_prefix_picks_longest_match_among_siblings() -> None:
    index = ingest_mod._build_prefix_index(
        [
            ("t-short", "warehouse/db1/"),
            ("t-long", "warehouse/db1/table_a/"),
        ]
    )
    assert (
        ingest_mod._match_table_prefix("warehouse/db1/table_a/data/f1.parquet", index) == "t-long"
    )


def test_match_table_prefix_returns_none_when_no_prefix_covers_key() -> None:
    index = ingest_mod._build_prefix_index([("t1", "warehouse/db1/table_a/")])
    assert ingest_mod._match_table_prefix("warehouse/other_db/table_z/f.parquet", index) is None


# ---------------------------------------------------------------------------
# locate_date_partition -- directory-walk discovery
# ---------------------------------------------------------------------------


class _FakeListClient:
    """Simulates a tree of CommonPrefixes, keyed by the exact Prefix passed in."""

    def __init__(self, tree: dict[str, list[str]]):
        self.tree = tree

    def list_objects_v2(self, *, Bucket, Prefix, Delimiter):  # noqa: N803
        children = self.tree.get(Prefix, [])
        return {"CommonPrefixes": [{"Prefix": c} for c in children]}


def test_locate_date_partition_finds_direct_year_month_day() -> None:
    client = _FakeListClient(
        {
            "logs/": ["logs/2026/"],
            "logs/2026/": ["logs/2026/07/"],
            "logs/2026/07/": ["logs/2026/07/07/"],
        }
    )
    location = ingest_mod.locate_date_partition(
        client, bucket="log-bucket", prefix="logs/", target_date=date(2026, 7, 7)
    )
    assert location == "logs/2026/07/07/"


def test_locate_date_partition_skips_account_id_segment() -> None:
    client = _FakeListClient(
        {
            "logs/": ["logs/123456789012/"],
            "logs/123456789012/": ["logs/123456789012/2026/"],
            "logs/123456789012/2026/": ["logs/123456789012/2026/07/"],
            "logs/123456789012/2026/07/": ["logs/123456789012/2026/07/07/"],
        }
    )
    location = ingest_mod.locate_date_partition(
        client, bucket="log-bucket", prefix="logs/", target_date=date(2026, 7, 7)
    )
    assert location == "logs/123456789012/2026/07/07/"


def test_locate_date_partition_returns_none_for_flat_layout() -> None:
    # Flat/unpartitioned delivery: no subfolders at all, just files (not represented in
    # CommonPrefixes at all since ListObjectsV2 only returns folder-shaped prefixes here).
    client = _FakeListClient({"logs/": []})
    location = ingest_mod.locate_date_partition(
        client, bucket="log-bucket", prefix="logs/", target_date=date(2026, 7, 7)
    )
    assert location is None


def test_locate_date_partition_finds_source_bucket_among_many_siblings() -> None:
    """Regression test: a shared, org-wide logging bucket receives deliveries for many source
    buckets under the same account/region prefix (AWS's documented "Partitioned" key format:
    [prefix][account]/[region]/[source-bucket]/[yyyy]/[mm]/[dd]/...). Before this fix,
    locate_date_partition only handled a *single* non-date-shaped child at a branch point --
    with dozens/hundreds of sibling source-bucket folders (the common real-world case), it gave
    up immediately and reported no logs found at all, for every bucket."""
    client = _FakeListClient(
        {
            "logs/": ["logs/123456789012/"],
            "logs/123456789012/": ["logs/123456789012/us-east-1/"],
            "logs/123456789012/us-east-1/": [
                "logs/123456789012/us-east-1/acme-etl-staging/",
                "logs/123456789012/us-east-1/acme-backup-archive/",
                "logs/123456789012/us-east-1/acme-lake-gold/",
                "logs/123456789012/us-east-1/acme-lake-silver/",
            ],
            "logs/123456789012/us-east-1/acme-lake-gold/": [
                "logs/123456789012/us-east-1/acme-lake-gold/2026/"
            ],
            "logs/123456789012/us-east-1/acme-lake-gold/2026/": [
                "logs/123456789012/us-east-1/acme-lake-gold/2026/07/"
            ],
            "logs/123456789012/us-east-1/acme-lake-gold/2026/07/": [
                "logs/123456789012/us-east-1/acme-lake-gold/2026/07/07/"
            ],
        }
    )
    location = ingest_mod.locate_date_partition(
        client,
        bucket="log-bucket",
        prefix="logs/",
        target_date=date(2026, 7, 7),
        source_bucket="acme-lake-gold",
    )
    assert location == "logs/123456789012/us-east-1/acme-lake-gold/2026/07/07/"


def test_locate_date_partition_without_source_bucket_still_fails_on_many_siblings() -> None:
    """Without source_bucket, behavior for a many-sibling branch is unchanged (returns None) --
    confirms the fix is additive, not a silent behavior change for callers that don't pass it."""
    client = _FakeListClient(
        {
            "logs/": ["logs/a/", "logs/b/", "logs/c/"],
        }
    )
    location = ingest_mod.locate_date_partition(
        client, bucket="log-bucket", prefix="logs/", target_date=date(2026, 7, 7)
    )
    assert location is None


# ---------------------------------------------------------------------------
# _aggregate_log_objects -- native read + parse + longest-prefix match + aggregate
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakePaginator:
    def __init__(self, client: _FakeLogBucketClient):
        self._client = client

    def paginate(self, *, Bucket, Prefix):  # noqa: N803
        yield self._client.list_objects_v2(Bucket=Bucket, Prefix=Prefix)


class _FakeLogBucketClient:
    """Combines the S3 call shapes ingest_bucket's native path needs: list_objects_v2 with
    Delimiter='/' for locate_date_partition's tree walk, list_objects_v2 without a delimiter
    (via get_paginator) for listing objects under a partition, and get_object for bodies.

    ``archived_keys`` simulates objects already transitioned to GLACIER/DEEP_ARCHIVE by a
    bucket lifecycle rule -- get_object raises the same InvalidObjectState ClientError AWS
    does, live-confirmed 2026-09-08 as the root cause of a 3-week ingestion stall."""

    def __init__(
        self,
        *,
        tree: dict[str, list[str]] | None = None,
        objects: dict[str, list[str]] | None = None,
        bodies: dict[str, bytes] | None = None,
        archived_keys: set[str] | None = None,
    ):
        self._tree = tree or {}
        self._objects = objects or {}
        self._bodies = bodies or {}
        self._archived_keys = archived_keys or set()

    def list_objects_v2(self, *, Bucket, Prefix, Delimiter=None):  # noqa: N803
        if Delimiter:
            children = self._tree.get(Prefix, [])
            return {"CommonPrefixes": [{"Prefix": c} for c in children]}
        keys = self._objects.get(Prefix, [])
        return {"Contents": [{"Key": k} for k in keys]}

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_objects_v2"
        return _FakePaginator(self)

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key in self._archived_keys:
            raise ClientError(
                {"Error": {"Code": "InvalidObjectState", "Message": "archived"}}, "GetObject"
            )
        return {"Body": _FakeBody(self._bodies.get(Key, b""))}


def test_aggregate_log_objects_matches_and_counts_by_table_date_tier() -> None:
    log_date = date(2026, 7, 7)
    body = "\n".join(
        [
            _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/data/f1.parquet"),
            _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/data/f2.parquet"),
            _log_line("REST.PUT.OBJECT", "warehouse/other/unmatched.parquet"),
        ]
    ).encode()
    client = _FakeLogBucketClient(
        objects={"logs/2026/07/07/": ["logs/2026/07/07/log1.txt"]},
        bodies={"logs/2026/07/07/log1.txt": body},
    )

    outcome = ingest_mod._aggregate_log_objects(
        client,
        log_target_bucket="log-bucket",
        located={log_date: "s3a://log-bucket/logs/2026/07/07/"},
        table_prefixes=[("table-a-id", "warehouse/db1/table_a/")],
        max_objects_per_day=100,
        max_workers=4,
    )

    assert outcome.truncated_dates == []
    assert outcome.counts[("table-a-id", log_date, "get")] == 2
    assert outcome.counts[(None, log_date, "put")] == 1


def test_aggregate_log_objects_skips_unparseable_lines() -> None:
    log_date = date(2026, 7, 7)
    body = (
        b"not a valid log line\n"
        + _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/f.parquet").encode()
    )
    client = _FakeLogBucketClient(
        objects={"logs/2026/07/07/": ["logs/2026/07/07/log1.txt"]},
        bodies={"logs/2026/07/07/log1.txt": body},
    )

    outcome = ingest_mod._aggregate_log_objects(
        client,
        log_target_bucket="log-bucket",
        located={log_date: "s3a://log-bucket/logs/2026/07/07/"},
        table_prefixes=[],
        max_objects_per_day=100,
        max_workers=4,
    )

    assert sum(outcome.counts.values()) == 1


def test_aggregate_log_objects_truncates_at_max_objects_per_day() -> None:
    log_date = date(2026, 7, 7)
    keys = [f"logs/2026/07/07/log{i}.txt" for i in range(5)]
    bodies = {
        k: _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/f.parquet").encode() for k in keys
    }
    client = _FakeLogBucketClient(objects={"logs/2026/07/07/": keys}, bodies=bodies)

    outcome = ingest_mod._aggregate_log_objects(
        client,
        log_target_bucket="log-bucket",
        located={log_date: "s3a://log-bucket/logs/2026/07/07/"},
        table_prefixes=[],
        max_objects_per_day=2,
        max_workers=4,
    )

    assert outcome.truncated_dates == [log_date]
    assert sum(outcome.counts.values()) == 2


def test_aggregate_log_objects_skips_archived_objects_instead_of_raising() -> None:
    """Direct regression test for the 2026-09-08 incident: gold/silver/bronze-layer ingestion
    stuck at last_ingested_date=2026-08-19 for 3+ weeks because a single DEEP_ARCHIVE object
    (the shared logging bucket transitions objects after 7 days) raised InvalidObjectState and
    aborted the whole pass. An archived object must be skipped, not fatal -- the rest of the
    window's readable objects still get counted."""
    log_date = date(2026, 7, 7)
    readable_body = _log_line("REST.GET.OBJECT", "warehouse/db1/table_a/f.parquet").encode()
    client = _FakeLogBucketClient(
        objects={"logs/2026/07/07/": ["logs/2026/07/07/archived.txt", "logs/2026/07/07/ok.txt"]},
        bodies={"logs/2026/07/07/ok.txt": readable_body},
        archived_keys={"logs/2026/07/07/archived.txt"},
    )

    outcome = ingest_mod._aggregate_log_objects(
        client,
        log_target_bucket="log-bucket",
        located={log_date: "s3a://log-bucket/logs/2026/07/07/"},
        table_prefixes=[("table-a-id", "warehouse/db1/table_a/")],
        max_objects_per_day=100,
        max_workers=4,
    )

    assert outcome.archived_object_count == 1
    assert outcome.counts[("table-a-id", log_date, "get")] == 1


def test_aggregate_log_objects_reraises_non_archived_client_errors() -> None:
    """Safety boundary: only InvalidObjectState is treated as skippable -- a genuine access
    error (permissions, throttling, etc.) must still surface as a real failure, not be
    silently swallowed alongside archived objects."""
    log_date = date(2026, 7, 7)

    class _DenyingClient(_FakeLogBucketClient):
        def get_object(self, *, Bucket, Key):  # noqa: N803
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetObject")

    client = _DenyingClient(objects={"logs/2026/07/07/": ["logs/2026/07/07/log1.txt"]})

    import pytest

    with pytest.raises(ClientError):
        ingest_mod._aggregate_log_objects(
            client,
            log_target_bucket="log-bucket",
            located={log_date: "s3a://log-bucket/logs/2026/07/07/"},
            table_prefixes=[],
            max_objects_per_day=100,
            max_workers=4,
        )


# ---------------------------------------------------------------------------
# §5 end-to-end ingest_bucket + §6.3 rollup
# ---------------------------------------------------------------------------


def test_coordinator_has_no_auth_when_password_unset() -> None:
    settings = Settings(telemetry_trino_url="https://trino.local:443")
    coordinator = ingest_mod._coordinator(settings, catalog="hive", schema="cryo_s3_logs")
    assert coordinator is not None
    assert coordinator.auth is None


def test_coordinator_attaches_basic_auth_when_password_set() -> None:
    settings = Settings(
        telemetry_trino_url="https://trino.local:443",
        telemetry_trino_user="phoenix_user",
        telemetry_trino_password="secret123",  # noqa: S106 - test fixture, not a real credential
    )
    coordinator = ingest_mod._coordinator(settings, catalog="hive", schema="cryo_s3_logs")
    assert coordinator is not None
    assert coordinator.user == "phoenix_user"
    assert coordinator.auth is not None
    assert coordinator.auth._username == "phoenix_user"
    assert coordinator.auth._password == "secret123"  # noqa: S105 - assertion against test fixture


def test_coordinator_defaults_user_to_cryo_when_only_password_set() -> None:
    settings = Settings(
        telemetry_trino_url="https://trino.local:443",
        telemetry_trino_password="secret123",  # noqa: S106 - test fixture, not a real credential
    )
    coordinator = ingest_mod._coordinator(settings, catalog="hive", schema="cryo_s3_logs")
    assert coordinator is not None
    assert coordinator.user == "cryo"
    assert coordinator.auth is not None
    assert coordinator.auth._username == "cryo"


def test_ingest_bucket_upserts_daily_rows_and_rolls_up_table_cost(session, monkeypatch) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    session.commit()

    today = date(2026, 7, 7)
    prefix = f"logs/{today.year}/{today.month:02d}/{today.day:02d}/"
    # 5,000 matched GETs so the resulting cost survives round(..., 4) as a non-zero value.
    get_lines = [
        _log_line("REST.GET.OBJECT", f"warehouse/db/a/data/f{i}.parquet") for i in range(5_000)
    ]
    unmatched_line = _log_line("REST.LIST.OBJECTS", "warehouse/unmatched/prefix/")
    body = "\n".join([*get_lines, unmatched_line]).encode()
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
    assert result.rows_ingested == 2
    assert result.tables_matched == 1
    assert result.unmatched_requests == 1

    session.flush()
    daily_rows = session.query(S3RequestCostDaily).all()
    assert len(daily_rows) == 2
    matched = next(r for r in daily_rows if r.table_id == table.id)
    assert matched.request_count == 5_000
    assert matched.operation_tier == "get"

    session.refresh(table)
    cost = session.get(TableCost, table.id)
    assert cost.get_requests_30d == 5_000
    assert cost.put_requests_30d == 0
    assert cost.request_cost_usd_month is not None
    assert cost.request_cost_usd_month > 0

    assert config.last_ingested_date == date(2026, 7, 7)
    assert config.last_error is None


def test_ingest_bucket_advances_watermark_despite_an_archived_log_object(
    session, monkeypatch
) -> None:
    """End-to-end regression test for the 2026-09-08 incident: with even one already-archived
    (DEEP_ARCHIVE) object in the window, ingest_bucket must still complete and advance
    last_ingested_date -- not abort with an unhandled InvalidObjectState and leave the
    watermark stuck retrying the same unreadable window forever. Live-confirmed: 3 buckets
    stuck at last_ingested_date=2026-08-19 for 3+ weeks from exactly this failure mode."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    config.last_ingested_date = date(2026, 8, 19)  # matches the live incident's stuck watermark
    session.commit()

    today = date(2026, 7, 7)
    prefix = f"logs/{today.year}/{today.month:02d}/{today.day:02d}/"
    readable_body = "\n".join(
        [_log_line("REST.GET.OBJECT", "warehouse/db/a/data/f1.parquet")]
    ).encode()
    fake_client = _FakeLogBucketClient(
        tree={
            "logs/": [f"logs/{today.year}/"],
            f"logs/{today.year}/": [f"logs/{today.year}/{today.month:02d}/"],
            f"logs/{today.year}/{today.month:02d}/": [prefix],
        },
        objects={prefix: [f"{prefix}archived.txt", f"{prefix}log1.txt"]},
        bodies={f"{prefix}log1.txt": readable_body},
        archived_keys={f"{prefix}archived.txt"},
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
    assert result.archived_object_count == 1
    # The one readable object's GET still gets counted -- an archived sibling doesn't zero out
    # the whole day.
    assert result.rows_ingested >= 1
    assert config.last_ingested_date == date(2026, 7, 7)
    assert config.last_error is None


def test_ingest_bucket_caps_catchup_window_when_watermark_is_badly_stuck(
    session, monkeypatch
) -> None:
    """Second half of the 2026-09-08 incident: the archived-object fix alone wasn't enough --
    a watermark stuck 20 days behind (window_start = min(last_ingested_date, ...) has no lower
    bound) tried to list+read every log object across all 20 days in a single pass and timed
    out live, even with archived objects being skipped rather than fatal (each one still costs
    a GetObject round-trip). This asserts the fix: window_end caps how far back one pass
    reaches (_MAX_CATCHUP_DAYS), and the watermark only advances to what was actually
    processed -- not all the way to today -- so a badly-stuck bucket catches up incrementally
    over several daily ticks instead of needing one unbounded run to succeed."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    stuck_since = date(2026, 8, 19)
    config.last_ingested_date = stuck_since
    session.commit()

    today = date(2026, 9, 8)  # 20 days after stuck_since -- matches the live incident's gap
    # A readable log object exists on the first candidate date only; the fake client has no
    # tree/objects entries for any later date, so locate_date_partition simply finds nothing
    # for dates beyond the cap -- proving those dates were never even attempted this run.
    prefix = f"logs/{stuck_since.year}/{stuck_since.month:02d}/{stuck_since.day:02d}/"
    readable_body = "\n".join(
        [_log_line("REST.GET.OBJECT", "warehouse/db/a/data/f1.parquet")]
    ).encode()
    fake_client = _FakeLogBucketClient(
        tree={
            "logs/": [f"logs/{stuck_since.year}/"],
            f"logs/{stuck_since.year}/": [f"logs/{stuck_since.year}/{stuck_since.month:02d}/"],
            f"logs/{stuck_since.year}/{stuck_since.month:02d}/": [prefix],
        },
        objects={prefix: [f"{prefix}log1.txt"]},
        bodies={f"{prefix}log1.txt": readable_body},
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

    result = ingest_mod.ingest_bucket(
        session, config, now=datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    )

    assert result.error is None
    expected_through = stuck_since + timedelta(days=ingest_mod._MAX_CATCHUP_DAYS)
    assert expected_through < today  # sanity: the cap genuinely falls short of today
    assert result.through_date == expected_through
    assert config.last_ingested_date == expected_through
    # Not "today" -- a full 20-day catch-up must not be silently claimed as done in one pass.
    assert config.last_ingested_date != today


def test_ingest_bucket_without_credentials_returns_error(session) -> None:
    source = _seed_source(session)
    config = S3BucketLogConfig(source_id=source.id, bucket="lake-bucket")
    config.log_target_bucket = "log-bucket"
    config.enabled = True
    session.add(config)
    session.commit()

    result = ingest_mod.ingest_bucket(session, config)

    assert result.error is not None
    assert "credentials" in result.error


def test_ingest_bucket_without_located_partitions_returns_error(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.log_target_prefix = "logs/"
    config.enabled = True
    session.commit()

    monkeypatch.setattr(
        s3_bucket_logging, "s3_client_for_config", lambda cfg: _FakeListClient({"logs/": []})
    )

    result = ingest_mod.ingest_bucket(session, config)

    assert result.error is not None
    assert "partitioned" in result.error


def test_recompute_table_cost_rollup_ignores_rows_outside_30d_window(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add_all(
        [
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date() - timedelta(days=5),
                operation_tier="get",
                request_count=10,
                cost_usd=0.01,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date() - timedelta(days=45),
                operation_tier="get",
                request_count=999,
                cost_usd=99.0,
                pricing_source="aws_price_list_api",
            ),
        ]
    )
    session.commit()

    ingest_mod.recompute_table_cost_rollup(session, table.id, now=now)

    cost = session.get(TableCost, table.id)
    assert cost.get_requests_30d == 10
    assert cost.request_cost_pricing_source == "live"


def test_recompute_table_cost_rollup_labels_fallback_pricing(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add(
        S3RequestCostDaily(
            source_id=source.id,
            bucket="lake-bucket",
            table_id=table.id,
            day=now.date(),
            operation_tier="put",
            request_count=10,
            cost_usd=0.05,
            pricing_source="fallback_default",
        )
    )
    session.commit()

    ingest_mod.recompute_table_cost_rollup(session, table.id, now=now)

    cost = session.get(TableCost, table.id)
    assert cost.put_requests_30d == 10
    assert cost.request_cost_pricing_source == "fallback_default"


def test_recompute_table_cost_rollup_separates_head_from_get(session) -> None:
    """2026-09-08 fix (docs/s3-total-cost-optimization.md P0#2/#3): HEAD used to be folded
    into get_requests_30d -- a real HEAD regression (found live: 57.7% of tables got MORE
    HEAD requests after maintenance) had no dedicated field to surface. HEAD and GET must
    roll up independently."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add_all(
        [
            S3RequestCostDaily(
                source_id=source.id, bucket="lake-bucket", table_id=table.id, day=now.date(),
                operation_tier="get", request_count=100, cost_usd=0.04,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id, bucket="lake-bucket", table_id=table.id, day=now.date(),
                operation_tier="head", request_count=25, cost_usd=0.01,
                pricing_source="aws_price_list_api",
            ),
        ]
    )
    session.commit()

    ingest_mod.recompute_table_cost_rollup(session, table.id, now=now)

    cost = session.get(TableCost, table.id)
    assert cost.get_requests_30d == 100
    assert cost.head_requests_30d == 25


def test_recompute_table_cost_rollup_tracks_delete_requests_separately(session) -> None:
    """DELETE is free but still tracked for operational-load visibility (throttling risk,
    cleanup volume) -- it must never be counted toward get/put/list_requests_30d."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0,
            projected_monthly_cost_usd=0,
            projected_savings_usd_month=0,
        )
    )
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add(
        S3RequestCostDaily(
            source_id=source.id, bucket="lake-bucket", table_id=table.id, day=now.date(),
            operation_tier="delete", request_count=500, cost_usd=0.0,
            pricing_source="aws_price_list_api",
        )
    )
    session.commit()

    ingest_mod.recompute_table_cost_rollup(session, table.id, now=now)

    cost = session.get(TableCost, table.id)
    assert cost.delete_requests_30d == 500
    assert cost.list_requests_30d == 0
    assert cost.get_requests_30d == 0
    assert cost.put_requests_30d == 0
    assert cost.request_cost_usd_month == 0.0


def test_delete_operation_tier_is_priced_at_zero() -> None:
    """The pricing-tier lookup itself, not just the rollup: 'delete' must map to the 'free'
    pricing tier, not fall through to a Tier-1 default."""
    assert ingest_mod._PRICING_TIER["delete"] == "free"


# ---------------------------------------------------------------------------
# §10 scheduling tick + §6.3 retention
# ---------------------------------------------------------------------------


def test_run_s3_ingest_tick_skips_disabled_buckets(session, monkeypatch) -> None:
    source = _seed_source(session)
    enabled = S3BucketLogConfig(source_id=source.id, bucket="enabled-bucket", enabled=True)
    disabled = S3BucketLogConfig(source_id=source.id, bucket="disabled-bucket", enabled=False)
    session.add_all([enabled, disabled])
    session.commit()

    calls: list[str] = []
    monkeypatch.setattr(
        ingest_mod,
        "ingest_bucket",
        lambda session, config, **kwargs: (
            calls.append(config.bucket) or ingest_mod.IngestResult(0, 0, 0)
        ),
    )

    succeeded = ingest_mod.run_s3_ingest_tick(session)

    assert calls == ["enabled-bucket"]
    assert succeeded == 1


def test_run_s3_ingest_tick_records_error_without_stopping_other_buckets(
    session, monkeypatch
) -> None:
    source = _seed_source(session)
    failing = S3BucketLogConfig(source_id=source.id, bucket="failing-bucket", enabled=True)
    ok = S3BucketLogConfig(source_id=source.id, bucket="ok-bucket", enabled=True)
    session.add_all([failing, ok])
    session.commit()

    def fake_ingest(session, config, **kwargs):
        if config.bucket == "failing-bucket":
            return ingest_mod.IngestResult(0, 0, 0, error="boom")
        return ingest_mod.IngestResult(1, 1, 0)

    monkeypatch.setattr(ingest_mod, "ingest_bucket", fake_ingest)

    succeeded = ingest_mod.run_s3_ingest_tick(session)

    assert succeeded == 1
    session.refresh(failing)
    assert failing.last_error == "boom"
    session.refresh(ok)
    assert ok.last_error is None


def test_run_s3_ingest_tick_skips_bucket_already_ingested_today(session, monkeypatch) -> None:
    """Regression test for the bucket-starvation bug: a bucket whose last_ingested_date is
    already today should be skipped so it can't monopolize every tick's time budget and starve
    every other enabled bucket. Observed live: one slow bucket kept re-running from scratch on
    every scheduler restart while 6 newly-enabled buckets never got a single turn."""
    source = _seed_source(session)
    already_done = S3BucketLogConfig(
        source_id=source.id,
        bucket="already-done-today",
        enabled=True,
        last_ingested_date=date(2026, 7, 10),
    )
    never_done = S3BucketLogConfig(
        source_id=source.id, bucket="never-done", enabled=True, last_ingested_date=None
    )
    session.add_all([already_done, never_done])
    session.commit()

    calls: list[str] = []
    monkeypatch.setattr(
        ingest_mod,
        "ingest_bucket",
        lambda session, config, **kwargs: (
            calls.append(config.bucket) or ingest_mod.IngestResult(0, 0, 0)
        ),
    )

    succeeded = ingest_mod.run_s3_ingest_tick(session, now=datetime(2026, 7, 10, 12, 0, tzinfo=UTC))

    assert calls == ["never-done"]
    assert succeeded == 1


def test_run_s3_ingest_tick_processes_stale_bucket_again_next_day(session, monkeypatch) -> None:
    source = _seed_source(session)
    config = S3BucketLogConfig(
        source_id=source.id,
        bucket="daily-bucket",
        enabled=True,
        last_ingested_date=date(2026, 7, 9),
    )
    session.add(config)
    session.commit()

    calls: list[str] = []
    monkeypatch.setattr(
        ingest_mod,
        "ingest_bucket",
        lambda session, config, **kwargs: (
            calls.append(config.bucket) or ingest_mod.IngestResult(0, 0, 0)
        ),
    )

    succeeded = ingest_mod.run_s3_ingest_tick(session, now=datetime(2026, 7, 10, 0, 5, tzinfo=UTC))

    assert calls == ["daily-bucket"]
    assert succeeded == 1


def test_run_s3_ingest_tick_orders_never_ingested_buckets_first(session, monkeypatch) -> None:
    """Even among buckets that all still need work, the ones furthest behind (NULL, then
    oldest last_ingested_date) must be tried first -- otherwise an unordered scan could let an
    arbitrary bucket keep winning a tick's limited time budget while a never-ingested bucket
    waits indefinitely."""
    source = _seed_source(session)
    stale = S3BucketLogConfig(
        source_id=source.id,
        bucket="stale-bucket",
        enabled=True,
        last_ingested_date=date(2026, 6, 1),
    )
    never = S3BucketLogConfig(
        source_id=source.id, bucket="never-bucket", enabled=True, last_ingested_date=None
    )
    session.add_all([stale, never])
    session.commit()

    calls: list[str] = []

    def fake_ingest(session, config, **kwargs):
        calls.append(config.bucket)
        return ingest_mod.IngestResult(0, 0, 0)

    monkeypatch.setattr(ingest_mod, "ingest_bucket", fake_ingest)

    ingest_mod.run_s3_ingest_tick(session, now=datetime(2026, 7, 10, 12, 0, tzinfo=UTC))

    assert calls == ["never-bucket", "stale-bucket"]


def test_prune_s3_request_cost_daily_deletes_only_stale_rows(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add_all(
        [
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date() - timedelta(days=40),
                operation_tier="get",
                request_count=1,
                cost_usd=0.0,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date() - timedelta(days=10),
                operation_tier="get",
                request_count=1,
                cost_usd=0.0,
                pricing_source="aws_price_list_api",
            ),
        ]
    )
    session.commit()

    deleted = ingest_mod.prune_s3_request_cost_daily(session, retention_days=35, now=now)
    session.commit()

    assert deleted == 1
    remaining = session.query(S3RequestCostDaily).all()
    assert len(remaining) == 1
    assert remaining[0].day == now.date() - timedelta(days=10)


# ---------------------------------------------------------------------------
# Cost page "S3 requests" tab -- fleet-wide per-source rollup
# ---------------------------------------------------------------------------


def test_compute_source_rollup_returns_none_with_no_tracked_buckets(session) -> None:
    source = _seed_source(session)

    result = ingest_mod.compute_source_request_cost_rollup(session, source.id)

    assert result is None


def test_compute_source_rollup_aggregates_across_buckets_and_tiers(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add(
        S3BucketLogConfig(
            source_id=source.id,
            bucket="lake-bucket",
            region="ap-south-1",
            logging_status="enabled",
            enabled=True,
            last_ingested_at=now,
        )
    )
    session.add_all(
        [
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date(),
                operation_tier="get",
                request_count=1000,
                cost_usd=0.4,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date(),
                operation_tier="put",
                request_count=200,
                cost_usd=1.0,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id,
                bucket="lake-bucket",
                table_id=table.id,
                day=now.date() - timedelta(days=1),
                operation_tier="list",
                request_count=50,
                cost_usd=0.25,
                pricing_source="aws_price_list_api",
            ),
        ]
    )
    session.commit()

    result = ingest_mod.compute_source_request_cost_rollup(session, source.id, now=now)

    assert result is not None
    assert result.bucket == "lake-bucket"
    assert result.region == "ap-south-1"
    assert result.logging_status == "enabled"
    assert result.total_request_count_30d == 1250
    assert result.total_cost_usd_month == 1.65
    assert result.by_tier.tier2_count == 1000
    assert result.by_tier.tier1_count == 250
    assert result.approximate is False
    assert len(result.by_day) == 2


def test_compute_source_rollup_excludes_rows_outside_lookback_window(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add(S3BucketLogConfig(source_id=source.id, bucket="lake-bucket", enabled=True))
    session.add(
        S3RequestCostDaily(
            source_id=source.id,
            bucket="lake-bucket",
            table_id=table.id,
            day=now.date() - timedelta(days=45),
            operation_tier="get",
            request_count=999,
            cost_usd=9.99,
            pricing_source="aws_price_list_api",
        )
    )
    session.commit()

    result = ingest_mod.compute_source_request_cost_rollup(
        session, source.id, lookback_days=30, now=now
    )

    assert result is not None
    assert result.total_request_count_30d == 0
    assert result.total_cost_usd_month == 0.0


def test_compute_source_rollup_flags_approximate_when_any_row_used_fallback_pricing(
    session,
) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a", location="s3://lake-bucket/warehouse/db/a")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add(S3BucketLogConfig(source_id=source.id, bucket="lake-bucket", enabled=True))
    session.add(
        S3RequestCostDaily(
            source_id=source.id,
            bucket="lake-bucket",
            table_id=table.id,
            day=now.date(),
            operation_tier="get",
            request_count=10,
            cost_usd=0.1,
            pricing_source="fallback_default",
        )
    )
    session.commit()

    result = ingest_mod.compute_source_request_cost_rollup(session, source.id, now=now)

    assert result is not None
    assert result.approximate is True


def test_compute_source_rollup_picks_most_recently_ingested_bucket_as_primary(session) -> None:
    source = _seed_source(session)
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add_all(
        [
            S3BucketLogConfig(
                source_id=source.id,
                bucket="stale-bucket",
                enabled=True,
                last_ingested_at=now - timedelta(days=5),
            ),
            S3BucketLogConfig(
                source_id=source.id,
                bucket="fresh-bucket",
                enabled=True,
                last_ingested_at=now,
            ),
        ]
    )
    session.commit()

    result = ingest_mod.compute_source_request_cost_rollup(session, source.id, now=now)

    assert result is not None
    assert result.bucket == "fresh-bucket"


def test_compute_source_rollup_by_bucket_breaks_down_cost_per_bucket(session) -> None:
    # Regression test: the "Per-bucket status" UI used to render one row per *source*,
    # summing all of a source's buckets into a single number labeled with just the
    # most-recently-ingested bucket's name -- the individual contribution of each bucket
    # (e.g. which of 8 tracked buckets is actually driving cost) was invisible. by_bucket
    # is the real per-bucket breakdown behind that single summed total.
    source = _seed_source(session)
    table_a = _seed_table(session, source, name="a", location="s3://bucket-a/warehouse/db/a")
    table_b = _seed_table(session, source, name="b", location="s3://bucket-b/warehouse/db/b")
    now = datetime(2026, 7, 7, tzinfo=UTC)
    session.add_all(
        [
            S3BucketLogConfig(
                source_id=source.id,
                bucket="bucket-a",
                region="ap-south-1",
                logging_status="enabled",
                enabled=True,
                last_ingested_at=now,
            ),
            S3BucketLogConfig(
                source_id=source.id,
                bucket="bucket-b",
                region="us-east-1",
                logging_status="enabled",
                enabled=True,
                last_ingested_at=now - timedelta(days=1),
            ),
            # Configured and enabled, but no S3RequestCostDaily rows yet -- must still
            # appear as a real zero row, not be silently dropped.
            S3BucketLogConfig(
                source_id=source.id,
                bucket="bucket-c-not-yet-ingested",
                enabled=True,
                logging_status="unknown",
            ),
        ]
    )
    session.add_all(
        [
            S3RequestCostDaily(
                source_id=source.id,
                bucket="bucket-a",
                table_id=table_a.id,
                day=now.date(),
                operation_tier="get",
                request_count=1000,
                cost_usd=4.0,
                pricing_source="aws_price_list_api",
            ),
            S3RequestCostDaily(
                source_id=source.id,
                bucket="bucket-b",
                table_id=table_b.id,
                day=now.date(),
                operation_tier="put",
                request_count=100,
                cost_usd=1.0,
                pricing_source="aws_price_list_api",
            ),
        ]
    )
    session.commit()

    result = ingest_mod.compute_source_request_cost_rollup(session, source.id, now=now)

    assert result is not None
    # Top-level totals are the true sum across every bucket, not just the primary one.
    assert result.total_request_count_30d == 1100
    assert result.total_cost_usd_month == 5.0

    assert len(result.by_bucket) == 3
    by_name = {b.bucket: b for b in result.by_bucket}

    assert by_name["bucket-a"].total_cost_usd_month == 4.0
    assert by_name["bucket-a"].total_request_count_30d == 1000
    assert by_name["bucket-a"].region == "ap-south-1"

    assert by_name["bucket-b"].total_cost_usd_month == 1.0
    assert by_name["bucket-b"].total_request_count_30d == 100

    # Never-ingested bucket still shows up, with real zeros rather than being omitted.
    assert by_name["bucket-c-not-yet-ingested"].total_cost_usd_month == 0.0
    assert by_name["bucket-c-not-yet-ingested"].total_request_count_30d == 0
    assert by_name["bucket-c-not-yet-ingested"].logging_status == "unknown"

    # Sorted by cost descending -- highest-cost bucket first, for "top buckets" chart use.
    assert [b.bucket for b in result.by_bucket] == [
        "bucket-a",
        "bucket-b",
        "bucket-c-not-yet-ingested",
    ]
