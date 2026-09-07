"""Exact current-layout baseline: replay query predicates against Iceberg file bounds.

Phase 1 of the automatic-clustering advisor (see
``docs/automatic-clustering-column-selection.md`` §5.1). Given the per-file lower/upper bounds
Cryo already samples from manifests, this module answers "how many files, and how many bytes,
does the *current* layout force each query to touch?" without reading a single data file.

This is deliberately a *baseline*, not a prediction. It is exact with respect to the bounds and
predicates it can see, and it reports what it could not see rather than guessing:

- predicates Cryo cannot parse or classify (``operator is None``);
- columns absent from the sampled bounds;
- Parquet row-group/page pruning, Bloom filters, and engine dynamic filtering, none of which
  are modelled here -- so real engines may touch *fewer* files than this reports.

Because those effects only ever prune further, ``files_touched`` is an upper bound on the
current layout's cost. That direction matters: it keeps the advisor conservative, since a
benefit estimated against an inflated baseline is never optimistic.

Nothing here selects clustering columns or rewrites data. Per the research note's §10, the
advisor's first job is to produce evidence and to abstain out loud when the evidence is thin.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Bounds are compared with Python's own ordering. Cross-type comparisons (str vs int) raise
# TypeError, which we treat as "cannot decide" -- see _may_contain.
Bound = Any


class AbstainReason(StrEnum):
    """Machine-readable reason a table produced no usable baseline.

    Abstention is a successful outcome, not an error (research note §10). Each value names a
    specific missing input so operators can see *what* to fix rather than an opaque "no
    recommendation".
    """

    NO_FILE_STATS = "no_file_stats"
    NO_PARSEABLE_PREDICATES = "no_parseable_predicates"
    LOW_PARSER_COVERAGE = "low_parser_coverage"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    NO_BOUNDS_FOR_FILTER_COLUMNS = "no_bounds_for_filter_columns"


@dataclass(frozen=True)
class Predicate:
    """One parsed, SARGable filter on a single column.

    ``lower``/``upper`` are inclusive. An equality predicate sets both to the same value; an
    open-ended range leaves one side ``None``. Predicates that cannot be expressed this way
    (LIKE, IS NULL, UDFs) are not Predicates at all -- they are counted as unparseable
    coverage loss by the caller.
    """

    column: str
    lower: Bound | None = None
    upper: Bound | None = None

    def may_match(self, file_low: Bound, file_high: Bound) -> bool:
        """Whether a file spanning ``[file_low, file_high]`` can contain a matching row."""
        return _may_contain(self.lower, self.upper, file_low, file_high)


@dataclass(frozen=True)
class ReplayQuery:
    """A query signature: its conjunctive predicates and how often it was observed."""

    predicates: tuple[Predicate, ...]
    frequency: int = 1
    # Columns filtered by this query that could not be turned into a Predicate. Used for
    # coverage reporting only; they never prune.
    unparseable_columns: tuple[str, ...] = ()


@dataclass
class QueryBaseline:
    """Per-query replay result against the current layout."""

    files_touched: int
    bytes_touched: int
    total_files: int
    total_bytes: int
    frequency: int = 1
    # Predicate columns with no bounds in the sample; the query is still replayed on the
    # columns that do have bounds, but this is why it may prune less than it could.
    columns_without_bounds: tuple[str, ...] = ()

    @property
    def file_prune_ratio(self) -> float:
        """Fraction of files the current layout already skips (0.0 = reads everything)."""
        if self.total_files <= 0:
            return 0.0
        return 1.0 - (self.files_touched / self.total_files)


@dataclass
class BaselineReport:
    """Fleet-facing summary of what the current layout costs the observed workload."""

    total_files: int = 0
    total_bytes: int = 0
    # Frequency-weighted totals across every replayed query.
    weighted_files_touched: float = 0.0
    weighted_bytes_touched: float = 0.0
    weighted_query_count: int = 0
    per_query: list[QueryBaseline] = field(default_factory=list)
    # Share of filter-column observations that became usable Predicates. The research note's
    # initial gate is 0.80 (§6.3); this reports it rather than enforcing it.
    parser_coverage: float = 0.0
    abstained: AbstainReason | None = None

    @property
    def mean_files_touched(self) -> float:
        if self.weighted_query_count <= 0:
            return 0.0
        return self.weighted_files_touched / self.weighted_query_count

    @property
    def mean_file_prune_ratio(self) -> float:
        if self.total_files <= 0 or self.weighted_query_count <= 0:
            return 0.0
        return 1.0 - (self.mean_files_touched / self.total_files)

    @property
    def best_case_files(self) -> float:
        """Selectivity lower bound: no layout can beat this (research note §5.2).

        Approximated as ``observed_selectivity * total_files`` per query, where observed
        selectivity is the replayed fraction of *bytes* -- the cheapest available proxy when
        row counts are unknown. Used only as a free "is any improvement even possible?" gate.
        """
        if self.weighted_query_count <= 0 or self.total_bytes <= 0:
            return 0.0
        weighted = 0.0
        for q in self.per_query:
            selectivity = (q.bytes_touched / self.total_bytes) if self.total_bytes else 1.0
            weighted += q.frequency * selectivity * self.total_files
        return weighted / self.weighted_query_count


def _may_contain(
    pred_low: Bound | None,
    pred_high: Bound | None,
    file_low: Bound,
    file_high: Bound,
) -> bool:
    """Interval intersection, biased toward reading the file when undecidable.

    A file is skipped only when its bounds *prove* no row can match. Missing bounds, mixed
    types, or unorderable values all fall through to True, matching how an engine behaves when
    it cannot use statistics.
    """
    if file_low is None or file_high is None:
        return True
    try:
        if pred_low is not None and file_high < pred_low:
            return False
        if pred_high is not None and file_low > pred_high:
            return False
    except TypeError:
        # e.g. a string bound compared against a numeric literal: undecidable, so read it.
        return True
    return True


def replay_query(
    query: ReplayQuery,
    files: Sequence[Mapping[str, Any]],
) -> QueryBaseline:
    """Count files/bytes a single query must touch under the current layout.

    ``files`` are the dicts produced by ``layout_analyzer.fetch_file_stats``: ``lower_bounds``
    and ``upper_bounds`` keyed by column name, plus an optional ``file_size_bytes``. A file is
    touched unless some predicate proves it cannot match (conjunctive semantics).
    """
    total_files = len(files)
    total_bytes = sum(int(f.get("file_size_bytes") or 0) for f in files)

    columns_with_bounds: set[str] = set()
    for f in files:
        columns_with_bounds.update(f.get("lower_bounds") or {})
        columns_with_bounds.update(f.get("upper_bounds") or {})

    usable = [p for p in query.predicates if p.column in columns_with_bounds]
    missing = tuple(
        sorted({p.column for p in query.predicates if p.column not in columns_with_bounds})
    )

    if not usable:
        # Nothing to prune with: the query reads the table.
        return QueryBaseline(
            files_touched=total_files,
            bytes_touched=total_bytes,
            total_files=total_files,
            total_bytes=total_bytes,
            frequency=query.frequency,
            columns_without_bounds=missing,
        )

    files_touched = 0
    bytes_touched = 0
    for f in files:
        lower = f.get("lower_bounds") or {}
        upper = f.get("upper_bounds") or {}
        touched = True
        for predicate in usable:
            file_low = lower.get(predicate.column)
            file_high = upper.get(predicate.column)
            if file_low is None or file_high is None:
                continue  # no bounds for this column in this file: cannot prune on it
            if not predicate.may_match(file_low, file_high):
                touched = False
                break
        if touched:
            files_touched += 1
            bytes_touched += int(f.get("file_size_bytes") or 0)

    return QueryBaseline(
        files_touched=files_touched,
        bytes_touched=bytes_touched,
        total_files=total_files,
        total_bytes=total_bytes,
        frequency=query.frequency,
        columns_without_bounds=missing,
    )


def build_baseline(
    queries: Iterable[ReplayQuery],
    files: Sequence[Mapping[str, Any]],
    *,
    min_observations: int = 1,
    min_parser_coverage: float = 0.0,
) -> BaselineReport:
    """Replay a workload against current file bounds, or abstain with a reason.

    ``min_observations`` and ``min_parser_coverage`` default to permissive values so callers
    opt in to gating explicitly; the research note's production defaults are 200 observations
    and 0.80 coverage (§6.3), which belong in the caller's policy, not in this primitive.
    """
    queries = list(queries)
    report = BaselineReport(
        total_files=len(files),
        total_bytes=sum(int(f.get("file_size_bytes") or 0) for f in files),
    )

    if not files:
        report.abstained = AbstainReason.NO_FILE_STATS
        return report

    parseable = sum(len(q.predicates) for q in queries)
    unparseable = sum(len(q.unparseable_columns) for q in queries)
    observed = parseable + unparseable
    report.parser_coverage = (parseable / observed) if observed else 0.0

    if not parseable:
        report.abstained = AbstainReason.NO_PARSEABLE_PREDICATES
        return report

    observations = sum(q.frequency for q in queries)
    if observations < min_observations:
        report.abstained = AbstainReason.INSUFFICIENT_OBSERVATIONS
        return report

    if report.parser_coverage < min_parser_coverage:
        report.abstained = AbstainReason.LOW_PARSER_COVERAGE
        return report

    for query in queries:
        result = replay_query(query, files)
        report.per_query.append(result)
        report.weighted_files_touched += result.frequency * result.files_touched
        report.weighted_bytes_touched += result.frequency * result.bytes_touched
        report.weighted_query_count += result.frequency

    # If not one replayed query found bounds for any column it filters on, the baseline is
    # just "every query reads the whole table" -- true, but useless as evidence, and a sign
    # the file-stats sample is missing the columns this workload actually filters on.
    if report.per_query and all(
        result.files_touched == result.total_files and result.columns_without_bounds
        for result in report.per_query
    ):
        report.abstained = AbstainReason.NO_BOUNDS_FOR_FILTER_COLUMNS

    return report
