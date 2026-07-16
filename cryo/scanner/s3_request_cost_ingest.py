"""S3 server access-log ingestion pipeline (``docs/s3-request-cost-tracking.md`` §5).

Reads and aggregates the log-target bucket's access-log objects natively (boto3 + in-process
parsing) over the trailing 2-3 day reconciliation window, longest-prefix-matches each request
key against ``Table.location``, and UPSERTs the result into ``S3RequestCostDaily`` before rolling
the trailing 30 days up onto ``TableCost`` (§6.3).

**Not a Trino/Hive-connector dependency.** An earlier version of this pipeline registered the
log-target bucket as an external Trino Hive-connector table and ran the aggregation as SQL --
that turned out to be fragile in practice: it requires a Trino catalog that is *both*
Hive-connector-backed (supports ``external_location``) *and* writable by whatever credentials are
configured, which real deployments don't reliably have (Iceberg-only catalogs reject
``external_location``; write access and catalog topology vary independently). The design doc's
own rationale for choosing Trino ("Cryo already owns a Trino cluster capable of doing the
aggregation") treated Trino as a given rather than an evaluated choice, and never actually
compared it against in-process aggregation -- doc §3's options table has no such row. Reading
access-log objects directly (the same boto3-client pattern already used for orphan-file scanning,
``cryo/scanner/orphans.py``) and aggregating in Python removes that dependency entirely and works
identically regardless of which maintenance engine (native or Trino) a deployment uses elsewhere.
``cryo/integrations/s3_bucket_provisioning.py`` still uses the small Trino-teardown helpers below
(``_coordinator``/``_fq_table``/``_log_table_name``) to clean up an external table from an older
Trino-based run, if one exists -- that's the only remaining Trino touchpoint in this feature.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import ClientError
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from trino.auth import BasicAuthentication

from cryo.config import Settings, get_settings
from cryo.db.models import S3BucketLogConfig, S3RequestCostDaily, Table, TableCost
from cryo.engines.trino.client import TrinoCoordinator
from cryo.integrations import s3_bucket_logging
from cryo.integrations.aws_s3_metrics import S3RequestPriceInfo, fetch_s3_request_prices
from cryo.scanner.orphans import parse_s3_location

logger = logging.getLogger(__name__)

# AWS's documented S3 server access log line format -- space-delimited, quoted where a field
# may itself contain spaces. Group 7 is `operation` (e.g. REST.GET.OBJECT), group 8 is `key`
# (bucket-relative object path) -- the only two fields this pipeline needs per log line.
#
# Deliberately doesn't model anything past group 8: an earlier version tried to match every
# trailing field (request-URI, referrer, user-agent, ..., through the newest access-point-arn/
# acl-required fields) with a fixed count of optional groups, anchored at end-of-line. That
# broke on every real line in production -- the user-agent field is quoted but frequently
# contains spaces (e.g. Hadoop's S3A client: `"Hadoop 3.3.6-amzn-3, aws-sdk-java/2.23.18 ..."`),
# and AWS has added new trailing fields over time (region was added after this pattern was
# written), so a fixed trailing-group count silently breaks again whenever AWS extends the
# format. Matching only through the key field sidesteps both failure modes entirely.
S3_LOG_LINE_PATTERN = r"^([^ ]*) ([^ ]*) \[(.*?)\] ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) ([^ ]*) "
_S3_LOG_LINE_RE = re.compile(S3_LOG_LINE_PATTERN)

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
    auth = None
    if settings.telemetry_trino_password:
        auth = BasicAuthentication(
            settings.telemetry_trino_user or "cryo", settings.telemetry_trino_password
        )
    return TrinoCoordinator(
        host=parsed.hostname or "localhost",
        port=parsed.port or (443 if parsed.scheme == "https" else 8080),
        http_scheme=parsed.scheme or "http",
        user=settings.telemetry_trino_user or "cryo",
        catalog=catalog,
        schema=schema,
        auth=auth,
    )


def _fq_table(catalog: str, schema: str, table: str) -> str:
    return f'{catalog}."{schema}"."{table}"'


def _log_table_name(bucket: str) -> str:
    # Trino identifiers are more restrictive than S3 bucket names (which allow dots/hyphens).
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", bucket)
    return f"s3logs_{safe}"


def _parse_log_line(line: str) -> tuple[str, str] | None:
    """Extract ``(operation, key)`` from one raw S3 access-log line, or ``None`` if the line
    doesn't match the expected format (partial writes, truncated lines) or carries no usable
    operation/key (``-`` is AWS's placeholder for "not applicable")."""
    match = _S3_LOG_LINE_RE.match(line)
    if not match:
        return None
    operation, key = match.group(7), match.group(8)
    if not operation or not key or operation == "-" or key == "-":
        return None
    return operation, key


def _operation_tier(operation: str) -> str:
    """Python equivalent of the SQL ``CASE`` this pipeline used to run in Trino -- maps a log
    ``operation`` string to this pipeline's finer-than-AWS operation_tier (see the
    S3RequestCostDaily docstring). Matched by prefix since AWS's operation names are consistent
    (REST.GET.OBJECT, REST.GET.OBJECT_TAGGING, etc. all start with REST.GET)."""
    if operation.startswith("REST.GET") or operation.startswith("REST.SELECT"):
        return "get"
    if operation.startswith("REST.HEAD"):
        return "head"
    if (
        operation.startswith("REST.LIST")
        or operation.startswith("BATCH.DELETE")
        or operation.startswith("REST.DELETE")
    ):
        return "list"
    if (
        operation.startswith("REST.PUT")
        or operation.startswith("REST.POST")
        or operation.startswith("REST.COPY")
    ):
        return "put"
    return "other"


def _prefix_join_key(path: str) -> str:
    """First two path segments -- narrows candidates before the residual longest-prefix scan,
    the same idea the Trino-based version expressed as a SQL equi-join prefilter."""
    return "/".join(path.split("/")[:2])


def _build_prefix_index(table_prefixes: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group ``(table_id, prefix)`` by their first-two-path-segments join key, longest-prefix
    first within each group, so matching a log key is a dict lookup plus a short linear scan
    instead of checking every table's prefix against every key."""
    index: dict[str, list[tuple[str, str]]] = {}
    for table_id, prefix in table_prefixes:
        index.setdefault(_prefix_join_key(prefix), []).append((table_id, prefix))
    for group in index.values():
        group.sort(key=lambda item: len(item[1]), reverse=True)
    return index


def _match_table_prefix(key: str, index: dict[str, list[tuple[str, str]]]) -> str | None:
    """Longest real prefix match for one request key. Unmatched keys (no table's prefix covers
    this request) return ``None`` and are kept as unmatched volume, not dropped -- see
    ``IngestResult.unmatched_requests``."""
    for table_id, prefix in index.get(_prefix_join_key(key), ()):
        if key.startswith(prefix):
            return table_id
    return None


@dataclass(frozen=True)
class _AggregationOutcome:
    counts: dict[tuple[str | None, date, str], int]
    truncated_dates: list[date]


def _aggregate_log_objects(
    s3_client: Any,
    *,
    log_target_bucket: str,
    located: dict[date, str],
    table_prefixes: list[tuple[str, str]],
    max_objects_per_day: int,
    max_workers: int,
) -> _AggregationOutcome:
    """Read every log object under each located date-partition, parse each line, and aggregate
    into ``(table_id_or_None, log_date, operation_tier) -> request_count`` -- the same grouping
    the Trino-based aggregation used to produce. Bounded by ``max_objects_per_day`` per
    date-partition as a safety valve (mirrors ``cost_scan_max_list_keys``'s role in
    ``cryo/scanner/orphans.py``); objects are fetched concurrently (bounded by ``max_workers``)
    since a busy bucket delivers many small per-flush log files rather than a few large ones.
    Memory stays bounded regardless of log volume: only the aggregated counts are retained, not
    raw log lines -- each object's parsed lines are folded into the running counts and discarded.
    """
    index = _build_prefix_index(table_prefixes)
    counts: Counter[tuple[str | None, date, str]] = Counter()
    truncated_dates: list[date] = []
    location_prefix = f"s3a://{log_target_bucket}/"

    def _read_and_parse(object_key: str) -> list[tuple[str, str]]:
        body = s3_client.get_object(Bucket=log_target_bucket, Key=object_key)["Body"].read()
        results = []
        for line in body.decode("utf-8", errors="replace").splitlines():
            parsed = _parse_log_line(line)
            if parsed:
                results.append(parsed)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for log_date, location in located.items():
            prefix = (
                location[len(location_prefix) :]
                if location.startswith(location_prefix)
                else location
            )
            object_keys: list[str] = []
            truncated = False
            for page in s3_client.get_paginator("list_objects_v2").paginate(
                Bucket=log_target_bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    object_keys.append(obj["Key"])
                    if len(object_keys) >= max_objects_per_day:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                truncated_dates.append(log_date)

            for parsed_lines in executor.map(_read_and_parse, object_keys):
                for operation, request_key in parsed_lines:
                    table_id = _match_table_prefix(request_key, index)
                    counts[(table_id, log_date, _operation_tier(operation))] += 1

    return _AggregationOutcome(counts=dict(counts), truncated_dates=truncated_dates)


def locate_date_partition(
    client: Any,
    *,
    bucket: str,
    prefix: str,
    target_date: date,
    source_bucket: str | None = None,
    max_depth: int = 7,
) -> str | None:
    """Walk the log-target prefix tree looking for a date-shaped subpath for ``target_date``.

    S3's partitioned log-delivery format inserts account/region/bucket segments before the
    ``YYYY/MM/DD`` directories in a way that isn't fully fixed across delivery configurations --
    rather than hardcode an assumed depth, this discovers the actual layout by listing common
    prefixes and descending into whichever child matches the next wanted date segment. Before a
    date segment appears, a branch point can have *many* non-date-shaped children, not just one
    -- the common case is a shared, org-wide logging bucket receiving deliveries for many source
    buckets at once. AWS's documented "Partitioned" key format is
    ``[prefix][account-id]/[region]/[source-bucket]/[yyyy]/[mm]/[dd]/...``, so every other
    bucket logging to the same target shows up as a sibling folder at that depth. When
    ``source_bucket`` is given, an exact match on it is tried first; a lone non-date child is
    still used as a fallback for delivery configs that don't literally embed the bucket name at
    that depth (or when ``source_bucket`` isn't known). Returns ``None`` if no such path is
    found within ``max_depth`` levels -- either an unpartitioned ("flat") log-delivery format,
    or no logs delivered for that date yet. Default ``max_depth=7`` covers the deepest
    documented AWS layout (account-id, region, source-bucket, then year/month/day = 6 levels)
    with one level of slack.
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
        if source_bucket:
            bucket_matches = [
                c for c in non_date_children if c.rstrip("/").rsplit("/", 1)[-1] == source_bucket
            ]
            if bucket_matches:
                current = bucket_matches[0]
                continue
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
    now: datetime | None = None,
) -> IngestResult:
    """Run one ingestion pass for a single enabled bucket (§5, manual-trigger or scheduled).

    Reads and aggregates the bucket's access-log objects natively -- see the module docstring
    for why this doesn't go through Trino.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    today = now.date()

    if not config.log_target_bucket:
        return IngestResult(0, 0, 0, error="No log target configured for this bucket")

    start_date = config.last_ingested_date or (today - timedelta(days=_RECONCILE_WINDOW_DAYS))
    window_start = min(start_date, today - timedelta(days=_RECONCILE_WINDOW_DAYS - 1))
    candidate_dates = [
        window_start + timedelta(days=offset) for offset in range((today - window_start).days + 1)
    ]

    s3_client = s3_bucket_logging.s3_client_for_config(config)
    if s3_client is None:
        return IngestResult(0, 0, 0, error="No AWS credentials configured for this bucket")

    located: dict[date, str] = {}
    for candidate_date in candidate_dates:
        location = locate_date_partition(
            s3_client,
            bucket=config.log_target_bucket,
            prefix=config.log_target_prefix or "",
            target_date=candidate_date,
            source_bucket=config.bucket,
        )
        if location:
            located[candidate_date] = f"s3a://{config.log_target_bucket}/{location}"

    if not located:
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

    try:
        outcome = _aggregate_log_objects(
            s3_client,
            log_target_bucket=config.log_target_bucket,
            located=located,
            table_prefixes=table_prefixes,
            max_objects_per_day=settings.s3_log_ingest_max_objects_per_day,
            max_workers=settings.s3_log_ingest_concurrency,
        )
    except Exception as exc:
        logger.exception("S3 request-cost ingestion failed for bucket %s", config.bucket)
        return IngestResult(0, 0, 0, error=str(exc))

    if outcome.truncated_dates:
        logger.warning(
            "S3 request-cost ingestion for bucket %s hit the %s-object cap on %s date(s); "
            "counts for those days are a lower bound, not exact",
            config.bucket,
            settings.s3_log_ingest_max_objects_per_day,
            ", ".join(d.isoformat() for d in outcome.truncated_dates),
        )

    price_info = _resolve_request_prices(config, settings)
    matched_table_ids: set[uuid.UUID] = set()
    unmatched = 0
    upserted = 0

    for (table_id_raw, log_date, operation_tier), request_count in outcome.counts.items():
        tier = _PRICING_TIER.get(operation_tier, "tier1")
        price_per_1k = (
            price_info.price_per_1k_get if tier == "tier2" else price_info.price_per_1k_put
        )
        cost_usd = (request_count / 1000.0) * price_per_1k

        table_id = uuid.UUID(table_id_raw) if table_id_raw is not None else None
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


def run_s3_ingest_tick(
    session: Session, *, settings: Settings | None = None, now: datetime | None = None
) -> int:
    """§10 -- one scheduled pass: ingest every enabled bucket that hasn't already succeeded
    today, one Trino query per bucket.

    Two things work together to stop one bucket from starving the rest of the fleet forever:
    every tick skips a bucket whose ``last_ingested_date`` is already today (no wasted re-work
    within the reconciliation window's own tolerance -- see §7's latency discussion), and the
    remaining candidates are ordered oldest-first (``NULLS FIRST``) so a bucket that's never
    ingested, or gone longest without success, always gets first crack at a tick's time budget.
    Without this, a single slow bucket (one hit the 20k-object cap on 2 consecutive days) could
    consume an entire tick every time, and every *other* enabled bucket would never get a turn
    -- observed live: 6 newly-enabled buckets sat at last_ingested_at=None for 30+ minutes and
    multiple scheduler restarts while bucket #1 alone kept re-running from scratch each time.

    ``ingest_bucket`` never raises (every failure path returns an ``IngestResult`` with
    ``.error`` set) so a single bucket's Trino/AWS trouble doesn't block the rest of the fleet's
    buckets from ingesting in the same tick. Returns the count of buckets successfully ingested.
    """
    now = now or datetime.now(UTC)
    today = now.date()
    configs = session.scalars(
        select(S3BucketLogConfig)
        .where(S3BucketLogConfig.enabled.is_(True))
        .order_by(S3BucketLogConfig.last_ingested_date.asc().nulls_first())
    ).all()
    succeeded = 0
    for config in configs:
        if config.last_ingested_date == today:
            continue
        result = ingest_bucket(session, config, settings=settings, now=now)
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
class SourceRequestCostByBucket:
    """One source can track several buckets (bronze/silver/gold layers, audit-log
    variants, etc.) -- this is the per-bucket breakdown of the same totals that get
    summed into SourceRequestCostRollup's top-level fields, so the UI can show which
    bucket is actually driving cost instead of only a source-wide sum."""

    bucket: str
    region: str | None
    enabled: bool
    logging_status: str
    last_ingested_at: datetime | None
    total_request_count_30d: int
    total_cost_usd_month: float
    by_tier: SourceRequestCostByTier


@dataclass(frozen=True)
class SourceRequestCostRollup:
    """Cost page's "S3 requests" tab (fleet-wide view, one row per source): the
    request-count/cost fields are summed across every bucket this source tracks; the
    identity/status fields (bucket, region, logging_status, enabled, last_ingested_at)
    describe a single "primary" bucket -- the most recently ingested one, or the first
    alphabetically if none have ingested yet -- since a source usually maps to one bucket
    and the fleet view needs a single representative status per row, not a nested table.

    ``by_bucket`` is the real per-bucket breakdown (see SourceRequestCostByBucket) --
    the top-level fields above stay for backwards compatibility with anything already
    reading the source-wide total, but any UI that wants to know which bucket is
    actually expensive should read ``by_bucket`` instead.
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
    by_bucket: list[SourceRequestCostByBucket]
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

    def _aware(value: datetime | None) -> datetime:
        # Some configs never ingest (last_ingested_at stays NULL) while others carry a
        # real timestamp -- SQLite (unit tests) can additionally round-trip a stored
        # TIMESTAMP as naive even when it was written tz-aware, so comparing a mix of
        # None/naive/aware values directly raises TypeError. Normalize everything to
        # aware UTC before sorting.
        if value is None:
            return datetime.min.replace(tzinfo=UTC)
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    primary = sorted(
        configs,
        key=lambda c: (_aware(c.last_ingested_at), c.bucket),
        reverse=True,
    )[0]

    now = now or datetime.now(UTC)
    window_start = now.date() - timedelta(days=lookback_days)
    rows = session.execute(
        select(
            S3RequestCostDaily.bucket,
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

    @dataclass
    class _BucketTotals:
        tier1_count: int = 0
        tier2_count: int = 0
        tier1_cost: float = 0.0
        tier2_cost: float = 0.0

    bucket_totals: dict[str, _BucketTotals] = {}

    for bucket, day, operation_tier, request_count, cost_usd, pricing_source in rows:
        tier = _PRICING_TIER.get(operation_tier, "tier1")
        totals = bucket_totals.setdefault(bucket, _BucketTotals())
        if tier == "tier2":
            tier2_count += request_count
            tier2_cost += cost_usd
            totals.tier2_count += request_count
            totals.tier2_cost += cost_usd
        else:
            tier1_count += request_count
            tier1_cost += cost_usd
            totals.tier1_count += request_count
            totals.tier1_cost += cost_usd
        entry = by_day.setdefault(day, {"count": 0, "cost_usd": 0.0})
        entry["count"] += request_count
        entry["cost_usd"] += cost_usd
        if pricing_source != "aws_price_list_api":
            approximate = True

    # Every configured bucket gets a row, not just ones with cost data in the window --
    # matching the existing per-source behavior where "configured but zero requests
    # measured yet" is a real, distinct, visible state rather than an omitted one.
    by_bucket = [
        SourceRequestCostByBucket(
            bucket=config.bucket,
            region=config.region,
            enabled=bool(config.enabled),
            logging_status=config.logging_status,
            last_ingested_at=config.last_ingested_at,
            total_request_count_30d=totals.tier1_count + totals.tier2_count,
            total_cost_usd_month=round(totals.tier1_cost + totals.tier2_cost, 4),
            by_tier=SourceRequestCostByTier(
                tier1_count=totals.tier1_count,
                tier2_count=totals.tier2_count,
                tier1_cost_usd=round(totals.tier1_cost, 4),
                tier2_cost_usd=round(totals.tier2_cost, 4),
            ),
        )
        for config in configs
        for totals in [bucket_totals.get(config.bucket, _BucketTotals())]
    ]
    by_bucket.sort(key=lambda b: b.total_cost_usd_month, reverse=True)

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
        by_bucket=by_bucket,
        approximate=approximate,
    )
