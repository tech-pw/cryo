"""Data clustering operations (sort, z-order) for Iceberg tables."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

logger = logging.getLogger(__name__)


class SortDirection(StrEnum):
    """Sort direction for column ordering."""

    ASC = "asc"
    DESC = "desc"
    ASC_NULLS_FIRST = "asc_nulls_first"
    ASC_NULLS_LAST = "asc_nulls_last"
    DESC_NULLS_FIRST = "desc_nulls_first"
    DESC_NULLS_LAST = "desc_nulls_last"


@dataclass
class SortOrder:
    """Sort order specification for a column."""

    column: str
    direction: SortDirection = SortDirection.ASC
    null_order: Literal["first", "last"] | None = None

    def to_spark_sql(self) -> str:
        """Generate Spark SQL sort expression."""
        result = f"`{self.column}`"
        if self.direction in (
            SortDirection.DESC,
            SortDirection.DESC_NULLS_FIRST,
            SortDirection.DESC_NULLS_LAST,
        ):
            result += " DESC"
        else:
            result += " ASC"

        if self.null_order == "first" or self.direction in (
            SortDirection.ASC_NULLS_FIRST,
            SortDirection.DESC_NULLS_FIRST,
        ):
            result += " NULLS FIRST"
        elif self.null_order == "last" or self.direction in (
            SortDirection.ASC_NULLS_LAST,
            SortDirection.DESC_NULLS_LAST,
        ):
            result += " NULLS LAST"

        return result

    def to_iceberg_sort_field(self) -> dict[str, Any]:
        """Generate Iceberg sort field specification."""
        return {
            "source-id": None,  # Will be resolved by Iceberg
            "transform": "identity",
            "direction": "desc" if "desc" in self.direction.value else "asc",
            "null-order": "nulls-first" if "first" in (self.null_order or "") else "nulls-last",
        }


@dataclass
class ZOrderSpec:
    """Z-order clustering specification."""

    columns: list[str]
    interleave_depth: int = 8  # Bits to interleave per column

    def to_spark_sql(self) -> str:
        """Generate Spark SQL z-order expression for rewrite_data_files."""
        cols = ", ".join(f"`{c}`" for c in self.columns)
        return f"zorder({cols})"


@dataclass
class ClusteringPlan:
    """Plan for clustering/sorting a table's data."""

    table_ref: str  # catalog.schema.table
    strategy: Literal["sort", "zorder", "binpack"]

    # For sort strategy
    sort_order: list[SortOrder] = field(default_factory=list)

    # For z-order strategy
    zorder_spec: ZOrderSpec | None = None

    # Execution options
    target_file_size_bytes: int = 256 * 1024 * 1024  # 256 MB default
    min_file_size_bytes: int = 64 * 1024 * 1024  # 64 MB min
    max_file_size_bytes: int = 512 * 1024 * 1024  # 512 MB max
    partial_progress_enabled: bool = True
    max_concurrent_file_group_rewrites: int = 5
    rewrite_all: bool = False  # If True, rewrite all files; if False, only poorly clustered

    # Filter (optional - for partition-scoped clustering)
    partition_filter: str | None = None

    def to_spark_rewrite_call(self) -> str:
        """Generate Spark CALL statement for rewrite_data_files."""
        options = [
            f"'target-file-size-bytes', '{self.target_file_size_bytes}'",
            f"'min-file-size-bytes', '{self.min_file_size_bytes}'",
            f"'max-file-size-bytes', '{self.max_file_size_bytes}'",
            f"'partial-progress.enabled', '{str(self.partial_progress_enabled).lower()}'",
            f"'max-concurrent-file-group-rewrites', '{self.max_concurrent_file_group_rewrites}'",
        ]

        if self.rewrite_all:
            options.append("'rewrite-all', 'true'")

        if self.strategy == "zorder" and self.zorder_spec:
            sort_expr = self.zorder_spec.to_spark_sql()
            options.append(f"'sort-order', '{sort_expr}'")
        elif self.strategy == "sort" and self.sort_order:
            sort_expr = ", ".join(s.to_spark_sql() for s in self.sort_order)
            options.append(f"'sort-order', '{sort_expr}'")

        options_str = ", ".join(options)

        call = f"""CALL system.rewrite_data_files(
  table => '{self.table_ref}',
  strategy => '{self.strategy}',
  options => map({options_str})"""

        if self.partition_filter:
            call += f",\n  where => '{self.partition_filter}'"

        call += "\n)"
        return call

    def to_spark_python(self) -> str:
        """Generate PySpark code for rewrite_data_files using Actions API."""
        code_lines = [
            "from pyspark.sql import SparkSession",
            "from org.apache.iceberg.spark.actions import SparkActions",
            "",
            f"table = spark.table('{self.table_ref}')",
            "actions = SparkActions.get()",
            "rewrite = actions.rewriteDataFiles(table)",
        ]

        # Set options
        code_lines.append(
            f"rewrite = rewrite.option('target-file-size-bytes', {self.target_file_size_bytes})"
        )
        code_lines.append(
            f"rewrite = rewrite.option('min-file-size-bytes', {self.min_file_size_bytes})"
        )
        code_lines.append(
            "rewrite = rewrite.option("
            f"'partial-progress.enabled', {str(self.partial_progress_enabled).lower()})"
        )

        # Set sort order
        if self.strategy == "zorder" and self.zorder_spec:
            cols = ", ".join(f'"{c}"' for c in self.zorder_spec.columns)
            code_lines.append(f"rewrite = rewrite.zOrder({cols})")
        elif self.strategy == "sort" and self.sort_order:
            for sort in self.sort_order:
                direction = (
                    "SortDirection.DESC" if "desc" in sort.direction.value else "SortDirection.ASC"
                )
                code_lines.append(f'rewrite = rewrite.sort("{sort.column}", {direction})')

        # Filter
        if self.partition_filter:
            code_lines.append(f"rewrite = rewrite.filter('{self.partition_filter}')")

        code_lines.append("")
        code_lines.append("result = rewrite.execute()")
        code_lines.append("print(f'Rewritten {result.rewrittenDataFilesCount()} files')")

        return "\n".join(code_lines)

    def to_trino_optimize(self) -> str | None:
        """Generate Trino EXECUTE optimize statement.

        Note: Trino's OPTIMIZE doesn't support custom sort order directly.
        It uses the table's sort_order property which must be set separately.
        """
        if self.strategy != "binpack":
            # Trino OPTIMIZE only does binpack, not sort rewrite
            logger.warning(
                f"Trino OPTIMIZE doesn't support {self.strategy} strategy. "
                "Use Spark for sort/z-order operations."
            )
            return None

        sql = f"ALTER TABLE {self.table_ref} EXECUTE optimize"

        if self.partition_filter:
            sql += f" WHERE {self.partition_filter}"

        return sql


