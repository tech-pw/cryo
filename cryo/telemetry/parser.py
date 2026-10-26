"""SQL query parser to extract predicates, joins, and projections.

Uses regex-based parsing for common SQL patterns. For production use,
consider integrating with sqlglot or a proper SQL parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from cryo.observability.logging import quiet_sqlglot_logger


@dataclass
class ColumnReference:
    """A column reference extracted from SQL."""

    column: str
    table_alias: str | None = None
    operator: str | None = None  # =, <, >, <=, >=, <>, LIKE, IN, BETWEEN
    is_equality: bool = False
    is_range: bool = False


@dataclass
class PredicateInfo:
    """Extracted predicate information from a SQL query."""

    filter_columns: list[ColumnReference] = field(default_factory=list)
    join_columns: list[str] = field(default_factory=list)
    projection_columns: list[str] = field(default_factory=list)
    tables_referenced: list[str] = field(default_factory=list)
    query_type: Literal["SELECT", "INSERT", "UPDATE", "DELETE", "OTHER"] = "OTHER"


@dataclass(frozen=True)
class QueryRange:
    column: str
    lower: bool | int | float | str | None
    upper: bool | int | float | str | None


# Patterns for SQL parsing
_EQUALITY_OPS = {"=", "==", "<>", "!=", "IN", "NOT IN"}
_RANGE_OPS = {"<", ">", "<=", ">=", "BETWEEN", "NOT BETWEEN"}
_ALL_OPS = _EQUALITY_OPS | _RANGE_OPS | {"LIKE", "NOT LIKE", "IS", "IS NOT"}

# Regex patterns
_SELECT_PATTERN = re.compile(r"\bSELECT\s+(.+?)\s+FROM\b", re.IGNORECASE | re.DOTALL)
_FROM_PATTERN = re.compile(r"\bFROM\s+([^\s,]+)", re.IGNORECASE)
_JOIN_PATTERN = re.compile(
    r"\bJOIN\s+([^\s]+)\s+(?:AS\s+)?(\w+)?\s+ON\s+(.+?)(?=\s+(?:LEFT|RIGHT|INNER|OUTER|FULL|CROSS|JOIN|WHERE|GROUP|ORDER|LIMIT|$))",
    re.IGNORECASE | re.DOTALL,
)
_WHERE_PATTERN = re.compile(
    r"\bWHERE\s+(.+?)(?=\s+(?:GROUP|ORDER|LIMIT|HAVING|$))", re.IGNORECASE | re.DOTALL
)
_COLUMN_PATTERN = re.compile(
    r"(\w+(?:\.\w+)?)\s*(=|<>|!=|<=|>=|<|>|LIKE|IN|BETWEEN|IS)\s*", re.IGNORECASE
)
_JOIN_ON_PATTERN = re.compile(r"(\w+(?:\.\w+)?)\s*=\s*(\w+(?:\.\w+)?)", re.IGNORECASE)
_TABLE_REF_PATTERN = re.compile(
    r"(?:FROM|JOIN)\s+([\"']?[\w.]+[\"']?)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE
)


def parse_query_predicates(sql: str) -> PredicateInfo:
    """Parse a SQL query to extract predicate information."""
    sqlglot_result = _parse_with_sqlglot(sql)
    if sqlglot_result is not None:
        return sqlglot_result
    return _parse_with_regex(sql)


def extract_query_ranges(sql: str, *, open_ended: bool = False) -> list[QueryRange]:
    """Range predicates per column; ``open_ended`` also keeps one-sided ones (``x >= 5``)
    with the missing side as None."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []
    quiet_sqlglot_logger()  # never let sqlglot log the statement text (#165)
    try:
        parsed = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return []

    bounds: dict[str, dict[str, bool | int | float | str]] = {}
    for between in parsed.find_all(exp.Between):
        if not isinstance(between.this, exp.Column):
            continue
        low = _literal_value(between.args.get("low"))
        high = _literal_value(between.args.get("high"))
        if low is not None and high is not None:
            bounds[between.this.name] = {"lower": low, "upper": high}

    comparisons = (
        (exp.GT, "lower"),
        (exp.GTE, "lower"),
        (exp.LT, "upper"),
        (exp.LTE, "upper"),
    )
    for comparison_type, side in comparisons:
        for comparison in parsed.find_all(comparison_type):
            column, value, effective_side = _comparison_parts(comparison, side)
            if column is not None and value is not None:
                bounds.setdefault(column, {})[effective_side] = value
    return [
        QueryRange(column, values.get("lower"), values.get("upper"))
        for column, values in sorted(bounds.items())
        if ("lower" in values and "upper" in values) or (open_ended and values)
    ]


