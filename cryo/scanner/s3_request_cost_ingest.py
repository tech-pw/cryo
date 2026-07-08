"""S3 server access-log ingestion pipeline (``docs/s3-request-cost-tracking.md`` §5).

Registers the log-target bucket as an external Trino table, aggregates the trailing 2-3 day
window with a longest-prefix match against ``Table.location``, and UPSERTs the result into
``S3RequestCostDaily`` before rolling the trailing 30 days up onto ``TableCost`` (§6.3).

**Validation status**: the SQL/DDL here is written to the design doc's spec and unit-tested
against a mocked Trino cursor (same pattern as ``tests/test_trino_engine.py``), but has not been
benchmarked against a real Trino cluster with realistic S3 access-log volume -- §5 of the design
doc explicitly calls that out as required before this ships to real buckets. Treat the join
strategy below (a two-segment equi-join prefilter before the STARTS_WITH longest-match) as a
reasonable first cut, not a validated-at-scale implementation.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import ClientError
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.db.models import S3BucketLogConfig, S3RequestCostDaily, Table, TableCost
from cryo.engines.trino.client import TrinoCoordinator, connect
from cryo.integrations import s3_bucket_logging
from cryo.integrations.aws_s3_metrics import S3RequestPriceInfo, fetch_s3_request_prices
from cryo.scanner.orphans import parse_s3_location

logger = logging.getLogger(__name__)

# AWS's documented S3 server access log line format -- space-delimited, quoted where a field
# may itself contain spaces. Group 7 is `operation` (e.g. REST.GET.OBJECT), group 8 is `key`
# (bucket-relative object path) -- the only two fields this pipeline needs per log line.
S3_LOG_LINE_PATTERN = (
    r"([^ ]*) ([^ ]*) \[(.*?)\] ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) "
    r"(\"[^\"]*\"|-) (-|[0-9]*) ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) "
    r"(\"[^\"]*\"|-) ([^ ]*)(?: ([^ ]*))?(?: ([^ ]*))?(?: ([^ ]*))?(?: ([^ ]*))?"
    r"(?: ([^ ]*))?(?: ([^ ]*))?(?: ([^ ]*))?$"
)

# Maps a log `operation` string to this pipeline's finer-than-AWS operation_tier (see the
# S3RequestCostDaily docstring). Matched by prefix since AWS's operation names are consistent
# (REST.GET.OBJECT, REST.GET.OBJECT_TAGGING, etc. all start with REST.GET).
_OPERATION_TIER_CASE = """
CASE
  WHEN operation LIKE 'REST.GET%' OR operation LIKE 'REST.SELECT%' THEN 'get'
  WHEN operation LIKE 'REST.HEAD%' THEN 'head'
  WHEN operation LIKE 'REST.LIST%' OR operation LIKE 'BATCH.DELETE%'
    OR operation LIKE 'REST.DELETE%' THEN 'list'
  WHEN operation LIKE 'REST.PUT%' OR operation LIKE 'REST.POST%' OR operation LIKE 'REST.COPY%'
    THEN 'put'
  ELSE 'other'