def build_clustering_plan(
    table_ref: str,
    strategy: Literal["sort", "zorder", "binpack"],
    columns: list[str] | None = None,
    **options: Any,
) -> ClusteringPlan:
    """Build a clustering plan for a table.

    Args:
        table_ref: Full table reference (catalog.schema.table)
        strategy: Clustering strategy
        columns: Columns to sort/cluster by
        **options: Additional options (target_file_size_bytes, etc.)

    Returns:
        ClusteringPlan ready for execution
    """
    plan = ClusteringPlan(
        table_ref=table_ref,
        strategy=strategy,
        target_file_size_bytes=options.get("target_file_size_bytes", 256 * 1024 * 1024),
        min_file_size_bytes=options.get("min_file_size_bytes", 64 * 1024 * 1024),
        max_file_size_bytes=options.get("max_file_size_bytes", 512 * 1024 * 1024),
        partial_progress_enabled=options.get("partial_progress_enabled", True),
        max_concurrent_file_group_rewrites=options.get("max_concurrent_file_group_rewrites", 5),
        rewrite_all=options.get("rewrite_all", False),
        partition_filter=options.get("partition_filter"),
    )

    if columns:
        if strategy == "zorder":
            plan.zorder_spec = ZOrderSpec(columns=columns)
        elif strategy == "sort":
            directions = options.get("directions", [SortDirection.ASC] * len(columns))
            plan.sort_order = [
                SortOrder(column=col, direction=dir)
                for col, dir in zip(columns, directions, strict=False)
            ]

    return plan
