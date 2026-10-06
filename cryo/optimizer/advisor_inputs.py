"""Collect the clustering advisor's inputs from stored telemetry and scan data.

``clustering_advisor.advise`` takes workload queries, file bounds, a joint row sample, column
signals, and co-occurrence weights. Phase 2 built the reasoning; this module supplies the data,
so the advisor can run against a real table instead of hand-constructed fixtures.

Four things are assembled here:

**Replay queries** come from ``query_logs.query_text``, re-parsed rather than read from the
stored ``filter_columns`` JSON. The stored column list predates the operator-preserving parser
fix and cannot distinguish ``ts > x`` from ``ts = x``; re-parsing recovers the ranges that make
a predicate prunable. Queries whose predicates cannot be turned into bounds are still counted,
because parser coverage is a gate the advisor checks.

**Column signals** come from ``query_patterns`` (frequency, selectivity, operator mix) joined
with per-column NDV and null fraction. NDV uses the existing ``ThetaSketch`` where a Puffin
sidecar has been written, and falls back to "unknown" rather than guessing -- an unknown NDV
means the screening rules that depend on it simply do not fire.

**Co-occurrence** is counted per query: every pair of columns filtered by the same query gets
that query's weight. This is what makes multi-column candidates workload-driven, and it is the
signal a top-k-by-frequency heuristic throws away.

**Quantile boundaries** come from the existing ``KLLSketch``. Note the sketch stores numeric
ranks, so it serves numeric columns; string and temporal columns fall back to boundaries
derived from the row sample itself.

What this module does *not* do is sample rows from Parquet. That needs a read path Cryo does
not have on the scan side today -- ``iter_data_files`` reads manifests, not row data. The
sampler here is deliberately a seam: it takes rows a caller supplies (a Trino/Spark
``TABLESAMPLE``, a dev fixture) and validates them. See ``build_row_sample``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, QueryPattern
from cryo.optimizer.baseline_replay import Predicate, ReplayQuery
from cryo.optimizer.candidate_generation import ColumnSignal
from cryo.telemetry.parser import extract_query_ranges, parse_query_predicates

logger = logging.getLogger(__name__)

# Rows below this make a joint sample too noisy to derive synthetic file bounds from: with
# fewer rows than target files, most simulated files hold a single row and every bound is a
# point, which overstates pruning badly.
MIN_ROW_SAMPLE = 500


@dataclass
class WorkloadSignals:
    """Everything the advisor needs about a table's workload."""

    queries: list[ReplayQuery] = field(default_factory=list)
    column_signals: list[ColumnSignal] = field(default_factory=list)
    cooccurrence: dict[tuple[str, str], float] = field(default_factory=dict)
    # Share of filter-column observations that became usable predicates.
    parser_coverage: float = 0.0
    queries_seen: int = 0

    @property
    def observations(self) -> int:
        return sum(q.frequency for q in self.queries)


def build_replay_queries(
    session: Session,
    table_id: uuid.UUID,
    *,
    limit: int = 5_000,
) -> tuple[list[ReplayQuery], float]:
    """Re-parse stored query text into replayable predicates.

    Returns the queries and the parser coverage. Identical predicate sets are collapsed into
    one ``ReplayQuery`` with a frequency, which is both cheaper to replay and the correct
    weighting -- a query run a thousand times should dominate one run once.
    """
    rows = session.scalars(
        select(QueryLog)
        .where(QueryLog.table_id == table_id, QueryLog.query_text.is_not(None))
        .order_by(QueryLog.executed_at.desc())
        .limit(limit)
    ).all()

    grouped: dict[tuple, dict[str, Any]] = {}
    parseable = 0
    observed = 0

    for row in rows:
        text = row.query_text or ""
        try:
            info = parse_query_predicates(text)
            ranges = extract_query_ranges(text)
        except Exception:  # noqa: BLE001 -- one malformed query must not stop collection
            continue

        bounded = {r.column: (r.lower, r.upper) for r in ranges}
        predicates: list[Predicate] = []
        unparseable: list[str] = []

        for column in info.filter_columns:
            observed += 1
            if column.column in bounded:
                low, high = bounded[column.column]
                predicates.append(Predicate(column.column, lower=low, upper=high))
                parseable += 1
            elif column.is_equality and column.operator == "=":
                # Equality without an extracted literal still tells us the column is filtered,
                # but without a value it cannot prune; count it as coverage loss.
                unparseable.append(column.column)
            else:
                unparseable.append(column.column)

        if not predicates and not unparseable:
            continue

        key = (
            tuple(sorted((p.column, p.lower, p.upper) for p in predicates)),
            tuple(sorted(unparseable)),
        )
        entry = grouped.setdefault(
            key,
            {"predicates": tuple(predicates), "unparseable": tuple(sorted(unparseable)), "n": 0},
        )
        entry["n"] += 1

    queries = [
        ReplayQuery(
            predicates=entry["predicates"],
            frequency=entry["n"],
            unparseable_columns=entry["unparseable"],
        )
        for entry in grouped.values()
    ]
    coverage = (parseable / observed) if observed else 0.0
    return queries, coverage


