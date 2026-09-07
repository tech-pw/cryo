"""Linear-sort clustering on Trino.

Closes the gap where Cryo's first-class engine could not cluster at all. Trino has no
zorder()/hilbert() sort-order function, so curves are refused rather than degraded to a
lexicographic sort -- silently changing what a layout optimizes for is worse than declining.
"""

from __future__ import annotations

import pytest

from cryo.engines.trino.clustering import (
    TrinoClusteringSupport,
    TrinoSortColumn,
    clustering_support,
    render_sort_clustering,
    sort_columns_from_names,
)
from cryo.optimizer.clustering import ClusteringPlan, SortDirection, SortOrder, ZOrderSpec


def test_sort_is_supported_curves_are_not() -> None:
    assert clustering_support("sort") is TrinoClusteringSupport.SUPPORTED
    assert clustering_support("zorder") is TrinoClusteringSupport.UNSUPPORTED_CURVE
    assert clustering_support("hilbert") is TrinoClusteringSupport.UNSUPPORTED_CURVE
    assert clustering_support("binpack") is TrinoClusteringSupport.UNSUPPORTED_STRATEGY


def test_sort_clustering_sets_the_property_before_optimizing() -> None:
    """OPTIMIZE reads the table's persisted sort order, so ordering of the two matters."""
    statements = render_sort_clustering("ice.db.t", sort_columns_from_names(["user_id", "ts"]))
    assert len(statements) == 2
    assert statements[0].startswith("ALTER TABLE ice.db.t SET PROPERTIES sorted_by")
    assert "EXECUTE optimize" in statements[1]


def test_sort_directions_are_rendered() -> None:
    statements = render_sort_clustering(
        "ice.db.t", sort_columns_from_names(["a", "b"], ["asc", "desc"])
    )
    assert "'\"a\" ASC'" in statements[0]
    assert "'\"b\" DESC'" in statements[0]


def test_null_order_is_rendered_only_when_specified() -> None:
    with_nulls = render_sort_clustering("ice.db.t", [TrinoSortColumn("a", nulls_first=True)])[0]
    assert "NULLS FIRST" in with_nulls
    without = render_sort_clustering("ice.db.t", [TrinoSortColumn("a")])[0]
    assert "NULLS" not in without


def test_identifiers_are_quoted_so_reserved_words_survive() -> None:
    statement = render_sort_clustering("ice.db.t", sort_columns_from_names(["order"]))[0]
    assert '"order"' in statement


def test_embedded_double_quote_is_escaped() -> None:
    statement = render_sort_clustering("ice.db.t", sort_columns_from_names(['we"ird']))[0]
    assert 'we""ird' in statement


def test_partition_filter_lands_on_the_optimize_not_the_property() -> None:
    """A WHERE clause scopes the rewrite; the sort order is a whole-table property."""
    statements = render_sort_clustering(
        "ice.db.t", sort_columns_from_names(["a"]), partition_filter="day = DATE '2026-01-01'"
    )
    assert "WHERE" not in statements[0]
    assert "WHERE day = DATE '2026-01-01'" in statements[1]


def test_file_size_threshold_is_configurable() -> None:
    statements = render_sort_clustering(
        "ice.db.t", sort_columns_from_names(["a"]), file_size_mb=512
    )
    assert "'512MB'" in statements[1]


def test_empty_column_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one sort column"):
        render_sort_clustering("ice.db.t", [])


# --- ClusteringPlan integration ---------------------------------------------------------


def test_plan_renders_trino_sort_as_two_statements() -> None:
    plan = ClusteringPlan(
        table_ref="ice.db.t",
        strategy="sort",
        sort_order=[SortOrder("user_id"), SortOrder("ts", SortDirection.DESC)],
    )
    sql = plan.to_trino_optimize()
    assert sql is not None
    assert sql.count(";\n") == 1  # executor splits on this
    assert "sorted_by" in sql
    assert '"ts" DESC' in sql


def test_plan_still_renders_plain_binpack() -> None:
    sql = ClusteringPlan(table_ref="ice.db.t", strategy="binpack").to_trino_optimize()
    assert sql == "ALTER TABLE ice.db.t EXECUTE optimize"


def test_plan_refuses_curves_on_trino() -> None:
    """Regression guard: a curve must not silently become a lexicographic sort."""
    plan = ClusteringPlan(
        table_ref="ice.db.t", strategy="zorder", zorder_spec=ZOrderSpec(["a", "b"])
    )
    assert plan.to_trino_optimize() is None


def test_plan_refuses_sort_without_columns() -> None:
    assert ClusteringPlan(table_ref="ice.db.t", strategy="sort").to_trino_optimize() is None


def test_plan_passes_target_file_size_through() -> None:
    plan = ClusteringPlan(
        table_ref="ice.db.t",
        strategy="sort",
        sort_order=[SortOrder("a")],
        target_file_size_bytes=512 * 1024 * 1024,
    )
    sql = plan.to_trino_optimize()
    assert sql is not None and "'512MB'" in sql