def _comparison_parts(
    comparison: object,
    side: str,
) -> tuple[str | None, bool | int | float | str | None, str]:
    from sqlglot import exp

    if not isinstance(comparison, exp.Expression):
        return None, None, side
    left = comparison.this
    right = comparison.expression
    if isinstance(left, exp.Column):
        return left.name, _literal_value(right), side
    if isinstance(right, exp.Column):
        reverse_side = "upper" if side == "lower" else "lower"
        return right.name, _literal_value(left), reverse_side
    return None, None, side


def _literal_value(expression: object) -> bool | int | float | str | None:
    from sqlglot import exp

    while isinstance(expression, exp.Cast | exp.Paren):
        expression = expression.this
    if isinstance(expression, exp.Boolean):
        return bool(expression.this)
    if not isinstance(expression, exp.Literal):
        return None
    if expression.is_string:
        return str(expression.this)
    raw = str(expression.this)
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def _parse_with_sqlglot(sql: str) -> PredicateInfo | None:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return None

    info = PredicateInfo()
    quiet_sqlglot_logger()  # never let sqlglot log the statement text (#165)
    try:
        parsed = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return None

    if isinstance(parsed, exp.Select):
        info.query_type = "SELECT"
    elif isinstance(parsed, exp.Insert):
        info.query_type = "INSERT"
    elif isinstance(parsed, exp.Update):
        info.query_type = "UPDATE"
    elif isinstance(parsed, exp.Delete):
        info.query_type = "DELETE"
    else:
        info.query_type = "OTHER"

    for table in parsed.find_all(exp.Table):
        name = table.name
        schema = table.db or table.catalog
        if schema and name:
            info.tables_referenced.append(f"{schema}.{name}")
        elif name:
            info.tables_referenced.append(name)

    where = parsed.find(exp.Where)
    if where is not None:
        _extract_sqlglot_filter_columns(where, info)
    return info


# sqlglot predicate node -> (operator token, is_equality, is_range). Anything absent from this
# map is a predicate whose column cannot be turned into a min/max skipping bound, so it is
# recorded with operator=None and neither flag set (see _extract_sqlglot_filter_columns).
_SQLGLOT_PREDICATE_OPERATORS: list[tuple[str, str, bool, bool]] = [
    ("EQ", "=", True, False),
    ("NEQ", "<>", True, False),
    ("NullSafeEQ", "<=>", True, False),
    ("NullSafeNEQ", "<>", True, False),
    ("In", "IN", True, False),
    ("GT", ">", False, True),
    ("GTE", ">=", False, True),
    ("LT", "<", False, True),
    ("LTE", "<=", False, True),
    ("Between", "BETWEEN", False, True),
    ("Like", "LIKE", False, False),
    ("ILike", "ILIKE", False, False),
    ("Is", "IS", False, False),
]


def _extract_sqlglot_filter_columns(where: Any, info: PredicateInfo) -> None:
    """Record each WHERE column with the operator it was actually compared under.

    Regression, 2026-09-04: this path previously stamped ``is_equality=True`` on every column
    it found under the WHERE clause and never set ``operator``/``is_range``, so a range scan
    (``ts > x``) was indistinguishable from a point lookup, and the regex fallback in
    ``_parse_with_regex`` -- which does classify operators -- was strictly more informative
    than the "better" parser that shadows it. Clustering candidate quality depends on the
    distinction: an unselective range predicate and a selective equality predicate imply very
    different layouts.

    A column under a predicate this function cannot classify (a UDF call, a subquery, an
    arithmetic expression) is still recorded, but with ``operator=None`` and both flags false,
    so downstream consumers can measure parser coverage rather than silently treating it as an
    equality filter.
    """
    from sqlglot import exp

    seen: set[int] = set()

    def record(column: object, operator: str | None, is_equality: bool, is_range: bool) -> None:
        if not isinstance(column, exp.Column) or not column.name:
            return
        # A column can appear under nested predicates (``a > 1 AND a < 9``); each occurrence is
        # its own observation, but the same node must not be counted twice.
        if id(column) in seen:
            return
        seen.add(id(column))
        info.filter_columns.append(
            ColumnReference(
                column=column.name,
                table_alias=column.table,
                operator=operator,
                is_equality=is_equality,
                is_range=is_range,
            )
        )

    for node_name, operator, is_equality, is_range in _SQLGLOT_PREDICATE_OPERATORS:
        node_type = getattr(exp, node_name, None)
        if node_type is None:  # sqlglot version without this node
            continue
        for predicate in where.find_all(node_type):
            # Only the compared column earns the operator. ``a = b`` records both sides; a
            # literal or function operand simply is not a Column and is skipped by record().
            record(predicate.this, operator, is_equality, is_range)
            record(predicate.args.get("expression"), operator, is_equality, is_range)

    # Columns under predicates none of the above matched (UDFs, subqueries, arithmetic).
    for column in where.find_all(exp.Column):
        record(column, None, False, False)


