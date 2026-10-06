"""Simulate a hypothetical layout's pruning on a joint row sample.

Phase 2 of the clustering advisor. ``baseline_replay`` answers what the *current* layout costs;
this answers what a *candidate* layout would cost, without rewriting a byte.

There is no validated closed-form pruning predictor for space-filling curves in the literature
(research note §5.3), so the only honest approach is to simulate:

1. take a deterministic joint row sample -- joint, because marginal per-column sketches cannot
   represent correlation, and correlated dimensions are exactly where naive column selection
   goes wrong;
2. apply the candidate's encoding (quantile map, NULL handling, curve) to each row;
3. sort by the resulting key and cut the rows into synthetic files at target size;
4. derive each synthetic file's min/max box per column;
5. replay the observed workload against those boxes, reusing the same predicate evaluator the
   baseline uses so the two numbers are comparable;
6. bootstrap over queries and rows to get an interval, not a point estimate.

**Sampled bounds are inward-biased.** A sample's min/max per file is narrower than the real
file's would be, which makes simulated pruning look better than reality. ``BOUND_INFLATION``
widens each synthetic box toward the neighbouring order statistic to counter this. The bias
cannot be fully removed by inflation alone, which is why every result here is a *prediction*
requiring canary validation (Phase 3) before anything is rewritten -- Snowflake reports realized
clustering cost differing from estimate by 100%+, and AutoComp measured 28% file-reduction
overestimation in production.

Nothing in this module rewrites data or applies a recommendation.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryo.optimizer.baseline_replay import ReplayQuery, _may_contain
from cryo.optimizer.curve_encoding import (
    DEFAULT_CURVE_BITS,
    curve_key,
    quantile_boundaries,
    rank_normalise,
)

logger = logging.getLogger(__name__)

# Widen each simulated file's bounds by this fraction of its own span, countering the inward
# bias of taking min/max from a sample rather than the full file. Deliberately conservative:
# over-wide bounds understate pruning, and understating a benefit is the safe direction.
BOUND_INFLATION = 0.05

# Quantile buckets per column when deriving the rank transform from the sample.
_SKETCH_BUCKETS = 256

# Bootstrap resamples for the confidence interval. Enough for a stable 10th percentile without
# making candidate search expensive -- the search evaluates dozens of candidates.
_BOOTSTRAP_ROUNDS = 64
_LOWER_CONFIDENCE_PERCENTILE = 10


@dataclass(frozen=True)
class LayoutCandidate:
    """A hypothetical layout to evaluate.

    ``strategy`` is ``sort`` (lexicographic on ``columns`` in order), ``zorder``/``hilbert``
    (symmetric curve over ``columns``), or ``current`` (the mandatory control).
    """

    strategy: str
    columns: tuple[str, ...]
    rank_normalised: bool = True

    @property
    def name(self) -> str:
        if self.strategy == "current":
            return "current layout"
        cols = ", ".join(self.columns)
        suffix = "" if self.rank_normalised or self.strategy == "sort" else " (raw values)"
        return f"{self.strategy}({cols}){suffix}"

    @property
    def dimensions(self) -> int:
        return len(self.columns)


@dataclass
class SimulationResult:
    """Predicted workload cost under one candidate layout."""

    candidate: LayoutCandidate
    mean_files_touched: float
    mean_bytes_touched: float
    total_files: int
    # Conservative lower bound on files touched -- the pessimistic end of the bootstrap, so
    # ranking on it prefers candidates whose benefit is robust rather than lucky.
    conservative_files_touched: float
    file_reduction_vs_baseline: float = 0.0
    conservative_file_reduction: float = 0.0
    is_prediction: bool = True  # never a measurement; canary required

    @property
    def mean_prune_ratio(self) -> float:
        if self.total_files <= 0:
            return 0.0
        return 1.0 - (self.mean_files_touched / self.total_files)


@dataclass
class SimulationReport:
    """Every candidate, ranked by conservative predicted benefit."""

    baseline_files: float = 0.0
    results: list[SimulationResult] = field(default_factory=list)
    sample_rows: int = 0
    synthetic_files: int = 0

    def ranked(self) -> list[SimulationResult]:
        """Best first, by conservative reduction -- never by the optimistic mean."""
        return sorted(self.results, key=lambda r: r.conservative_file_reduction, reverse=True)

    @property
    def winner(self) -> SimulationResult | None:
        ranked = self.ranked()
        return ranked[0] if ranked else None


def _column_boundaries(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> dict[str, list[Any]]:
    """Per-column quantile cut points from the joint sample."""
    return {
        column: quantile_boundaries([row.get(column) for row in rows], _SKETCH_BUCKETS)
        for column in columns
    }


def _sort_rows(
    rows: Sequence[Mapping[str, Any]],
    candidate: LayoutCandidate,
    boundaries: Mapping[str, Sequence[Any]],
) -> list[Mapping[str, Any]]:
    """Order the sample the way the candidate layout would write it."""
    if candidate.strategy == "sort":
        # Lexicographic: column order is semantic, unlike the curves.
        def sort_key(row: Mapping[str, Any]) -> tuple:
            key = []
            for column in candidate.columns:
                value = row.get(column)
                # (is_null, coordinate) keeps NULLs grouped and avoids cross-type comparison.
                key.append(
                    (
                        value is None,
                        rank_normalise(value, boundaries.get(column, ())),
                    )
                )
            return tuple(key)

        return sorted(rows, key=sort_key)

    def curve_sort_key(row: Mapping[str, Any]) -> int:
        coordinates = [
            rank_normalise(row.get(column), boundaries.get(column, ()))
            if candidate.rank_normalised
            else _raw_coordinate(row.get(column))
            for column in candidate.columns
        ]
        return curve_key(coordinates, candidate.strategy, bits=DEFAULT_CURVE_BITS)

    return sorted(rows, key=curve_sort_key)


def _raw_coordinate(value: object) -> int:
    """Coordinate without rank normalisation, approximating Iceberg's raw-byte encoding.

    Exists so a candidate can be simulated *without* the skew treatment, making the value of
    that treatment measurable rather than assumed.
    """
    if value is None:
        return 0
    max_coord = (1 << DEFAULT_CURVE_BITS) - 1
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return max(0, min(max_coord, int(value) & max_coord))
    text = str(value)[:4].encode("utf-8", "ignore")
    packed = int.from_bytes(text.ljust(4, b"\x00"), "big")
    return packed % (max_coord + 1)


def _synthetic_files(
    ordered_rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    rows_per_file: int,
) -> list[dict[str, Any]]:
    """Cut ordered rows into files and derive each one's inflated min/max box."""
    files: list[dict[str, Any]] = []
    for start in range(0, len(ordered_rows), rows_per_file):
        chunk = ordered_rows[start : start + rows_per_file]
        if not chunk:
            continue
        lower: dict[str, Any] = {}
        upper: dict[str, Any] = {}
        for column in columns:
            values: list[Any] = []
            for row in chunk:
                value = row.get(column)
                if value is not None:
                    values.append(value)
            if not values:
                continue
            try:
                low, high = min(values), max(values)
            except TypeError:
                continue  # unorderable column: no bounds, so it never prunes
            low, high = _inflate(low, high)
            lower[column] = low
            upper[column] = high
        files.append({"lower_bounds": lower, "upper_bounds": upper, "file_size_bytes": len(chunk)})
    return files


