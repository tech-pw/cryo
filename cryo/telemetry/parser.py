"""SQL query parser to extract predicates, joins, and projections.

Uses regex-based parsing for common SQL patterns. For production use,
consider integrating with sqlglot or a proper SQL parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


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


def _parse_with_sqlglot(sql: str) -> PredicateInfo | None:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return None

    info = PredicateInfo()
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
        for col in where.find_all(exp.Column):
            col_name = col.name
            if not col_name:
                continue
            info.filter_columns.append(
                ColumnReference(column=col_name, table_alias=col.table, is_equality=True)
            )
    return info


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
