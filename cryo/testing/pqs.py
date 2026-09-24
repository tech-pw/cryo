"""Pivoted Query Synthesis probes for maintenance-correctness tests.

Rigger and Su, "Testing Database Engines via Pivoted Query Synthesis" (OSDI 2020): pick a
row (the pivot), generate a random predicate, evaluate it on the pivot under SQL's
three-valued logic, and *rectify* it so it is guaranteed TRUE for that row. A reader that
then fails to return the pivot has lost a row -- no full result oracle needed.

Cryo's use differs from the paper's. The subject under test is not a query engine but a
maintenance rewrite: probes run before and after compaction, through PyIceberg's scan
planning, so a rewrite that writes wrong column bounds or drops rows makes metadata pruning
skip the pivot. Fixtures here are small enough to also compare every probe's full result
against a reference evaluation of the same predicate over the known rows, which catches extra
rows too -- pivot containment alone cannot.

Predicates stay column-vs-literal (what Iceberg pushes into manifest and file pruning).
When a predicate evaluates to NULL on the pivot, PQS wraps it in ``IS NULL``; PyIceberg has no
IS NULL over boolean expressions, so it is rectified as ``expr OR column IS NULL`` on a column
that is null in the pivot, which is TRUE for the pivot all the same.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

ColumnType = Literal["long", "double", "string", "date", "boolean"]
Row = Mapping[str, Any]

EDGE_VALUES: dict[ColumnType, tuple[Any, ...]] = {
    "long": (None, 0, -1, 1, 42, 2**31, -(2**31) - 1, 2**53 + 1, -(2**62)),
    "double": (None, 0.0, 1.5, -2.5, 1e-300, 1e18, -1e18),
    # The 40-character value exceeds Iceberg's default 16-character bound truncation.
    "string": (None, "", "a", "A", "é", "zz", "a b", "0", "x" * 40),
    "date": (
        None,
        date(1970, 1, 1),
        date(1969, 12, 31),
        date(2000, 2, 29),
        date(2026, 9, 24),
        date(9999, 12, 31),
    ),
    "boolean": (None, True, False),
}

_ORDERED_OPS = ("=", "<>", "<", "<=", ">", ">=")


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType


@dataclass(frozen=True)
class Cmp:
    op: str
    column: str
    value: Any


@dataclass(frozen=True)
class IsNull:
    column: str


@dataclass(frozen=True)
class InList:
    column: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class Not:
    child: Expr


@dataclass(frozen=True)
class And:
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Or:
    left: Expr
    right: Expr


Expr = Cmp | IsNull | InList | Not | And | Or


@dataclass(frozen=True)
class Probe:
    pivot: Row
    predicate: Expr


def generate_rows(
    columns: Sequence[Column], count: int, rng: random.Random
) -> list[dict[str, Any]]:
    """Rows mixing edge values (nulls, extremes, truncation-length strings) with random ones;
    drawing from small pools also produces duplicates."""
    return [{c.name: _value(c.type, rng) for c in columns} for _ in range(count)]


def evaluate(expr: Expr, row: Row) -> bool | None:
    """SQL three-valued logic: None is UNKNOWN."""
    if isinstance(expr, Cmp):
        return _compare(expr.op, row.get(expr.column), expr.value)
    if isinstance(expr, IsNull):
        return row.get(expr.column) is None
    if isinstance(expr, InList):
        value = row.get(expr.column)
        return None if value is None else value in expr.values
    if isinstance(expr, Not):
        inner = evaluate(expr.child, row)
        return None if inner is None else not inner
    left, right = evaluate(expr.left, row), evaluate(expr.right, row)
    if isinstance(expr, And):
        if left is False or right is False:
            return False
        return None if left is None or right is None else True
    if left is True or right is True:
        return True
    return None if left is None or right is None else False


def rectify(expr: Expr, pivot: Row) -> Expr:
    result = evaluate(expr, pivot)
    if result is True:
        return expr
    if result is False:
        return Not(expr)
    null_column = next(
        (c for c in sorted(_columns(expr)) if pivot.get(c) is None),
        None,
    )
    if null_column is None:
        raise AssertionError("an UNKNOWN predicate must reference a column null in the pivot")
    return Or(expr, IsNull(null_column))


def random_predicate(
    columns: Sequence[Column], pivot: Row, rng: random.Random, *, depth: int = 3
) -> Expr:
    if depth <= 0 or rng.random() < 0.3:
        return _leaf(columns, pivot, rng)
    kind = rng.random()
    if kind < 0.2:
        return Not(random_predicate(columns, pivot, rng, depth=depth - 1))
    left = random_predicate(columns, pivot, rng, depth=depth - 1)
    right = random_predicate(columns, pivot, rng, depth=depth - 1)
    return And(left, right) if kind < 0.6 else Or(left, right)


def pivot_probes(
    rows: Sequence[Row],
    columns: Sequence[Column],
    rng: random.Random,
    *,
    count: int,
    depth: int = 3,
) -> list[Probe]:
    probes = []
    for _ in range(count):
        pivot = rng.choice(rows)
        predicate = rectify(random_predicate(columns, pivot, rng, depth=depth), pivot)
        probes.append(Probe(pivot=pivot, predicate=predicate))
    return probes


def expected_rows(rows: Iterable[Row], predicate: Expr) -> list[Row]:
    return [row for row in rows if evaluate(predicate, row) is True]


def multiset(rows: Iterable[Row]) -> Counter[tuple[tuple[str, Any], ...]]:
    return Counter(tuple(sorted(row.items())) for row in rows)


def shrink(probe: Probe, still_fails: Callable[[Probe], bool]) -> Probe:
    """Greedy minimization: keep replacing the predicate with a rectified sub-predicate while
    the failure reproduces, so a report shows the smallest failing probe found."""
    current = probe
    changed = True
    while changed:
        changed = False
        for sub in _children(current.predicate):
            candidate = Probe(current.pivot, rectify(sub, current.pivot))
            if _size(candidate.predicate) < _size(current.predicate) and still_fails(candidate):
                current, changed = candidate, True
                break
    return current


def to_pyiceberg(expr: Expr, *, negate: bool = False) -> Any:
    """PyIceberg row filter with SQL three-valued semantics.

    PyIceberg 0.11.1 does not treat NULL consistently under negation: ``v <> 5`` drops an
    explicit NULL but keeps a row whose file predates the column, and ``NOT v IN (...)`` keeps
    both. A compaction that rewrites schema-evolved files turns the second kind into the first,
    so those filters change results over identical data. Negation is therefore pushed down to
    the atoms PyIceberg evaluates consistently (=, <, <=, >, >=, IN, IS [NOT] NULL), with
    ``a <> x`` written as ``a < x OR a > x`` -- equivalent under three-valued logic.
    """
    ex = _expressions()

    if isinstance(expr, Not):
        return to_pyiceberg(expr.child, negate=not negate)
    if isinstance(expr, And | Or):
        left = to_pyiceberg(expr.left, negate=negate)
        right = to_pyiceberg(expr.right, negate=negate)
        conjunction = isinstance(expr, And) != negate
        return ex.And(left, right) if conjunction else ex.Or(left, right)
    if isinstance(expr, IsNull):
        return ex.NotNull(expr.column) if negate else ex.IsNull(expr.column)
    if isinstance(expr, InList):
        if not negate:
            return ex.In(expr.column, {_pyiceberg_literal(v) for v in expr.values})
        parts = [_not_equal(expr.column, v) for v in expr.values]
        result = parts[0]
        for part in parts[1:]:
            result = ex.And(result, part)
        return result
    op = _COMPLEMENT[expr.op] if negate else expr.op
    if op == "<>":
        return _not_equal(expr.column, expr.value)
    return {
        "=": ex.EqualTo,
        "<": ex.LessThan,
        "<=": ex.LessThanOrEqual,
        ">": ex.GreaterThan,
        ">=": ex.GreaterThanOrEqual,
    }[op](expr.column, _pyiceberg_literal(expr.value))


def _expressions() -> Any:
    # PyIceberg's pydantic-model constructors take positional arguments its stubs reject.
    from pyiceberg import expressions

    return expressions


_COMPLEMENT = {"=": "<>", "<>": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}


def _not_equal(column: str, value: Any) -> Any:
    ex = _expressions()

    if isinstance(value, bool):
        return ex.EqualTo(column, not value)
    literal = _pyiceberg_literal(value)
    return ex.Or(ex.LessThan(column, literal), ex.GreaterThan(column, literal))


def render(expr: Expr) -> str:
    """Human-readable SQL-like form for failure reports."""
    if isinstance(expr, Cmp):
        return f"{expr.column} {expr.op} {expr.value!r}"
    if isinstance(expr, IsNull):
        return f"{expr.column} IS NULL"
    if isinstance(expr, InList):
        return f"{expr.column} IN ({', '.join(repr(v) for v in expr.values)})"
    if isinstance(expr, Not):
        return f"NOT ({render(expr.child)})"
    op = "AND" if isinstance(expr, And) else "OR"
    return f"({render(expr.left)} {op} {render(expr.right)})"


def _leaf(columns: Sequence[Column], pivot: Row, rng: random.Random) -> Expr:
    column = rng.choice(columns)
    if rng.random() < 0.15:
        return IsNull(column.name)
    # Literals near the pivot's own value hit bound edges; the rest are anywhere in range.
    value = pivot.get(column.name) if rng.random() < 0.5 else None
    if value is None:
        value = _value(column.type, rng, allow_null=False)
    if rng.random() < 0.15:
        others = {_value(column.type, rng, allow_null=False) for _ in range(rng.randint(1, 3))}
        return InList(column.name, tuple(sorted({value, *others}, key=repr)))
    ops = ("=", "<>") if column.type == "boolean" else _ORDERED_OPS
    return Cmp(rng.choice(ops), column.name, value)


def _value(column_type: ColumnType, rng: random.Random, *, allow_null: bool = True) -> Any:
    edges = [v for v in EDGE_VALUES[column_type] if allow_null or v is not None]
    if rng.random() < 0.6:
        return rng.choice(edges)
    if column_type == "long":
        return rng.randint(-1000, 1000)
    if column_type == "double":
        return round(rng.uniform(-1000, 1000), 3)
    if column_type == "string":
        return "".join(rng.choice("abcXYZ 09") for _ in range(rng.randint(0, 20)))
    if column_type == "date":
        return date(2020, 1, 1) + timedelta(days=rng.randint(0, 3000))
    return rng.random() < 0.5


def _compare(op: str, left: Any, right: Any) -> bool | None:
    if left is None or right is None:
        return None
    if op == "=":
        return bool(left == right)
    if op == "<>":
        return bool(left != right)
    if op == "<":
        return bool(left < right)
    if op == "<=":
        return bool(left <= right)
    if op == ">":
        return bool(left > right)
    return bool(left >= right)


def _pyiceberg_literal(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _columns(expr: Expr) -> set[str]:
    if isinstance(expr, Cmp | IsNull | InList):
        return {expr.column}
    if isinstance(expr, Not):
        return _columns(expr.child)
    return _columns(expr.left) | _columns(expr.right)


def _children(expr: Expr) -> list[Expr]:
    if isinstance(expr, Not):
        return [expr.child, *_children(expr.child)]
    if isinstance(expr, And | Or):
        return [expr.left, expr.right, *_children(expr.left), *_children(expr.right)]
    return []


def _size(expr: Expr) -> int:
    return 1 + sum(_size(child) for child in _direct_children(expr))


def _direct_children(expr: Expr) -> list[Expr]:
    if isinstance(expr, Not):
        return [expr.child]
    if isinstance(expr, And | Or):
        return [expr.left, expr.right]
    return []
