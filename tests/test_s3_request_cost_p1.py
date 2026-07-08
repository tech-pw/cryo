"""Tests for the P0-remainder discovery layer + P1 ingestion pipeline
(docs/s3-request-cost-tracking.md §4, §5, §6.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

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

    def client(self, name):
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
# §5 SQL/DDL generation (golden-string tests, no live Trino)
# ---------------------------------------------------------------------------


def test_register_log_table_ddl_is_idempotent_and_partitioned() -> None:
    sql = ingest_mod.register_log_table_ddl(
        catalog="hive", schema="cryo_s3_logs", bucket="lake-bucket", log_target_bucket="log-bucket"
    )
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "s3logs_lake_bucket" in sql
    assert "partitioned_by = ARRAY['log_date']" in sql
    assert "s3a://log-bucket/" in sql


def test_register_partition_sql_calls_system_register_partition() -> None:
    sql = ingest_mod.register_partition_sql(
        catalog="hive",
        schema="cryo_s3_logs",
        bucket="lake-bucket",
        log_date=date(2026, 7, 7),
        location="s3a://log-bucket/logs/2026/07/07/",
    )
    assert "hive.system.register_partition" in sql
    assert "'2026-07-07'" in sql
    assert "s3a://log-bucket/logs/2026/07/07/" in sql


def test_build_aggregation_sql_includes_longest_prefix_match() -> None:
    sql = ingest_mod.build_aggregation_sql(
        catalog="hive",
        schema="cryo_s3_logs",
        bucket="lake-bucket",
        log_dates=[date(2026, 7, 7)],
        table_prefixes=[("t1", "warehouse/db1/table_a/"), ("t2", "warehouse/db1/table_b/")],
    )
    assert "STARTS_WITH(p.key, t.prefix)" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "ORDER BY prefix_len DESC" in sql
    assert "warehouse/db1/table_a/" in sql
    assert "warehouse/db1/table_b/" in sql


def test_build_aggregation_sql_handles_no_known_tables() -> None:
    sql = ingest_mod.build_aggregation_sql(
        catalog="hive",
        schema="cryo_s3_logs",
        bucket="lake-bucket",
        log_dates=[date(2026, 7, 7)],
        table_prefixes=[],
    )
    assert "CAST(NULL AS VARCHAR)" in sql


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


# ---------------------------------------------------------------------------
# §5 end-to-end ingest_bucket + §6.3 rollup
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _settings_with_trino() -> Settings:
    return Settings(telemetry_trino_url="http://trino.local:8080")


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
    fake_list_client = _FakeListClient(
        {
            "logs/": [f"logs/{today.year}/"],
            f"logs/{today.year}/": [f"logs/{today.year}/{today.month:02d}/"],
            f"logs/{today.year}/{today.month:02d}/": [
                f"logs/{today.year}/{today.month:02d}/{today.day:02d}/"
            ],
        }
    )
    monkeypatch.setattr(s3_bucket_logging, "s3_client_for_config", lambda cfg: fake_list_client)
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

    cursor = _FakeCursor(
        rows=[
            (str(table.id), today.isoformat(), "get", 30_000),
            (None, today.isoformat(), "list", 5),
        ]
    )

    def fake_connector(coordinator):
        return _FakeConnection(cursor)

    result = ingest_mod.ingest_bucket(
        session,
        config,
        settings=_settings_with_trino(),
        connector=fake_connector,
        now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )

    assert result.error is None
    assert result.rows_ingested == 2
    assert result.tables_matched == 1
    assert result.unmatched_requests == 5

    session.flush()
    daily_rows = session.query(S3RequestCostDaily).all()
    assert len(daily_rows) == 2
    matched = next(r for r in daily_rows if r.table_id == table.id)
    assert matched.request_count == 30_000
    assert matched.operation_tier == "get"

    session.refresh(table)
    cost = session.get(TableCost, table.id)
    assert cost.get_requests_30d == 30_000
    assert cost.put_requests_30d == 0
    assert cost.request_cost_usd_month is not None
    assert cost.request_cost_usd_month > 0

    assert config.last_ingested_date == date(2026, 7, 7)
    assert config.last_error is None


def test_ingest_bucket_without_trino_configured_returns_error(session) -> None:
    source = _seed_source(session)
    config = _config_with_creds(session, source)
    config.log_target_bucket = "log-bucket"
    config.enabled = True
    session.commit()

    result = ingest_mod.ingest_bucket(session, config, settings=Settings(telemetry_trino_url=None))

    assert result.error is not None
    assert "Trino" in result.error


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

    result = ingest_mod.ingest_bucket(session, config, settings=_settings_with_trino())

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
