"""Per-table, per-run dollar impact: files/GB freed *and* an estimated Trino query-cost delta
for the same table, from the same run -- not a fleet-wide average.

Two real, already-measured inputs, no invented pricing:

1. File-count and byte reduction for the run, straight off the AuditLog rows the run wrote
   (``input_files_count``/``output_files_count``/``actual_bytes_freed`` -- see
   ``cryo.engines.trino.engine`` and ``cryo.engines.spark.k8s_engine`` for how those get
   populated per engine).
2. This table's own measured Trino scan-cost rate: ``avg_bytes_per_query`` from ``QueryLog``
   (the same real signal ``cryo.planner.benefit_score`` already scores admission on) times the
   fleet's most recently *priced* $/GiB Trino rate (``QueryCostAttributionDaily.
   estimated_scan_cost_usd``, itself derived from real namespace spend -- see
   ``cryo.cost.model.derive_trino_scan_cost_prices``).

The estimated dollar delta is: (this table's daily query-scan cost before) minus (the same
formula applied with the post-run file count), holding query frequency and per-file overhead
constant and scaling scan cost by the file-count reduction ratio -- fewer files serving the same
data means less GET/open overhead per query, which is the mechanism `benefit_score`'s own
`_scan_cost_multiplier` already leans on. This is an ESTIMATE grounded in two real measurements,
not a live before/after Trino EXPLAIN -- it is not claimed as more than that anywhere in this
module or its payload.

No $/GiB price for the table's source and no query-log activity for the table both produce
`estimated_query_cost_delta_usd=None`, never a fabricated $0.00 -- same "no honest number, no
number" rule the rest of cryo.cost/cryo.reporting follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryCostAttributionDaily, QueryLog

_SUCCESS_STATUSES = ("OK", "succeeded")
# Actions whose input/output file counts are meaningful for a file-count-driven scan-cost
# estimate -- expire_snapshots and remove_orphan_files change *which* files exist but don't
# report a rewritten input/output pair the way OPTIMIZE/convert_deletes do.
_FILE_COUNT_ACTIONS = ("optimize", "convert_deletes", "rewrite_position_deletes")
_QUERY_LOOKBACK_DAYS = 30
_PRICE_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class RunDollarImpact:
    table_id: UUID
    run_start: datetime
    run_end: datetime
    input_files_count: int | None
    output_files_count: int | None
    bytes_freed: int
    # None when no action in this run reported file counts at all (e.g. an expire-only run).
    file_count_reduction_ratio: float | None
    queries_per_day_30d: float | None
    avg_bytes_per_query_30d: float | None
    trino_price_usd_per_gib: float | None
    trino_price_as_of: object | None  # date | None, kept loosely typed to avoid a date import
    estimated_daily_query_cost_before_usd: float | None
    estimated_daily_query_cost_after_usd: float | None
    estimated_query_cost_delta_usd: float | None

    @property
    def gb_freed(self) -> float:
        return self.bytes_freed / (1024**3)


def _latest_trino_price(session: Session, *, before: datetime) -> tuple[float, object] | None:
    """Most recent priced day's $/GiB, backed out of QueryCostAttributionDaily rather than
    re-deriving it -- a day is "priced" when estimated_scan_cost_usd is non-null for at least
    one row with physical_bytes_scanned > 0 (see apply_scan_cost_prices, which writes exactly
    that pair together or neither). Looks back up to _PRICE_LOOKBACK_DAYS days; beyond that a
    stale price is worse than admitting there isn't a current one."""
    since = (before - timedelta(days=_PRICE_LOOKBACK_DAYS)).date()
    row = session.execute(
        select(
            QueryCostAttributionDaily.day,
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
        )
        .where(
            QueryCostAttributionDaily.day >= since,
            QueryCostAttributionDaily.estimated_scan_cost_usd.isnot(None),
        )
        .group_by(QueryCostAttributionDaily.day)
        .order_by(QueryCostAttributionDaily.day.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    day, total_bytes, total_cost = row
    total_bytes = int(total_bytes or 0)
    total_cost = float(total_cost or 0.0)
    if total_bytes <= 0 or total_cost <= 0:
        return None
    price_per_gib = total_cost / (total_bytes / 1_073_741_824)
    return price_per_gib, day


def _query_rate_for_table(
    session: Session, table_id: UUID, *, before: datetime
) -> tuple[float, float] | None:
    """(queries_per_day, avg_bytes_per_query) over the trailing _QUERY_LOOKBACK_DAYS, same
    source (QueryLog) and same AVG-not-SUM sampling caveat cryo.planner.benefit_score's own
    avg_bytes_per_query query documents -- QueryLog is curated/sampled, not a full log."""
    since = before - timedelta(days=_QUERY_LOOKBACK_DAYS)
    row = session.execute(
        select(func.count(), func.avg(QueryLog.bytes_read)).where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= since,
            QueryLog.executed_at < before,
        )
    ).one()
    count, avg_bytes = row
    count = int(count or 0)
    if count == 0 or avg_bytes is None:
        return None
    queries_per_day = count / _QUERY_LOOKBACK_DAYS
    return queries_per_day, float(avg_bytes)


def compute_run_dollar_impact(
    session: Session,
    table_id: UUID,
    *,
    run_start: datetime,
    run_end: datetime,
) -> RunDollarImpact:
    """Files/GB freed and an estimated Trino query-cost delta for one table's maintenance
    window [run_start, run_end) -- not a fleet average, this specific table, this specific run.
    """
    audit_rows = session.execute(
        select(
            AuditLog.action,
            AuditLog.input_files_count,
            AuditLog.output_files_count,
            AuditLog.actual_bytes_freed,
        ).where(
            AuditLog.table_id == table_id,
            AuditLog.ts >= run_start,
            AuditLog.ts < run_end,
            AuditLog.status.in_(_SUCCESS_STATUSES),
        )
    ).all()

    bytes_freed = sum(int(freed) for *_rest, freed in audit_rows if freed is not None)

    input_total = 0
    output_total = 0
    have_file_counts = False
    for action, input_files, output_files, _freed in audit_rows:
        if action not in _FILE_COUNT_ACTIONS:
            continue
        if input_files is None or output_files is None:
            continue
        have_file_counts = True
        input_total += int(input_files)
        output_total += int(output_files)

    file_count_reduction_ratio: float | None = None
    if have_file_counts and input_total > 0:
        file_count_reduction_ratio = max(0.0, min(1.0, 1 - (output_total / input_total)))

    query_rate = _query_rate_for_table(session, table_id, before=run_start)
    price = _latest_trino_price(session, before=run_start)

    queries_per_day = query_rate[0] if query_rate else None
    avg_bytes_per_query = query_rate[1] if query_rate else None
    price_per_gib = price[0] if price else None
    price_as_of = price[1] if price else None

    cost_before: float | None = None
    cost_after: float | None = None
    cost_delta: float | None = None
    if (
        queries_per_day is not None
        and avg_bytes_per_query is not None
        and price_per_gib is not None
    ):
        daily_bytes_scanned = queries_per_day * avg_bytes_per_query
        cost_before = round((daily_bytes_scanned / 1_073_741_824) * price_per_gib, 4)
        if file_count_reduction_ratio is not None:
            cost_after = round(cost_before * (1 - file_count_reduction_ratio), 4)
            cost_delta = round(cost_before - cost_after, 4)

    return RunDollarImpact(
        table_id=table_id,
        run_start=run_start,
        run_end=run_end,
        input_files_count=input_total if have_file_counts else None,
        output_files_count=output_total if have_file_counts else None,
        bytes_freed=bytes_freed,
        file_count_reduction_ratio=file_count_reduction_ratio,
        queries_per_day_30d=queries_per_day,
        avg_bytes_per_query_30d=avg_bytes_per_query,
        trino_price_usd_per_gib=price_per_gib,
        trino_price_as_of=price_as_of,
        estimated_daily_query_cost_before_usd=cost_before,
        estimated_daily_query_cost_after_usd=cost_after,
        estimated_query_cost_delta_usd=cost_delta,
    )


def run_dollar_impact_payload(impact: RunDollarImpact) -> dict[str, object]:
    return {
        "table_id": str(impact.table_id),
        "run_start": impact.run_start.isoformat(),
        "run_end": impact.run_end.isoformat(),
        "input_files_count": impact.input_files_count,
        "output_files_count": impact.output_files_count,
        "bytes_freed": impact.bytes_freed,
        "gb_freed": round(impact.gb_freed, 3),
        "file_count_reduction_pct": (
            None
            if impact.file_count_reduction_ratio is None
            else round(impact.file_count_reduction_ratio * 100, 1)
        ),
        "estimated_daily_query_cost_before_usd": impact.estimated_daily_query_cost_before_usd,
        "estimated_daily_query_cost_after_usd": impact.estimated_daily_query_cost_after_usd,
        "estimated_query_cost_delta_usd_per_day": impact.estimated_query_cost_delta_usd,
        "trino_price_usd_per_gib": impact.trino_price_usd_per_gib,
        "trino_price_as_of": (
            impact.trino_price_as_of.isoformat()
            if hasattr(impact.trino_price_as_of, "isoformat")
            else impact.trino_price_as_of
        ),
        "estimate_basis": (
            "avg_bytes_per_query (QueryLog, 30d) x queries_per_day, scaled by this run's "
            "file-count reduction ratio, priced at the fleet's most recent real $/GiB Trino "
            "rate. Not a live before/after query benchmark."
        ),
    }


def now_utc() -> datetime:
    return datetime.now(UTC)