END
""".strip()

# Which AWS Price List tier prices a given operation_tier value (see fetch_s3_request_prices):
# Tier1 = PUT/COPY/POST/LIST/DELETE, Tier2 = GET/SELECT/HEAD.
_PRICING_TIER: dict[str, str] = {
    "get": "tier2",
    "head": "tier2",
    "put": "tier1",
    "list": "tier1",
    "other": "tier1",
}

_DATE_SEGMENT_RE = re.compile(r"^\d{1,4}$")
_RECONCILE_WINDOW_DAYS = 3


@dataclass(frozen=True)
class IngestResult:
    rows_ingested: int
    tables_matched: int
    unmatched_requests: int
    through_date: date | None = None
    error: str | None = None


def _coordinator(settings: Settings, *, catalog: str, schema: str) -> TrinoCoordinator | None:
    url = settings.telemetry_trino_url
    if not url:
        return None
    parsed = urlparse(url)
    return TrinoCoordinator(
        host=parsed.hostname or "localhost",
        port=parsed.port or (443 if parsed.scheme == "https" else 8080),
        http_scheme=parsed.scheme or "http",
        catalog=catalog,
        schema=schema,
    )


def _fq_table(catalog: str, schema: str, table: str) -> str:
    return f'{catalog}."{schema}"."{table}"'


def _log_table_name(bucket: str) -> str:
    # Trino identifiers are more restrictive than S3 bucket names (which allow dots/hyphens).
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", bucket)
    return f"s3logs_{safe}"


def register_log_table_ddl(
    *, catalog: str, schema: str, bucket: str, log_target_bucket: str
) -> str:
    """§5 step 1 -- one Hive-external table per log-target bucket, one time.

    A single ``log_line`` column (whole raw line); fields are parsed at query time via
    ``regexp_extract`` rather than a Hive RegexSerDe, since Trino's own ``CREATE TABLE`` syntax
    for the Hive connector has no ``ROW FORMAT SERDE`` clause -- registering a plain-text table
    and parsing in SQL avoids needing a separate Hive Metastore DDL path alongside Trino's.
    Individual day partitions are registered separately via ``register_partition`` (see
    ``register_partition_sql``) since S3's partitioned log delivery uses plain value-only
    directories, not Hive's ``key=value`` convention that ``partitioned_by`` auto-discovery
    expects.
    """
    table = _log_table_name(bucket)
    return (
        f"CREATE TABLE IF NOT EXISTS {_fq_table(catalog, schema, table)} "
        "(log_line VARCHAR, log_date VARCHAR) "
        f"WITH (external_location = 's3a://{log_target_bucket}/', "
        "format = 'TEXTFILE', partitioned_by = ARRAY['log_date'])"
    )


def register_partition_sql(
    *, catalog: str, schema: str, bucket: str, log_date: date, location: str
) -> str:
    table = _log_table_name(bucket)
    return (
        f"CALL {catalog}.system.register_partition("
        f"schema_name => '{schema}', table_name => '{table}', "
        "partition_columns => ARRAY['log_date'], "
        f"partition_values => ARRAY['{log_date.isoformat()}'], "
        f"location => '{location}')"
    )


def _join_key_expr(column: str) -> str:
    """First two path segments of a key/prefix -- the equi-join prefilter (§5's "broadcast join
    instead of nested-loop" direction). Narrows candidates before the residual STARTS_WITH
    longest-match, without needing a UDF or precomputed index."""
    return f"array_join(slice(split({column}, '/'), 1, 2), '/')"


def build_aggregation_sql(
    *,
    catalog: str,
    schema: str,
    bucket: str,
    log_dates: list[date],
    table_prefixes: list[tuple[str, str]],
) -> str:
    """§5 step 4 -- one query per bucket per run over the trailing reconciliation window.

    ``table_prefixes`` is ``[(table_id, bucket_relative_prefix), ...]`` for every table this
    source has a known ``Table.location`` for. Matching is a two-stage longest-prefix match:
    an equi-join on the first two path segments narrows candidates (broadcast-hash-joinable),
    then ``STARTS_WITH`` + ``ROW_NUMBER() ... ORDER BY length(prefix) DESC`` picks the single
    longest real match per key -- see the module docstring's cross-table-prefix-collision note.
    Rows matching no table's prefix (``table_id IS NULL``) are kept, not dropped, so unmatched
    request volume is still visible rather than silently discarded.
    """
    table = _log_table_name(bucket)
    date_list = ", ".join(f"'{d.isoformat()}'" for d in log_dates)
    values = (
        ", ".join(f"('{tid}', '{prefix}')" for tid, prefix in table_prefixes)
        if table_prefixes
        else "(CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR))"
    )
    return f"""
WITH parsed AS (
  SELECT
    regexp_extract(log_line, '{S3_LOG_LINE_PATTERN}', 7) AS operation,
    regexp_extract(log_line, '{S3_LOG_LINE_PATTERN}', 8) AS key,
    log_date
  FROM {_fq_table(catalog, schema, table)}
  WHERE log_date IN ({date_list})
),
tables(table_id, prefix) AS (VALUES {values}),
candidates AS (
  SELECT p.key, p.operation, p.log_date, t.table_id, length(t.prefix) AS prefix_len
  FROM parsed p
  LEFT JOIN tables t
    ON {_join_key_expr("t.prefix")} = {_join_key_expr("p.key")}
   AND STARTS_WITH(p.key, t.prefix)
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY key, log_date ORDER BY prefix_len DESC NULLS LAST
  ) AS rn
  FROM candidates
)
SELECT
  table_id,
  log_date,
  {_OPERATION_TIER_CASE} AS operation_tier,
  COUNT(*) AS request_count