def _parse_with_regex(sql: str) -> PredicateInfo:
    info = PredicateInfo()

    # Determine query type
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("SELECT"):
        info.query_type = "SELECT"
    elif sql_upper.startswith("INSERT"):
        info.query_type = "INSERT"
    elif sql_upper.startswith("UPDATE"):
        info.query_type = "UPDATE"
    elif sql_upper.startswith("DELETE"):
        info.query_type = "DELETE"
    else:
        info.query_type = "OTHER"

    # Extract tables referenced
    for match in _TABLE_REF_PATTERN.finditer(sql):
        table_name = match.group(1).strip("\"'")
        if table_name.lower() not in ("select", "from", "where", "join"):
            info.tables_referenced.append(table_name)

    # Extract SELECT columns (projections)
    select_match = _SELECT_PATTERN.search(sql)
    if select_match:
        select_clause = select_match.group(1)
        if "*" not in select_clause:
            # Parse individual columns
            for col in select_clause.split(","):
                col = col.strip()
                # Remove AS alias
                if " AS " in col.upper():
                    col = col.split()[0]
                # Remove table prefix for storage
                if "." in col:
                    col = col.split(".")[-1]
                col = col.strip("`\"'")
                if col and col.upper() not in ("DISTINCT", "ALL"):
                    info.projection_columns.append(col)

    # Extract WHERE predicates
    where_match = _WHERE_PATTERN.search(sql)
    if where_match:
        where_clause = where_match.group(1)
        _extract_filter_columns(where_clause, info)

    # Extract JOIN columns
    for match in _JOIN_ON_PATTERN.finditer(sql):
        left_col = match.group(1)
        right_col = match.group(2)
        # Extract just the column names
        if "." in left_col:
            left_col = left_col.split(".")[-1]
        if "." in right_col:
            right_col = right_col.split(".")[-1]
        if left_col not in info.join_columns:
            info.join_columns.append(left_col)
        if right_col not in info.join_columns:
            info.join_columns.append(right_col)

    return info


def _extract_filter_columns(where_clause: str, info: PredicateInfo) -> None:
    """Extract filter columns from a WHERE clause."""
    # Find column = value, column > value, etc.
    for match in _COLUMN_PATTERN.finditer(where_clause):
        col_ref = match.group(1)
        operator = match.group(2).upper()

        # Extract just the column name
        column = col_ref.split(".")[-1] if "." in col_ref else col_ref
        table_alias = col_ref.split(".")[0] if "." in col_ref else None

        is_equality = operator in _EQUALITY_OPS
        is_range = operator in _RANGE_OPS

        ref = ColumnReference(
            column=column,
            table_alias=table_alias,
            operator=operator,
            is_equality=is_equality,
            is_range=is_range,
        )

        # Avoid duplicates
        existing = [c.column for c in info.filter_columns]
        if column not in existing:
            info.filter_columns.append(ref)


def extract_table_from_query(sql: str, catalog: str | None = None) -> tuple[str | None, str | None]:
    """Extract schema and table name from a query.

    Args:
        sql: The SQL query
        catalog: Optional catalog prefix to strip

    Returns:
        Tuple of (schema_name, table_name) or (None, None) if not found
    """
    for match in _TABLE_REF_PATTERN.finditer(sql):
        table_ref = match.group(1).strip("\"'")

        # Skip common keywords
        if table_ref.lower() in ("select", "from", "where", "join"):
            continue

        parts = table_ref.split(".")

        # Handle different formats: catalog.schema.table, schema.table, table
        if len(parts) == 3:
            # catalog.schema.table
            if catalog and parts[0].lower() == catalog.lower():
                return parts[1], parts[2]
            return parts[1], parts[2]
        elif len(parts) == 2:
            # schema.table
            return parts[0], parts[1]
        elif len(parts) == 1:
            # Just table name, no schema
            return None, parts[0]

    return None, None