def _inflate(low: Any, high: Any) -> tuple[Any, Any]:
    """Widen a sampled bound toward what the full file's bound would have been."""
    if isinstance(low, bool) or isinstance(high, bool):
        return low, high
    if isinstance(low, int | float) and isinstance(high, int | float):
        span = (high - low) * BOUND_INFLATION
        if span <= 0:
            return low, high
        widened_low = low - span
        widened_high = high + span
        if isinstance(low, int) and isinstance(high, int):
            return int(widened_low), int(widened_high) + 1
        return widened_low, widened_high
    return low, high  # strings and other types: inflation is not well defined


def _replay_against(
    queries: Sequence[ReplayQuery],
    files: Sequence[Mapping[str, Any]],
) -> tuple[float, float]:
    """Frequency-weighted mean files and bytes touched, using the baseline's own evaluator."""
    total_frequency = sum(q.frequency for q in queries) or 1
    files_sum = 0.0
    bytes_sum = 0.0
    for query in queries:
        touched_files = 0
        touched_bytes = 0
        for entry in files:
            lower = entry.get("lower_bounds") or {}
            upper = entry.get("upper_bounds") or {}
            touched = True
            for predicate in query.predicates:
                file_low = lower.get(predicate.column)
                file_high = upper.get(predicate.column)
                if file_low is None or file_high is None:
                    continue
                if not _may_contain(predicate.lower, predicate.upper, file_low, file_high):
                    touched = False
                    break
            if touched:
                touched_files += 1
                touched_bytes += int(entry.get("file_size_bytes") or 0)
        files_sum += query.frequency * touched_files
        bytes_sum += query.frequency * touched_bytes
    return files_sum / total_frequency, bytes_sum / total_frequency


