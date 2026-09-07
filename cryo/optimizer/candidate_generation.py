"""Bounded candidate generation for the clustering advisor.

The naive approach -- take the top *k* most frequently filtered columns -- is wrong, and the
research review is specific about why (§2.1): frequency ignores selectivity, predicate
co-occurrence, data correlation, existing natural order, and the fact that every added
dimension dilutes the ones already there. A column filtered in every query with an unselective
range is worth far less than one filtered in a third of queries with a point lookup.

Optimal selection is NP-hard (Balanced MaxSkip, by reduction from hypergraph bisection), so
this does what deployed physical-design advisors do -- DB2's MDC advisor, Qd-tree, Redshift's
MDDL: screen columns for eligibility, score them, generate a *bounded* candidate set from
predicate co-occurrence, and let simulation pick the winner. No formula here claims to find the
optimum; it narrows a huge space to something a simulator can evaluate.

The output feeds ``layout_simulation``. Nothing here decides anything on its own.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations

from cryo.optimizer.layout_simulation import LayoutCandidate

logger = logging.getLogger(__name__)

# Operational caps. Snowflake recommends at most 3-4 clustering expressions; Databricks caps
# liquid clustering at 4 keys and warns four can underperform two below 10 TB. The cap bounds
# risk and search cost -- simulation should usually choose fewer.
MAX_CURVE_DIMENSIONS = 4
CANDIDATE_POOL_SIZE = 8
MAX_CANDIDATES = 64

# A column must appear in at least this share of the workload's weighted bytes to be worth a
# dimension. Below it, a dimension is spent on queries that barely matter.
MIN_COLUMN_WORKLOAD_SHARE = 0.05


class ColumnIneligibility(StrEnum):
    """Why a column cannot be a useful clustering dimension."""

    NO_BOUNDS = "no_min_max_statistics"
    NEAR_CONSTANT = "near_constant"
    NEAR_UNIQUE = "near_unique_without_point_lookups"
    NULL_HEAVY = "null_heavy"
    NOT_SARGABLE = "predicates_not_sargable"
    UNORDERABLE = "unorderable_type"


@dataclass(frozen=True)
class ColumnSignal:
    """What the advisor knows about one candidate column."""

    name: str
    # Share of observed queries filtering this column.
    filter_frequency: float = 0.0
    # Mean fraction of rows its predicates match. Lower prunes better.
    avg_selectivity: float | None = None
    # Distinct values / row count. Near 0 is near-constant; near 1 is near-unique.
    ndv_ratio: float | None = None
    null_fraction: float = 0.0
    has_bounds: bool = True
    orderable: bool = True
    # Share of this column's predicates expressible as min/max ranges.
    sargable_fraction: float = 1.0
    # Share of the column's predicates that are point lookups.
    equality_fraction: float = 0.0

    @property
    def univariate_value(self) -> float:
        """Frequency weighted by how much each predicate actually prunes.

        ``frequency * (1 - selectivity)``: a column filtered often but unselectively scores
        below one filtered less often but precisely. Unknown selectivity falls back to 0.5 --
        "no information" -- rather than to an optimistic assumption.
        """
        selectivity = 0.5 if self.avg_selectivity is None else self.avg_selectivity
        return self.filter_frequency * (1.0 - min(1.0, max(0.0, selectivity)))


@dataclass
class ScreeningResult:
    """Eligible columns, plus a machine-readable reason for each rejection."""

    eligible: list[ColumnSignal] = field(default_factory=list)
    rejected: dict[str, ColumnIneligibility] = field(default_factory=dict)


def screen_columns(
    signals: Sequence[ColumnSignal],
    *,
    near_constant_ndv: float = 0.001,
    near_unique_ndv: float = 0.9,
    null_heavy_fraction: float = 0.9,
    min_sargable_fraction: float = 0.5,
) -> ScreeningResult:
    """Drop columns that cannot produce useful file bounds, recording why.

    A near-unique column is only rejected when the workload is *not* dominated by point
    lookups: a high-cardinality id is an excellent sort key for equality filters and a poor one
    for ranges.
    """
    result = ScreeningResult()
    for signal in signals:
        if not signal.has_bounds:
            result.rejected[signal.name] = ColumnIneligibility.NO_BOUNDS
        elif not signal.orderable:
            result.rejected[signal.name] = ColumnIneligibility.UNORDERABLE
        elif signal.ndv_ratio is not None and signal.ndv_ratio < near_constant_ndv:
            result.rejected[signal.name] = ColumnIneligibility.NEAR_CONSTANT
        elif (
            signal.ndv_ratio is not None
            and signal.ndv_ratio > near_unique_ndv
            and signal.equality_fraction < 0.5
        ):
            result.rejected[signal.name] = ColumnIneligibility.NEAR_UNIQUE
        elif signal.null_fraction > null_heavy_fraction:
            result.rejected[signal.name] = ColumnIneligibility.NULL_HEAVY
        elif signal.sargable_fraction < min_sargable_fraction:
            result.rejected[signal.name] = ColumnIneligibility.NOT_SARGABLE
        else:
            result.eligible.append(signal)
    return result


def _correlated(
    a: str,
    b: str,
    correlations: Mapping[tuple[str, str], float],
    threshold: float,
) -> bool:
    """Whether two columns are redundant enough that pairing them wastes a dimension."""
    value = correlations.get((a, b), correlations.get((b, a), 0.0))
    return abs(value) >= threshold


def generate_candidates(
    signals: Sequence[ColumnSignal],
    *,
    cooccurrence: Mapping[tuple[str, str], float] | None = None,
    correlations: Mapping[tuple[str, str], float] | None = None,
    hilbert_available: bool = False,
    max_dimensions: int = MAX_CURVE_DIMENSIONS,
    pool_size: int = CANDIDATE_POOL_SIZE,
    max_candidates: int = MAX_CANDIDATES,
    correlation_threshold: float = 0.9,
) -> list[LayoutCandidate]:
    """Build a bounded candidate set for simulation.

    ``cooccurrence`` maps a column pair to the workload weight of queries filtering *both*;
    it is what makes multi-column candidates workload-driven rather than combinatorial.
    ``correlations`` maps a pair to a data-correlation coefficient, used to drop redundant
    dimensions -- two columns that always move together contribute one dimension's worth of
    pruning while costing two.

    The current layout is always included as a control, so simulation can conclude that doing
    nothing wins.
    """
    cooccurrence = cooccurrence or {}
    correlations = correlations or {}

    screened = screen_columns(signals)
    pool = sorted(screened.eligible, key=lambda s: s.univariate_value, reverse=True)[:pool_size]
    pool = [s for s in pool if s.univariate_value >= MIN_COLUMN_WORKLOAD_SHARE]

    candidates: list[LayoutCandidate] = [LayoutCandidate("current", ())]
    if not pool:
        return candidates

    names = [s.name for s in pool]

    # Single-column sorts: the baseline every multi-column candidate must beat. A workload with
    # one dominant predicate should stop here, and often does.
    for name in names:
        candidates.append(LayoutCandidate("sort", (name,)))

    # Multi-column candidates, ordered by how much workload actually filters the whole set.
    for k in range(2, min(max_dimensions, len(names)) + 1):
        scored: list[tuple[float, tuple[str, ...]]] = []
        for combo in combinations(names, k):
            if any(
                _correlated(a, b, correlations, correlation_threshold)
                for a, b in combinations(combo, 2)
            ):
                continue  # redundant dimensions
            weight = sum(
                cooccurrence.get((a, b), cooccurrence.get((b, a), 0.0))
                for a, b in combinations(combo, 2)
            )
            if weight <= 0:
                continue  # no query filters these together: a curve over them helps nobody
            scored.append((weight, combo))

        scored.sort(key=lambda item: item[0], reverse=True)
        for _, combo in scored[:4]:  # keep the search bounded per k
            # Compound sort: order matters, so put the most selective prefix first.
            ordered = tuple(
                sorted(combo, key=lambda n: _signal_for(pool, n).univariate_value, reverse=True)
            )
            candidates.append(LayoutCandidate("sort", ordered))
            # Curves treat dimensions symmetrically; a canonical order keeps candidates unique.
            symmetric = tuple(sorted(combo))
            candidates.append(LayoutCandidate("zorder", symmetric))
            if hilbert_available:
                candidates.append(LayoutCandidate("hilbert", symmetric))

    # Deduplicate while preserving order, then cap.
    seen: set[tuple[str, tuple[str, ...], bool]] = set()
    unique: list[LayoutCandidate] = []
    for candidate in candidates:
        key = (candidate.strategy, candidate.columns, candidate.rank_normalised)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique[:max_candidates]


def _signal_for(pool: Sequence[ColumnSignal], name: str) -> ColumnSignal:
    for signal in pool:
        if signal.name == name:
            return signal
    return ColumnSignal(name=name)
