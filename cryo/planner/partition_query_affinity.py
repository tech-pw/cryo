"""Per-partition query affinity for AutoComp benefit scoring.

AutoComp ("Automated Data Compaction for Log-Structured Tables in Data Lakes", SIGMOD 2025,
LinkedIn) weighs a partition's compaction benefit by how often queries actually hit it.
``benefit_score.update_partition_benefit_scores`` currently broadcasts the table's overall
queries/day to every partition equally, so a cold partition with many small files can outrank
a hot partition with fewer (see that module's former "Future: join with per-partition filter
predicates" note). This module closes that gap.

Cryo's curated ``QueryLog`` rows carry the raw ``query_text`` but not parsed filter literals
(``QueryLog.filter_columns`` only ever holds column/operator, never the comparison value -- see
``cryo/telemetry/parser.py`` -- and the tiered-pipeline curator writes it as ``None`` outright).
So attribution here re-parses ``query_text`` with sqlglot and matches literals against the
*actual* partition values recorded by the latest scan (``TablePartition.partition_values``),
rather than guessing a value range independently -- that keeps the match set bounded (never
larger than the scanned partition count) and correct by construction: a predicate either
selects some subset of the partitions that exist, or it doesn't match this scan at all.

Pure / no I/O: callers pass in already-loaded query texts and partition values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import sqlglot
from sqlglot import exp

from cryo.domain.enums import PartitionTransform
from cryo.observability.logging import quiet_sqlglot_logger
from cryo.planner.partitions import (
    PartitionFieldSpec,
    date_to_transform_value,
    parse_literal_to_date,
)

# Below this fraction of in-window queries resolving to a specific partition value, there's too
# little signal to trust per-partition scores over the existing table-broadcast behavior.
MIN_COVERAGE_RATIO = 0.2

_COMPARABLE_TRANSFORMS = (PartitionTransform.DAY, PartitionTransform.MONTH, PartitionTransform.YEAR)

_COMPARATORS: dict[type, Callable[[Any, Any], bool]] = {
    exp.EQ: lambda v, t: v == t,
    exp.GT: lambda v, t: v > t,
    exp.GTE: lambda v, t: v >= t,
    exp.LT: lambda v, t: v < t,
    exp.LTE: lambda v, t: v <= t,
}
_FLIPPED_OP: dict[type, type] = {
    exp.GT: exp.LT,
    exp.LT: exp.GT,
    exp.GTE: exp.LTE,
    exp.LTE: exp.GTE,
    exp.EQ: exp.EQ,
}
_COMPARISON_NODES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In)


@dataclass(frozen=True)
class PartitionAffinity:
    """Per-partition query-hit credit for one table's latest scan window.

    ``hits`` maps a partition_value key (matching ``TablePartition.partition_value``) to
    fractional query-hit credit; a query whose predicate resolves to N partitions splits 1.0
    credit evenly across them so total attributed credit never exceeds query count.
    """

    hits: dict[str, float]
    total_queries: int
    resolved_queries: int

    @property
    def coverage_ratio(self) -> float:
        if self.total_queries <= 0:
            return 0.0
        return self.resolved_queries / self.total_queries

    @property
    def is_trustworthy(self) -> bool:
        return self.total_queries > 0 and self.coverage_ratio >= MIN_COVERAGE_RATIO


def compute_partition_affinity(
    query_texts: Sequence[str | None],
    field: PartitionFieldSpec,
    known_partition_values: Mapping[str, Any],
) -> PartitionAffinity:
    """Attribute each query to the partition(s) its predicate on ``field`` selects.

    ``known_partition_values`` is ``{partition_value_key: raw_transform_space_value}`` for the
    table's latest scan (e.g. ``{"20210": 20210}`` for a day-transform partition, since the
    scanner already stores the raw epoch-day int both as the row's ``partition_value`` string
    key and inside ``partition_values``).
    """
    hits: dict[str, float] = {}
    resolved = 0
    total = 0
    for text in query_texts:
        if not text:
            continue
        total += 1
        matchers = _matchers_for_query(text, field)
        if not matchers:
            continue
        matched_keys = [
            key
            for key, raw_value in known_partition_values.items()
            if any(_safe_call(matcher, raw_value) for matcher in matchers)
        ]
        if not matched_keys:
            # Predicate parsed fine but selects nothing in *this* scan (e.g. a future date not
            # yet written) -- still referenced the column, just not attributable right now.
            continue
        resolved += 1
        weight = 1.0 / len(matched_keys)
        for key in matched_keys:
            hits[key] = hits.get(key, 0.0) + weight
    return PartitionAffinity(hits=hits, total_queries=total, resolved_queries=resolved)


def _safe_call(matcher: Callable[[Any], bool], value: Any) -> bool:
    try:
        return bool(matcher(value))
    except TypeError:
        # Comparing incompatible types (e.g. an int transform value against a str partition
        # value that failed to coerce) -- not a match, not a crash.
        return False


def _matchers_for_query(query_text: str, field: PartitionFieldSpec) -> list[Callable[[Any], bool]]:
    quiet_sqlglot_logger()  # never let sqlglot log the statement text (#165)
    try:
        parsed = sqlglot.parse_one(query_text, read="trino")
    except Exception:
        return []
    where = parsed.find(exp.Where)
    if where is None:
        return []

    matchers: list[Callable[[Any], bool]] = []
    for condition in where.find_all(*_COMPARISON_NODES):
        # find_all's return type is inferred from the *union* of the passed-in expression
        # types (exp.Condition, the common ancestor); every member of _COMPARISON_NODES is
        # also an exp.Expression (the type _node_references/_matcher_for_node actually need
        # .args from) via multiple inheritance -- true at runtime, just not expressible in
        # find_all's generic signature for a heterogeneous tuple of types.
        node = cast(exp.Expression, condition)
        if not _node_references(node, field):
            continue
        matcher = _matcher_for_node(node, field)
        if matcher is not None:
            matchers.append(matcher)
    return matchers


def _node_references(node: exp.Expression, field: PartitionFieldSpec) -> bool:
    if isinstance(node, (exp.Between, exp.In)):
        return _column_side(node.this, field) is not None
    return (
        _column_side(node.this, field) is not None
        or _column_side(node.expression, field) is not None
    )


def _column_side(node: exp.Expression | None, field: PartitionFieldSpec) -> str | None:
    """'base' if this node is the partition's base column, 'derived' if it's the exploded
    transform column (e.g. ``event_date_day``), else None."""
    if not isinstance(node, exp.Column):
        return None
    name = node.name.lower()
    if name == field.base_column.lower():
        return "base"
    if field.partition_field and name == field.partition_field.lower():
        return "derived"
    return None


def _resolve_literal(node: exp.Expression | None) -> Any | None:
    if node is None:
        return None
    if isinstance(node, exp.Cast):
        node = node.this
    if not isinstance(node, exp.Literal):
        return None
    if node.is_string:
        return str(node.this)
    text = str(node.this)
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _to_target_value(literal: Any, kind: str, field: PartitionFieldSpec) -> Any | None:
    """Convert a raw query-literal into the same value space as scanned partition values."""
    if kind == "derived":
        return literal  # already transform-space (e.g. filtering event_date_day directly)
    if field.transform is PartitionTransform.IDENTITY:
        return literal
    if field.transform not in _COMPARABLE_TRANSFORMS:
        # BUCKET/UNSUPPORTED: can't derive the transform's output from a base-column literal
        # without the engine's own hash function -- do not invent fake hits.
        return None
    parsed_date = parse_literal_to_date(literal)
    if parsed_date is None:
        return None
    return date_to_transform_value(parsed_date, field.transform)


def _matcher_for_node(
    node: exp.Expression, field: PartitionFieldSpec
) -> Callable[[Any], bool] | None:
    if isinstance(node, exp.Between):
        kind = _column_side(node.this, field)
        if kind is None:
            return None
        low = _to_target_value(_resolve_literal(node.args.get("low")), kind, field)
        high = _to_target_value(_resolve_literal(node.args.get("high")), kind, field)
        if low is None or high is None:
            return None
        return _make_between_matcher(low, high)

    if isinstance(node, exp.In):
        kind = _column_side(node.this, field)
        if kind is None:
            return None
        targets = []
        for expr_node in node.args.get("expressions") or []:
            literal = _resolve_literal(expr_node)
            target = _to_target_value(literal, kind, field) if literal is not None else None
            if target is None:
                return None
            targets.append(target)
        return _make_in_matcher(set(targets))

    if isinstance(node, exp.NEQ):
        # Excludes one value / matches "everything else" -- not a usable positive signal for
        # which partition is hot.
        return None

    comparator = _COMPARATORS.get(type(node))
    op_type: type[exp.Expression] | None = type(node)

    left_kind = _column_side(node.this, field)
    if left_kind is not None:
        literal = _resolve_literal(node.expression)
    else:
        right_kind = _column_side(node.expression, field)
        if right_kind is None:
            return None
        left_kind = right_kind
        literal = _resolve_literal(node.this)
        op_type = _FLIPPED_OP.get(type(node))
        comparator = _COMPARATORS.get(op_type) if op_type is not None else None

    if comparator is None:
        return None
    target = _to_target_value(literal, left_kind, field) if literal is not None else None
    if target is None:
        return None
    return _make_comparator_matcher(comparator, target)


def _make_between_matcher(low: Any, high: Any) -> Callable[[Any], bool]:
    def matcher(value: Any) -> bool:
        return bool(low <= value <= high)

    return matcher


def _make_in_matcher(targets: set[Any]) -> Callable[[Any], bool]:
    def matcher(value: Any) -> bool:
        return value in targets

    return matcher


def _make_comparator_matcher(
    comparator: Callable[[Any, Any], bool], target: Any
) -> Callable[[Any], bool]:
    def matcher(value: Any) -> bool:
        return comparator(value, target)

    return matcher