def build_cooccurrence(queries: Sequence[ReplayQuery]) -> dict[tuple[str, str], float]:
    """Weight each column pair by the workload that filters both together.

    Pairs are stored with columns sorted so lookups are order-independent.
    """
    weights: dict[tuple[str, str], float] = {}
    for query in queries:
        columns = sorted({p.column for p in query.predicates})
        for a, b in combinations(columns, 2):
            weights[(a, b)] = weights.get((a, b), 0.0) + query.frequency
    return weights


def build_column_signals(
    session: Session,
    table_id: uuid.UUID,
    *,
    ndv_ratios: Mapping[str, float] | None = None,
    null_fractions: Mapping[str, float] | None = None,
    columns_with_bounds: Sequence[str] | None = None,
) -> list[ColumnSignal]:
    """Assemble per-column signals from query patterns and column statistics.

    ``ndv_ratios`` and ``null_fractions`` are optional; where a column is missing from them the
    signal carries ``None``/0.0 and the screening rules that need those values do not fire.
    That is deliberate -- screening a column out on a guessed statistic is worse than not
    screening it at all, since the simulator can still reject it on merit.
    """
    patterns = session.scalars(select(QueryPattern).where(QueryPattern.table_id == table_id)).all()
    if not patterns:
        return []

    total_queries = max(1, max((p.filter_count for p in patterns), default=1))
    bounds_set = set(columns_with_bounds) if columns_with_bounds is not None else None

    signals: list[ColumnSignal] = []
    for pattern in patterns:
        if pattern.filter_count <= 0:
            continue  # projected or joined but never filtered: cannot drive pruning
        filters = pattern.filter_count
        equality = pattern.equality_filter_count or 0
        ranges = pattern.range_filter_count or 0
        classified = equality + ranges
        signals.append(
            ColumnSignal(
                name=pattern.column_name,
                filter_frequency=min(1.0, filters / total_queries),
                avg_selectivity=pattern.avg_selectivity,
                ndv_ratio=(ndv_ratios or {}).get(pattern.column_name),
                null_fraction=(null_fractions or {}).get(pattern.column_name, 0.0),
                has_bounds=True if bounds_set is None else pattern.column_name in bounds_set,
                # Only equality and range predicates produce min/max bounds; anything else
                # (LIKE, IS NULL, a UDF) was recorded by the parser but cannot prune.
                sargable_fraction=(classified / filters) if filters else 0.0,
                equality_fraction=(equality / classified) if classified else 0.0,
            )
        )
    return signals


def collect_workload_signals(
    session: Session,
    table_id: uuid.UUID,
    *,
    ndv_ratios: Mapping[str, float] | None = None,
    null_fractions: Mapping[str, float] | None = None,
    columns_with_bounds: Sequence[str] | None = None,
    limit: int = 5_000,
) -> WorkloadSignals:
    """Assemble every workload-derived advisor input for one table."""
    queries, coverage = build_replay_queries(session, table_id, limit=limit)
    return WorkloadSignals(
        queries=queries,
        column_signals=build_column_signals(
            session,
            table_id,
            ndv_ratios=ndv_ratios,
            null_fractions=null_fractions,
            columns_with_bounds=columns_with_bounds,
        ),
        cooccurrence=build_cooccurrence(queries),
        parser_coverage=coverage,
        queries_seen=len(queries),
    )


def build_row_sample(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    *,
    min_rows: int = MIN_ROW_SAMPLE,
) -> list[dict[str, Any]]:
    """Validate and project a caller-supplied joint row sample.

    **Joint** is the operative word: the sample must preserve each row's values across all
    candidate columns together. Per-column sketches cannot substitute, because they cannot
    represent correlation, and correlated dimensions are exactly where naive column selection
    goes wrong (research note §4.3).

    Cryo has no Parquet row-read path on the scan side -- ``iter_data_files`` reads manifest
    metadata, not data -- so rows come from outside: a Trino ``TABLESAMPLE``, a Spark job, or a
    fixture. This function is the seam where that arrives, projected to the candidate columns
    and rejected if too small to derive meaningful synthetic file bounds from.
    """
    if len(rows) < min_rows:
        logger.info(
            "row sample of %d rows is below the %d-row floor; simulation would overstate pruning",
            len(rows),
            min_rows,
        )
        return []
    wanted = list(columns)
    return [{column: row.get(column) for column in wanted} for row in rows]


def quantile_boundaries_from_sketch(sketch: Any, buckets: int = 256) -> list[float]:
    """Read ascending quantile cut points out of a KLL sketch.

    The sketch interpolates numeric ranks, so this serves numeric columns. String and temporal
    columns should derive boundaries from the row sample instead (``curve_encoding``'s own
    ``quantile_boundaries``), which handles any orderable type.
    """
    # KLLSketch.is_empty is a property, not a method; default True so an object without it is
    # treated as unusable rather than silently producing garbage boundaries.
    if sketch is None or getattr(sketch, "is_empty", True):
        return []
    cuts: list[float] = []
    for i in range(1, buckets):
        try:
            value = float(sketch.quantile(i / buckets))
        except Exception:  # noqa: BLE001 -- a bad sketch must not break candidate generation
            return []
        if not cuts or value > cuts[-1]:  # strictly ascending; collapse heavy-hitter ties
            cuts.append(value)
    return cuts
