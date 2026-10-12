"""Query-driven cost attribution: who/what is actually scanning bytes, and how much.

Trino's own query-completion event (read through the same way
``cryo.telemetry.iceberg_source`` reads it for selectivity -- see that module's docstring for
the read-through-vs-poll design rationale) carries, per query: the client/BI-tool that ran it
(``source``, e.g. "Metabase v0.60.0 [dashboard-uuid]"), the schema it targeted, and real
physical bytes scanned (``inputs_json``'s ``physicalInputBytes`` per input table). Mining a
20K-query real sample (2026-08-14) found Metabase alone responsible for 1.5TB scanned, and
individual queries scanning hundreds of GB for near-zero output (one query: 525GB scanned,
~0 bytes returned) -- real, directly attributable waste, not a guess.

This module rolls that up into ``cryo.query_cost_attribution_daily`` (day, client, schema) --
coarse enough to answer "who's driving scan cost" without keeping a second copy of every query
Trino already logs durably in its own event-listener table.

Deliberately a separate watermark/scan from ``iceberg_source.aggregate_from_iceberg_query_log``
(stored under ``Source.config["cost_attribution"]["last_aggregated_at"]``, not reusing
``query_log``'s watermark) even though both read the same underlying table: a failure in one
must not stall the other, and their retry/backoff needs may diverge as this system evolves.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from cryo.cost.model import TrinoScanCostPrice
from cryo.db.models import QueryCostAttributionDaily, Source
from cryo.telemetry.iceberg_source import parse_iceberg_query_log_config

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK_DAYS = 7
# A raw Trino `source` string carries a per-dashboard/session UUID for some clients (Metabase:
# "Metabase v0.60.0 [87b39d26-f81a-459a-b06b-836f6b3f68aa]") -- confirmed live, 2026-08-14: this
# fragments what should be one "Metabase" cost bucket into dozens of near-duplicate rows, one
# per dashboard instance. Normalizing to the part before " [" keeps the tool-level identity
# (which version, which product) without the noise.
_CLIENT_UUID_SUFFIX = " ["


@dataclass(frozen=True)
class CostAttributionResult:
    windows_processed: int
    queries_read: int
    rows_upserted: int


def _normalize_client(raw_source: str | None) -> str:
    if not raw_source:
        return "unknown"
    if _CLIENT_UUID_SUFFIX in raw_source:
        return raw_source.split(_CLIENT_UUID_SUFFIX, 1)[0].strip()
    return raw_source.strip()


def _normalize_user(raw_user: str | None) -> str:
    """Same 'unknown' fallback _normalize_client uses -- a report never has to special-case a
    missing value differently for client vs. user (see the user-attribution follow-up plan)."""
    if not raw_user:
        return "unknown"
    return raw_user.strip() or "unknown"


def _sum_physical_bytes(inputs_json: str | None) -> int:
    """Sum physicalInputBytes across every table this one query actually read.

    A join touches multiple input tables; the query's total scan cost is the sum across all of
    them, not just the first (which is what iceberg_source._selectivity_for_query intentionally
    looks at, for a different purpose -- selectivity for one specific table's own column
    patterns, not this module's "how many total bytes did this query cost").
    """
    if not inputs_json:
        return 0
    try:
        inputs = json.loads(inputs_json)
    except (TypeError, ValueError):
        return 0
    if not isinstance(inputs, list):
        return 0
    total = 0
    for entry in inputs:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("physicalInputBytes")
        if raw is None:
            continue
        try:
            total += int(raw)
        except (TypeError, ValueError):
            continue
    return total


def aggregate_query_cost_attribution(
    session: Session,
    source: Source,
    *,
    now: datetime | None = None,
    window_hours: int = 6,
    max_windows_per_tick: int = 4,
    max_rows_per_window: int = 25_000,
) -> CostAttributionResult:
    """Read new rows from the source's configured Iceberg query-log table and roll them up
    into QueryCostAttributionDaily.

    Same bounded-window-with-persisted-watermark shape as
    ``iceberg_source.aggregate_from_iceberg_query_log`` (see that function's docstring for why:
    a single unbounded scan of a busy query-event-listener table previously OOM'd the process
    reading it), reusing its config parser (both features are configured under the same
    ``Source.config["query_log"]`` block -- there's one Iceberg query-log table per source, not
    two) but tracking its own watermark under ``Source.config["cost_attribution"]``.
    """
    config = parse_iceberg_query_log_config(source.config or {})
    if config is None:
        return CostAttributionResult(0, 0, 0)

    from pyiceberg.expressions import And, GreaterThan, LessThanOrEqual

    from cryo.sources.factory import source_catalog_for

    now = now or datetime.now(UTC)
    cost_block = dict((source.config or {}).get("cost_attribution") or {})
    raw_watermark = cost_block.get("last_aggregated_at")
    since = None
    if isinstance(raw_watermark, str):
        try:
            since = datetime.fromisoformat(raw_watermark)
        except ValueError:
            since = None
    since = since or (now - timedelta(days=_DEFAULT_LOOKBACK_DAYS))

    window = timedelta(hours=window_hours)
    namespace, table_name = config.namespace_and_table

    try:
        catalog = source_catalog_for(source)
        iceberg_table = catalog.load_table(namespace, table_name)
    except Exception:
        logger.warning(
            "aggregate_query_cost_attribution: could not load %r for source %s",
            config.table_identifier,
            source.id,
            exc_info=True,
        )
        return CostAttributionResult(0, 0, 0)

    windows_processed = 0
    queries_read = 0
    rows_upserted = 0
    cursor = since

    fields: tuple[str, ...] = (
        config.query_text_column,
        config.executed_at_column,
        "source",
        "schema",
        "query_state",
        "inputs_json",
    )
    if config.user_column:
        fields = (*fields, config.user_column)

    while cursor < now and windows_processed < max_windows_per_tick:
        window_end = min(cursor + window, now)
        # (day, client, schema_name, user_name) -> [query_count, failed_count, bytes] -- one
        # rollup dict per window, upserted and committed before the watermark advances past
        # this window (below), not accumulated across the whole call and flushed once at the
        # end: a crash partway through a multi-window call would otherwise lose an earlier
        # window's already-read data even though its watermark had already moved past it.
        rollup: defaultdict[tuple[date, str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        try:
            scan = iceberg_table.scan(
                row_filter=And(
                    GreaterThan(config.executed_at_column, cursor),  # type: ignore[misc,call-arg,arg-type]
                    LessThanOrEqual(config.executed_at_column, window_end),  # type: ignore[misc,call-arg,arg-type]
                ),
                selected_fields=fields,
            )
            rows_in_window = 0
            for batch in scan.to_arrow_batch_reader():
                if rows_in_window >= max_rows_per_window:
                    # Still inside a real per-batch generator (to_arrow_batch_reader), so
                    # breaking here stops pulling further batches from the scan entirely --
                    # not just skipping their processing -- unlike breaking only the inner
                    # per-row loop below, which would let the outer loop keep materializing
                    # (and discarding) every remaining batch in the window regardless of the
                    # cap already being hit.
                    break
                columns = {
                    name: batch.column(name).to_pylist()
                    for name in fields
                    if name in batch.column_names
                }
                n = len(columns.get(config.executed_at_column, []))
                for i in range(n):
                    if rows_in_window >= max_rows_per_window:
                        break
                    executed_at = columns.get(config.executed_at_column, [None] * n)[i]
                    if executed_at is None:
                        continue
                    day = executed_at.date() if hasattr(executed_at, "date") else window_end.date()
                    client = _normalize_client(columns.get("source", [None] * n)[i])
                    schema_name = columns.get("schema", [None] * n)[i] or "unknown"
                    query_state = columns.get("query_state", [None] * n)[i]
                    inputs_json = columns.get("inputs_json", [None] * n)[i]
                    user_name = (
                        _normalize_user(columns.get(config.user_column, [None] * n)[i])
                        if config.user_column
                        else "unknown"
                    )

                    key = (day, client, schema_name, user_name)
                    bucket = rollup[key]
                    bucket[0] += 1
                    if query_state == "FAILED":
                        bucket[1] += 1
                    bucket[2] += _sum_physical_bytes(inputs_json)
                    rows_in_window += 1
            queries_read += rows_in_window
        except Exception:
            logger.warning(
                "aggregate_query_cost_attribution: failed reading window [%s, %s] for source %s",
                cursor,
                window_end,
                source.id,
                exc_info=True,
            )
            break

        # Both writes commit together (one transaction) -- the rollup upsert is additive
        # (+= on conflict, since the same (day, client, schema) key can legitimately be
        # touched by more than one window on the same calendar day), so a rollup commit that
        # succeeded without its watermark advance would double-count this window's data on
        # the next call. Combining them into a single commit removes that window entirely
        # rather than trying to make the additive upsert idempotent under a partial failure.
        rows_upserted += _upsert_rollup(session, source.id, rollup)

        cost_block["last_aggregated_at"] = window_end.isoformat()
        config_dict = dict(source.config or {})
        config_dict["cost_attribution"] = cost_block
        source.config = config_dict
        session.add(source)
        session.commit()

        cursor = window_end
        windows_processed += 1

    logger.info(
        "Query cost attribution: %s window(s), %s queries read, %s rows upserted, watermark now %s",
        windows_processed,
        queries_read,
        rows_upserted,
        cursor,
    )
    return CostAttributionResult(
        windows_processed=windows_processed,
        queries_read=queries_read,
        rows_upserted=rows_upserted,
    )


def _upsert_rollup(
    session: Session,
    source_id: Any,
    rollup: dict[tuple[date, str, str, str], list[int]],
) -> int:
    """Stage the additive upsert on ``session`` without committing -- the caller commits this
    together with the watermark advance in a single transaction (see the atomicity comment at
    the call site in ``aggregate_query_cost_attribution``)."""
    upserted = 0
    for (day, client, schema_name, user_name), (
        query_count,
        failed_count,
        bytes_scanned,
    ) in rollup.items():
        stmt = insert(QueryCostAttributionDaily).values(
            source_id=source_id,
            day=day,
            client=client,
            schema_name=schema_name,
            user_name=user_name,
            query_count=query_count,
            failed_query_count=failed_count,
            physical_bytes_scanned=bytes_scanned,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_query_cost_attribution_key",
            set_={
                "query_count": QueryCostAttributionDaily.query_count + stmt.excluded.query_count,
                "failed_query_count": (
                    QueryCostAttributionDaily.failed_query_count + stmt.excluded.failed_query_count
                ),
                "physical_bytes_scanned": (
                    QueryCostAttributionDaily.physical_bytes_scanned
                    + stmt.excluded.physical_bytes_scanned
                ),
                "updated_at": datetime.now(UTC),
            },
        )
        session.execute(stmt)
        upserted += 1
    return upserted


def prune_query_cost_attribution(
    session: Session, *, retention_days: int = 90, now: datetime | None = None
) -> int:
    """Bound query_cost_attribution_daily's footprint. 90 days (vs S3RequestCostDaily's 35) --
    this is a coarse (day, client, schema) rollup, not a per-table-per-operation-tier row, so
    the same retention window costs far less storage and a longer look-back is more useful for
    a cost-attribution *trend* report. Returns rows deleted; caller commits."""
    now = now or datetime.now(UTC)
    cutoff = now.date() - timedelta(days=retention_days)
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(QueryCostAttributionDaily).where(QueryCostAttributionDaily.day < cutoff)
        ),
    )
    return int(result.rowcount or 0)


def top_cost_drivers(
    session: Session,
    *,
    since: date,
    limit: int = 20,
) -> list[QueryCostAttributionDaily]:
    """Real per-(client, schema) rows since ``since``, for a report -- callers aggregate
    further (e.g. sum bytes per client across schemas) as needed; this returns the raw rollup
    rows rather than pre-aggregating, so a caller can slice by client OR schema OR both without
    two different query shapes."""
    return list(
        session.execute(
            select(QueryCostAttributionDaily)
            .where(QueryCostAttributionDaily.day >= since)
            .order_by(QueryCostAttributionDaily.physical_bytes_scanned.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def bytes_scanned_by_day(
    session: Session,
    *,
    since: date,
) -> dict[date, int]:
    """Total ``physical_bytes_scanned`` per day, summed across every (client, schema) row --
    the denominator ``cryo.cost.model.derive_trino_scan_cost_prices`` needs, since a day's real
    Trino namespace cost has to divide by that day's *total* bytes scanned, not any one
    client/schema slice of it."""
    rows = session.execute(
        select(
            QueryCostAttributionDaily.day,
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
        )
        .where(QueryCostAttributionDaily.day >= since)
        .group_by(QueryCostAttributionDaily.day)
    ).all()
    return {day: int(total or 0) for day, total in rows}


def apply_scan_cost_prices(
    session: Session,
    prices: Sequence[TrinoScanCostPrice],
) -> int:
    """Write ``estimated_scan_cost_usd`` onto every (day, client, schema) row for each priced
    day -- proportional to that row's own ``physical_bytes_scanned`` share of the day's total
    (the price itself is a $/GiB rate, not a flat per-row number; a client responsible for half
    a day's bytes should be attributed half that day's cost, not the whole day's cost repeated
    on every row). Rows for a day with a price get their existing value overwritten (this is a
    derived/recomputable field, not raw ingested data); days absent from ``prices`` (see
    derive_trino_scan_cost_prices' docstring for why some days have none) are left untouched,
    not zeroed -- an old, real price is more honest than erasing it because today's fetch
    didn't cover that day. Returns rows updated; caller commits."""
    updated = 0
    for price in prices:
        rows = list(
            session.scalars(
                select(QueryCostAttributionDaily).where(QueryCostAttributionDaily.day == price.day)
            )
        )
        if not rows:
            continue
        for row in rows:
            row_bytes = row.physical_bytes_scanned or 0
            row.estimated_scan_cost_usd = round(
                (row_bytes / 1_073_741_824) * price.price_usd_per_gib, 6
            )
            updated += 1
    return updated