FROM ranked
WHERE rn = 1
GROUP BY table_id, log_date, {_OPERATION_TIER_CASE}
""".strip()


def locate_date_partition(
    client: Any, *, bucket: str, prefix: str, target_date: date, max_depth: int = 5
) -> str | None:
    """Walk the log-target prefix tree looking for a date-shaped subpath for ``target_date``.

    S3's partitioned log-delivery format inserts account/region/bucket segments before the
    ``YYYY/MM/DD`` directories in a way that isn't fully fixed across delivery configurations --
    rather than hardcode an assumed depth, this discovers the actual layout by listing common
    prefixes and descending into whichever child matches the next wanted date segment (or, if
    none match yet, the single non-date-shaped child, e.g. an account-id folder). Returns
    ``None`` if no such path is found within ``max_depth`` levels -- either an unpartitioned
    ("flat") log-delivery format, or no logs delivered for that date yet.
    """
    wanted = [f"{target_date.year:04d}", f"{target_date.month:02d}", f"{target_date.day:02d}"]
    current = prefix
    for _ in range(max_depth):
        try:
            response = client.list_objects_v2(Bucket=bucket, Prefix=current, Delimiter="/")
        except ClientError:
            return None
        children = [cp["Prefix"] for cp in response.get("CommonPrefixes", [])]
        if not children:
            return None
        matches = [c for c in children if c.rstrip("/").rsplit("/", 1)[-1] == wanted[0]]
        if matches:
            current = matches[0]
            wanted = wanted[1:]
            if not wanted:
                return str(current)
            continue
        non_date_children = [
            c for c in children if not _DATE_SEGMENT_RE.match(c.rstrip("/").rsplit("/", 1)[-1])
        ]
        if len(non_date_children) == 1:
            current = non_date_children[0]
            continue
        return None
    return None


def _resolve_request_prices(config: S3BucketLogConfig, settings: Settings) -> S3RequestPriceInfo:
    creds = s3_bucket_logging.credentials_for_config(config)
    if creds is None:
        logger.warning(
            "S3 request pricing: no AWS credentials configured for bucket %r; using the "
            "configured default rates. Cost derived from this will be approximate.",
            config.bucket,
        )
        return S3RequestPriceInfo(
            region=config.region or "us-east-1",
            storage_class="General Purpose",
            price_per_1k_put=settings.s3_put_usd_per_1k,
            price_per_1k_get=settings.s3_get_usd_per_1k,
            source="fallback_default",
        )
    return fetch_s3_request_prices(
        creds.region,
        creds,
        fallback_price_per_1k_put=settings.s3_put_usd_per_1k,
        fallback_price_per_1k_get=settings.s3_get_usd_per_1k,
    )


def ingest_bucket(
    session: Session,
    config: S3BucketLogConfig,
    *,
    settings: Settings | None = None,
    connector: Any = None,
    now: datetime | None = None,
) -> IngestResult:
    """Run one ingestion pass for a single enabled bucket (§5, manual-trigger or scheduled)."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    today = now.date()

    if not config.log_target_bucket:
        return IngestResult(0, 0, 0, error="No log target configured for this bucket")

    catalog = settings.s3_log_trino_catalog
    schema = settings.s3_log_trino_schema
    coordinator = _coordinator(settings, catalog=catalog, schema=schema)
    if coordinator is None:
        return IngestResult(
            0, 0, 0, error="No Trino coordinator configured (CRYO_TELEMETRY_TRINO_URL)"
        )

    connector = connector or connect
    conn = connector(coordinator)
    cursor = conn.cursor()

    start_date = config.last_ingested_date or (today - timedelta(days=_RECONCILE_WINDOW_DAYS))
    window_start = min(start_date, today - timedelta(days=_RECONCILE_WINDOW_DAYS - 1))
    candidate_dates = [
        window_start + timedelta(days=offset) for offset in range((today - window_start).days + 1)
    ]

    s3_client = s3_bucket_logging.s3_client_for_config(config)
    if s3_client is None:
        cursor.close()
        conn.close()
        return IngestResult(0, 0, 0, error="No AWS credentials configured for this bucket")

    located: dict[date, str] = {}
    for candidate_date in candidate_dates:
        location = locate_date_partition(
            s3_client,
            bucket=config.log_target_bucket,
            prefix=config.log_target_prefix or "",
            target_date=candidate_date,
        )
        if location:
            located[candidate_date] = f"s3a://{config.log_target_bucket}/{location}"

    if not located:
        cursor.close()
        conn.close()
        return IngestResult(
            0,
            0,
            0,
            error=(
                "No date-partitioned log directories found under the configured target -- this "
                "bucket's log delivery may not use partitioned prefixes yet, or no logs have "
                "landed for the reconciliation window"
            ),
        )

    try:
        cursor.execute(
            register_log_table_ddl(
                catalog=catalog,
                schema=schema,
                bucket=config.bucket,
                log_target_bucket=config.log_target_bucket,
            )
        )
        for log_date, location in located.items():
            try:
                cursor.execute(
                    register_partition_sql(
                        catalog=catalog,
                        schema=schema,
                        bucket=config.bucket,
                        log_date=log_date,
                        location=location,
                    )
                )
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

        table_locations = session.execute(
            select(Table.id, Table.location).where(
                Table.source_id == config.source_id, Table.location.is_not(None)
            )
        ).all()
        table_prefixes: list[tuple[str, str]] = []
        for table_id, location in table_locations:
            row_bucket, prefix = parse_s3_location(location)
            if row_bucket == config.bucket:
                table_prefixes.append((str(table_id), prefix))

        sql = build_aggregation_sql(
            catalog=catalog,
            schema=schema,
            bucket=config.bucket,
            log_dates=list(located.keys()),
            table_prefixes=table_prefixes,
        )
        cursor.execute(sql)
        rows = cursor.fetchall()
    except Exception as exc:
        logger.exception("S3 request-cost ingestion failed for bucket %s", config.bucket)
        return IngestResult(0, 0, 0, error=str(exc))
    finally:
        cursor.close()
        conn.close()

    price_info = _resolve_request_prices(config, settings)
    matched_table_ids: set[uuid.UUID] = set()
    unmatched = 0
    upserted = 0

    for row in rows:
        table_id_raw, log_date_raw, operation_tier, request_count = (
            row[0],
            row[1],
            row[2],
            int(row[3]),
        )
        log_date = (
            log_date_raw
            if isinstance(log_date_raw, date)
            else date.fromisoformat(str(log_date_raw))
        )
        tier = _PRICING_TIER.get(operation_tier, "tier1")
        price_per_1k = (
            price_info.price_per_1k_get if tier == "tier2" else price_info.price_per_1k_put
        )
        cost_usd = (request_count / 1000.0) * price_per_1k

        table_id = uuid.UUID(str(table_id_raw)) if table_id_raw is not None else None
        if table_id is None:
            unmatched += request_count
        else:
            matched_table_ids.add(table_id)

        # Postgres treats every NULL as distinct for UNIQUE purposes, so ON CONFLICT never
        # dedupes table_id IS NULL rows (see the S3RequestCostDaily docstring) -- a plain
        # select-then-update-or-insert sidesteps that entirely, for both NULL and non-NULL
        # table_id, and matches this codebase's existing upsert style (persist_table_cost)
        # rather than introducing a Postgres-only ON CONFLICT dialect dependency.
        existing = session.scalar(
            select(S3RequestCostDaily).where(
                S3RequestCostDaily.source_id == config.source_id,
                S3RequestCostDaily.bucket == config.bucket,
                S3RequestCostDaily.table_id == table_id
                if table_id is not None
                else S3RequestCostDaily.table_id.is_(None),
                S3RequestCostDaily.day == log_date,
                S3RequestCostDaily.operation_tier == operation_tier,
            )
        )
        if existing is None:
            session.add(
                S3RequestCostDaily(
                    source_id=config.source_id,
                    bucket=config.bucket,
                    table_id=table_id,
                    day=log_date,
                    operation_tier=operation_tier,
                    request_count=request_count,
                    cost_usd=cost_usd,
                    pricing_source=price_info.source,
                )
            )
        else:
            existing.request_count = request_count
            existing.cost_usd = cost_usd
            existing.pricing_source = price_info.source
        upserted += 1

    config.last_ingested_at = now
    config.last_ingested_date = today
    config.last_error = None
    config.last_error_at = None

    # recompute_table_cost_rollup reads back what was just written above via a fresh SELECT --
    # flush first so it sees this run's own rows rather than whatever was committed before it.
    session.flush()
    for table_id in matched_table_ids:
        recompute_table_cost_rollup(session, table_id, now=now)

    return IngestResult(
        rows_ingested=upserted,
        tables_matched=len(matched_table_ids),
        unmatched_requests=unmatched,
        through_date=today,
    )


