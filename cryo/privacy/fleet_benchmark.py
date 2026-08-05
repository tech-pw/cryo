from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import median

from cryo.intelligence.outcomes import MaintenanceOutcome, OutcomeDataset

FLEET_BENCHMARK_SCHEMA = "cryo.aggregate-fleet-benchmark/v1"
DEFAULT_MIN_DISTINCT_TABLES = 10
DEFAULT_MIN_OUTCOMES = 20


@dataclass(frozen=True, slots=True)
class AggregateCohort:
    engine: str
    action: str
    table_size_bucket: str
    distinct_tables: int
    outcomes: int
    median_runtime_ms: int | None
    median_rewrite_gib: float | None
    median_reclaim_gib: float
    median_reclaim_ratio: float | None
    median_p95_latency_change_ms: int | None


@dataclass(frozen=True, slots=True)
class AggregateFleetBenchmark:
    schema_version: str
    privacy_model: str
    min_distinct_tables: int
    min_outcomes: int
    eligible_outcomes: int
    published_cohorts: tuple[AggregateCohort, ...]
    suppressed_cohorts: int
    contains_direct_identifiers: bool
    differential_privacy: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_aggregate_fleet_benchmark(
    dataset: OutcomeDataset,
    *,
    min_distinct_tables: int = DEFAULT_MIN_DISTINCT_TABLES,
    min_outcomes: int = DEFAULT_MIN_OUTCOMES,
) -> AggregateFleetBenchmark:
    if min_distinct_tables < 2:
        raise ValueError("min_distinct_tables must be at least 2")
    if min_outcomes < min_distinct_tables:
        raise ValueError("min_outcomes must be at least min_distinct_tables")
    eligible = [row for row in dataset.rows if _eligible(row)]
    groups: dict[tuple[str, str, str], list[MaintenanceOutcome]] = {}
    for row in eligible:
        key = (
            row.engine or "unknown",
            row.action,
            _size_bucket(row.pre_total_size_gb or 0.0),
        )
        groups.setdefault(key, []).append(row)

    published: list[AggregateCohort] = []
    suppressed = 0
    for (engine, action, size_bucket), rows in sorted(groups.items()):
        table_count = len({row.table_id for row in rows})
        if table_count < min_distinct_tables or len(rows) < min_outcomes:
            suppressed += 1
            continue
        published.append(_aggregate(engine, action, size_bucket, rows, table_count))
    return AggregateFleetBenchmark(
        schema_version=FLEET_BENCHMARK_SCHEMA,
        privacy_model="k-anonymous cohorts with coarse buckets and rounded medians",
        min_distinct_tables=min_distinct_tables,
        min_outcomes=min_outcomes,
        eligible_outcomes=len(eligible),
        published_cohorts=tuple(published),
        suppressed_cohorts=suppressed,
        contains_direct_identifiers=False,
        differential_privacy=False,
    )


def _eligible(row: MaintenanceOutcome) -> bool:
    return (
        row.lineage_complete
        and row.engine is not None
        and row.pre_total_size_gb is not None
        and row.pre_total_size_gb >= 0
        and row.actual_bytes_freed >= 0
        and (row.duration_ms is None or row.duration_ms >= 0)
        and (row.actual_rewrite_bytes is None or row.actual_rewrite_bytes >= 0)
    )


def _aggregate(
    engine: str,
    action: str,
    size_bucket: str,
    rows: list[MaintenanceOutcome],
    table_count: int,
) -> AggregateCohort:
    runtimes = [row.duration_ms for row in rows if row.duration_ms is not None]
    rewritten = [row.actual_rewrite_bytes for row in rows if row.actual_rewrite_bytes is not None]
    ratios = [
        row.actual_bytes_freed / row.predicted_bytes_freed
        for row in rows
        if row.predicted_bytes_freed is not None and row.predicted_bytes_freed > 0
    ]
    latency_changes = [
        row.post_p95_query_latency_ms - row.pre_p95_query_latency_ms
        for row in rows
        if row.pre_p95_query_latency_ms is not None and row.post_p95_query_latency_ms is not None
    ]
    return AggregateCohort(
        engine=engine,
        action=action,
        table_size_bucket=size_bucket,
        distinct_tables=table_count,
        outcomes=len(rows),
        median_runtime_ms=_rounded_median(runtimes, 100),
        median_rewrite_gib=_rounded_gib_median(rewritten),
        median_reclaim_gib=_rounded_gib_median([row.actual_bytes_freed for row in rows]) or 0.0,
        median_reclaim_ratio=round(median(ratios), 2) if ratios else None,
        median_p95_latency_change_ms=_rounded_median(latency_changes, 10),
    )


def _rounded_median(values: Sequence[int | float], resolution: int) -> int | None:
    if not values:
        return None
    return int(round(median(values) / resolution) * resolution)


def _rounded_gib_median(values: list[int]) -> float | None:
    if not values:
        return None
    return round(median(values) / 1024**3, 1)


def _size_bucket(size_gb: float) -> str:
    if size_gb < 10:
        return "lt_10_gib"
    if size_gb < 100:
        return "10_100_gib"
    if size_gb < 1024:
        return "100_gib_1_tib"
    if size_gb < 10 * 1024:
        return "1_10_tib"
    return "gte_10_tib"