def simulate_candidate(
    candidate: LayoutCandidate,
    *,
    rows: Sequence[Mapping[str, Any]],
    queries: Sequence[ReplayQuery],
    target_files: int,
    seed: int = 20260904,
) -> SimulationResult:
    """Predict a candidate's workload cost, with a bootstrap lower confidence bound.

    ``target_files`` is how many files the table would hold after the rewrite; the sample is cut
    into that many synthetic files so simulated granularity matches the real one.
    """
    columns = list(candidate.columns)
    rows_per_file = max(1, len(rows) // max(1, target_files))

    boundaries = _column_boundaries(rows, columns)
    ordered = _sort_rows(rows, candidate, boundaries)
    files = _synthetic_files(ordered, columns, rows_per_file)

    mean_files, mean_bytes = _replay_against(queries, files)

    # Bootstrap over the *query* sample: which queries were observed is the larger source of
    # uncertainty, since a workload shift changes the answer more than row sampling noise.
    rng = random.Random(seed)
    samples: list[float] = []
    if len(queries) > 1:
        for _ in range(_BOOTSTRAP_ROUNDS):
            resampled = [queries[rng.randrange(len(queries))] for _ in range(len(queries))]
            resampled_files, _ = _replay_against(resampled, files)
            samples.append(resampled_files)
        samples.sort()
        # Pessimistic end: the *high* files-touched percentile is the conservative case.
        index = min(
            len(samples) - 1,
            int(len(samples) * (100 - _LOWER_CONFIDENCE_PERCENTILE) / 100),
        )
        conservative = samples[index]
    else:
        conservative = mean_files

    return SimulationResult(
        candidate=candidate,
        mean_files_touched=mean_files,
        mean_bytes_touched=mean_bytes,
        total_files=len(files),
        conservative_files_touched=conservative,
    )


def simulate_candidates(
    candidates: Sequence[LayoutCandidate],
    *,
    rows: Sequence[Mapping[str, Any]],
    queries: Sequence[ReplayQuery],
    baseline_files: float,
    target_files: int,
    seed: int = 20260904,
) -> SimulationReport:
    """Evaluate every candidate on identical inputs and rank by conservative benefit.

    ``baseline_files`` comes from ``baseline_replay`` against the *real* current layout, so a
    candidate's reported reduction is measured against observed behaviour rather than against
    another simulation.
    """
    report = SimulationReport(
        baseline_files=baseline_files,
        sample_rows=len(rows),
        synthetic_files=target_files,
    )
    if not rows or not queries:
        return report

    for candidate in candidates:
        result = simulate_candidate(
            candidate,
            rows=rows,
            queries=queries,
            target_files=target_files,
            seed=seed,
        )
        if baseline_files > 0:
            # Scale simulated file counts onto the real table's granularity before comparing:
            # the sample is cut into target_files synthetic files, the baseline counted real
            # ones, so the ratio is comparable but the absolute counts are not.
            scale = baseline_files / max(result.total_files, 1)
            result.file_reduction_vs_baseline = max(
                0.0, 1.0 - (result.mean_files_touched * scale) / baseline_files
            )
            result.conservative_file_reduction = max(
                0.0, 1.0 - (result.conservative_files_touched * scale) / baseline_files
            )
        report.results.append(result)

    return report