def recompute_table_cost_rollup(
    session: Session, table_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """§6.3 -- recompute ``TableCost``'s 4 rollup fields from the trailing 30 days of
    ``S3RequestCostDaily``. A read-optimized summary, not the ingestion target -- this is what
    the Table Detail UI actually reads (§9.2)."""
    now = now or datetime.now(UTC)
    window_start = now.date() - timedelta(days=30)
    rows = session.execute(
        select(
            S3RequestCostDaily.operation_tier,
            S3RequestCostDaily.pricing_source,
            S3RequestCostDaily.request_count,
            S3RequestCostDaily.cost_usd,
        ).where(
            S3RequestCostDaily.table_id == table_id,
            S3RequestCostDaily.day >= window_start,
        )
    ).all()

    get_requests = put_requests = list_requests = 0
    cost_total = 0.0
    any_fallback = False
    for operation_tier, pricing_source, request_count, cost_usd in rows:
        if operation_tier in ("get", "head"):
            get_requests += request_count
        elif operation_tier == "put":
            put_requests += request_count
        elif operation_tier == "list":
            list_requests += request_count
        cost_total += cost_usd
        if pricing_source != "aws_price_list_api":
            any_fallback = True

    cost = session.get(TableCost, table_id)
    if cost is None:
        return
    cost.get_requests_30d = get_requests
    cost.put_requests_30d = put_requests
    cost.list_requests_30d = list_requests
    cost.request_cost_usd_month = round(cost_total, 4)
    cost.request_cost_pricing_source = "fallback_default" if any_fallback else "live"


def run_s3_ingest_tick(session: Session, *, settings: Settings | None = None) -> int:
    """§10 -- one scheduled pass: ingest every enabled bucket, one Trino query per bucket.

    ``ingest_bucket`` never raises (every failure path returns an ``IngestResult`` with
    ``.error`` set) so a single bucket's Trino/AWS trouble doesn't block the rest of the fleet's
    buckets from ingesting in the same tick. Returns the count of buckets successfully ingested.
    """
    configs = session.scalars(
        select(S3BucketLogConfig).where(S3BucketLogConfig.enabled.is_(True))
    ).all()
    succeeded = 0
    for config in configs:
        result = ingest_bucket(session, config, settings=settings)
        if result.error is not None:
            config.last_error = result.error
            config.last_error_at = datetime.now(UTC)
            logger.warning(
                "S3 request-cost ingestion failed for bucket %s: %s", config.bucket, result.error
            )
        else:
            succeeded += 1
        session.commit()
    return succeeded


def prune_s3_request_cost_daily(
    session: Session, *, retention_days: int = 35, now: datetime | None = None
) -> int:
    """§6.3 -- bound ``S3RequestCostDaily``'s footprint. Default 35 days: a few days past the
    30-day rollup window, so a delayed ingestion run never reads a just-pruned day out from
    under it. Returns the number of rows deleted. Caller is responsible for committing."""
    now = now or datetime.now(UTC)
    cutoff = now.date() - timedelta(days=retention_days)
    result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        delete(S3RequestCostDaily).where(S3RequestCostDaily.day < cutoff)
    )
    return int(result.rowcount or 0)


