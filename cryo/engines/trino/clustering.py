"""Linear-sort clustering on Trino.

Trino is Cryo's first-class engine, and until now it could not cluster at all: every sort or
curve request was routed to Spark, and ``ClusteringPlan.to_trino_optimize`` returned ``None``
for anything but binpack. That is a real gap for the common deployment where Trino is the only
engine an operator runs.

Trino's Iceberg connector cannot express a *curve* -- there is no ``zorder()``/``hilbert()``
sort-order function, and ``ALTER TABLE ... EXECUTE optimize`` takes no sort argument. But it
does support the table's own **`sorted_by` property**: writes honour it, and a subsequent
``OPTIMIZE`` rewrites files in that order. So linear multi-column sort is reachable in two
statements:

    ALTER TABLE t SET PROPERTIES sorted_by = ARRAY['a ASC NULLS FIRST', 'b DESC']
    ALTER TABLE t EXECUTE optimize(file_size_threshold => '256MB')

That covers the case the advisor most often recommends. Research note §2.3 and the Phase 2
simulator agree that a workload with one dominant predicate is best served by a plain sort --
curves are for genuinely multidimensional filtering, and even then the file-pruning gain over a
well-chosen sort is often modest.

What Trino still cannot do is Z-order or Hilbert. ``clustering_support`` reports that honestly
so callers route those to Spark rather than silently emitting SQL that sorts on the wrong thing:
degrading a curve request to a lexicographic sort would change what the layout optimizes for
without telling anyone, which is worse than refusing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)

# Trino's Iceberg connector accepts these per-column suffixes in `sorted_by`.
_VALID_DIRECTIONS = ("ASC", "DESC")
_VALID_NULL_ORDERS = ("NULLS FIRST", "NULLS LAST")


class TrinoClusteringSupport(StrEnum):
    """Whether Trino can express a given clustering strategy."""

    SUPPORTED = "supported"
    UNSUPPORTED_CURVE = "unsupported_curve"
    UNSUPPORTED_STRATEGY = "unsupported_strategy"


@dataclass(frozen=True)
class TrinoSortColumn:
    """One column of a Trino `sorted_by` specification."""

    column: str
    descending: bool = False
    nulls_first: bool | None = None

    def render(self) -> str:
        """Render as Trino expects inside the `sorted_by` array."""
        parts = [_quote_sort_identifier(self.column), "DESC" if self.descending else "ASC"]
        if self.nulls_first is not None:
            parts.append("NULLS FIRST" if self.nulls_first else "NULLS LAST")
        return " ".join(parts)


def _quote_sort_identifier(column: str) -> str:
    """Quote a column name for use inside a `sorted_by` string element.

    These are single-quoted SQL strings, so an embedded double quote is what needs escaping,
    and a literal single quote must be doubled by the caller's string escaping. Trino accepts
    an unquoted identifier here; quoting keeps reserved words and mixed case working.
    """
    escaped = column.replace('"', '""')
    return f'"{escaped}"'


def clustering_support(strategy: str) -> TrinoClusteringSupport:
    """Report whether Trino can express ``strategy``.

    Curves are refused rather than degraded: emitting a lexicographic sort for a Z-order
    request would silently change what the layout is optimized for.
    """
    if strategy == "sort":
        return TrinoClusteringSupport.SUPPORTED
    if strategy in ("zorder", "hilbert"):
        return TrinoClusteringSupport.UNSUPPORTED_CURVE
    return TrinoClusteringSupport.UNSUPPORTED_STRATEGY


def render_sort_clustering(
    table_ref: str,
    columns: list[TrinoSortColumn],
    *,
    file_size_mb: int = 256,
    partition_filter: str | None = None,
) -> list[str]:
    """Render the two statements that apply a linear sort order on Trino.

    Returns ``[set_properties, optimize]``. The property must be set *before* the rewrite:
    ``OPTIMIZE`` reads the table's persisted sort order, exactly as Iceberg's Spark procedure
    does for a linear sort (see the 2026-08-11 regression note in ``engines/spark/sql.py`` --
    the same trap, on the other engine).

    Setting the property also makes *future* writes sorted, so the layout holds as the table
    grows instead of decaying until the next maintenance run.
    """
    if not columns:
        raise ValueError("render_sort_clustering requires at least one sort column")

    rendered = ", ".join(f"'{column.render()}'" for column in columns)
    set_properties = f"ALTER TABLE {table_ref} SET PROPERTIES sorted_by = ARRAY[{rendered}]"

    optimize = (
        f"ALTER TABLE {table_ref} EXECUTE optimize(file_size_threshold => '{file_size_mb}MB')"
    )
    if partition_filter:
        optimize = f"{optimize} WHERE {partition_filter}"

    return [set_properties, optimize]


def sort_columns_from_names(
    names: list[str],
    directions: list[str] | None = None,
) -> list[TrinoSortColumn]:
    """Build sort columns from the advisor's plain column list.

    Directions default to ascending, which is what a clustering recommendation implies: the
    advisor picks *which* columns carry pruning value, not which end of the range matters.
    """
    directions = directions or []
    columns: list[TrinoSortColumn] = []
    for index, name in enumerate(names):
        direction = (directions[index] if index < len(directions) else "asc").strip().lower()
        columns.append(TrinoSortColumn(column=name, descending=direction.startswith("desc")))
    return columns