@dataclass(frozen=True)
class SourceRequestCostByTier:
    tier1_count: int
    tier2_count: int
    tier1_cost_usd: float
    tier2_cost_usd: float


@dataclass(frozen=True)
class SourceRequestCostByDay:
    day: date
    count: int
    cost_usd: float


@dataclass(frozen=True)
class SourceRequestCostRollup:
    """Cost page's "S3 requests" tab (fleet-wide view, one row per source): the
    request-count/cost fields are summed across every bucket this source tracks; the
    identity/status fields (bucket, region, logging_status, enabled, last_ingested_at)
    describe a single "primary" bucket -- the most recently ingested one, or the first
    alphabetically if none have ingested yet -- since a source usually maps to one bucket
    and the fleet view needs a single representative status per row, not a nested table.
    """

    bucket: str
    region: str | None
    enabled: bool
    logging_status: str
    last_ingested_at: datetime | None
    total_request_count_30d: int
    total_cost_usd_month: float
    by_tier: SourceRequestCostByTier
    by_day: list[SourceRequestCostByDay]
    approximate: bool


def compute_source_request_cost_rollup(
    session: Session,
    source_id: uuid.UUID,
    *,
    lookback_days: int = 30,
    now: datetime | None = None,
) -> SourceRequestCostRollup | None:
    """Fleet-wide rollup for the Cost page's "S3 requests" tab -- aggregates every bucket
    this source tracks (not just one, unlike the per-table rollup in
    ``recompute_table_cost_rollup``). Returns ``None`` if the source has no
    ``S3BucketLogConfig`` rows at all (never discovered/configured), which the API layer
    turns into a 404 -- distinct from "configured but zero requests measured yet", which
    returns real zeros here instead.
    """
    configs = session.scalars(
        select(S3BucketLogConfig).where(S3BucketLogConfig.source_id == source_id)
    ).all()
    if not configs:
        return None

    primary = sorted(
        configs,
        key=lambda c: (c.last_ingested_at or datetime.min.replace(tzinfo=UTC), c.bucket),
        reverse=True,
    )[0]

    now = now or datetime.now(UTC)
    window_start = now.date() - timedelta(days=lookback_days)
    rows = session.execute(
        select(
            S3RequestCostDaily.day,
            S3RequestCostDaily.operation_tier,
            S3RequestCostDaily.request_count,
            S3RequestCostDaily.cost_usd,
            S3RequestCostDaily.pricing_source,
        ).where(
            S3RequestCostDaily.source_id == source_id,
            S3RequestCostDaily.day >= window_start,
        )
    ).all()

    tier1_count = tier2_count = 0
    tier1_cost = tier2_cost = 0.0
    by_day: dict[date, dict[str, float]] = {}
    approximate = False
    for day, operation_tier, request_count, cost_usd, pricing_source in rows:
        tier = _PRICING_TIER.get(operation_tier, "tier1")
        if tier == "tier2":
            tier2_count += request_count
            tier2_cost += cost_usd
        else:
            tier1_count += request_count
            tier1_cost += cost_usd
        entry = by_day.setdefault(day, {"count": 0, "cost_usd": 0.0})
        entry["count"] += request_count
        entry["cost_usd"] += cost_usd
        if pricing_source != "aws_price_list_api":
            approximate = True

    return SourceRequestCostRollup(
        bucket=primary.bucket,
        region=primary.region,
        enabled=primary.enabled,
        logging_status=primary.logging_status,
        last_ingested_at=primary.last_ingested_at,
        total_request_count_30d=tier1_count + tier2_count,
        total_cost_usd_month=round(tier1_cost + tier2_cost, 4),
        by_tier=SourceRequestCostByTier(
            tier1_count=tier1_count,
            tier2_count=tier2_count,
            tier1_cost_usd=round(tier1_cost, 4),
            tier2_cost_usd=round(tier2_cost, 4),
        ),
        by_day=[
            SourceRequestCostByDay(day=day, count=int(v["count"]), cost_usd=round(v["cost_usd"], 4))
            for day, v in sorted(by_day.items())
        ],
        approximate=approximate,
    )
