"""Bounded row-sample fetching for Stage 2 MinHash scoring (docs/TABLE_DEDUPLICATION_DESIGN.md
SS4.4).

At ~450TB fleet-wide, hashing every row of every candidate table is not affordable --
this module pulls a size-bounded sample per table via Trino instead. Sampling error in the
resulting Jaccard estimate scales O(1/sqrt(sample_size)): a few hundred thousand rows per
side is enough to separate true duplicates (sample-match near 95-100%) from coincidentally
similar tables (near 0%, since unrelated fact tables essentially never produce identical
row hashes by chance) -- see the design doc for the full reasoning.

Uses Trino's TABLESAMPLE SYSTEM, which samples at the SPLIT/file granularity (cheap --
Trino skips whole splits rather than reading every row and rejecting most of them), matching
the design doc's guidance to sample via whole-file/whole-row-group selection rather than
true random-row sampling, which Parquet makes expensive.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryo.db.model_utils import catalog_name_for_table
from cryo.db.models import Table
from cryo.engines.trino.client import connect, coordinators_from_config
from cryo.engines.trino.sql import quote_identifier, quote_qualified_name

# Bounded per the design doc SS4.4's "a few hundred thousand rows per side" guidance --
# high enough for the O(1/sqrt(n)) Jaccard estimation error to be small, low enough that
# pulling + hashing a sample for a Stage-2 candidate pair stays cheap even for the fleet's
# largest tables (e.g. the 24TB unified_bq.gold_dbt_pw_bq).
DEFAULT_SAMPLE_ROW_LIMIT = 200_000
# TABLESAMPLE percentage is a request, not a guarantee -- Trino may return more or fewer
# rows depending on split sizes. Combined with the hard LIMIT below, this bounds actual
# query cost regardless of table size: a 24TB table samples the same wall-clock-bounded
# amount of data as a 24GB one.
_SAMPLE_PERCENT = 5.0


@dataclass(frozen=True, slots=True)
class RowSample:
    table_id: str
    row_hashes: list[int]
    sampled_row_count: int
    columns_used: tuple[str, ...]


def _hash_row(values: tuple[object, ...]) -> int:
    """Deterministic 64-bit hash of a row's concatenated column values, stable across
    processes (see minhash.py's _stable_hash docstring for why Python's built-in hash()
    is unsuitable here -- it's salted per-process by default)."""
    parts = "\x1f".join("\x00" if v is None else str(v) for v in values)
    digest = hashlib.blake2b(parts.encode("utf-8", errors="replace"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def fetch_row_sample(
    table: Table,
    *,
    trino_config: dict,
    columns: list[str] | None = None,
    row_limit: int = DEFAULT_SAMPLE_ROW_LIMIT,
) -> RowSample:
    """Pull a bounded, deterministic-enough row sample for MinHash signature computation.

    ``columns`` restricts sampling to a specific column subset (e.g. only the columns two
    candidate tables have in common, from their schema-fingerprint intersection) -- pass
    None to sample all columns, which is the right choice when the two tables being
    compared have identical schemas (the common Stage-1-blocked case) but wasteful when
    only a handful of columns overlap.

    Raises whatever the underlying Trino client raises on connection/query failure --
    callers (dedup.pipeline) are expected to catch and record a Stage-2 scoring failure
    for this pair rather than let one bad table abort the whole batch, matching
    tiering.s3_analyzer's per-table try/except pattern in get_all_tiering_recommendations.
    """
    qualified_name = quote_qualified_name(
        catalog_name_for_table(table), table.schema_name, table.table_name
    )
    column_list = ", ".join(quote_identifier(c) for c in columns) if columns else "*"
    row_limit = int(row_limit)

    coordinators = coordinators_from_config(trino_config)
    conn = connect(coordinators[0])
    cur = conn.cursor()
    cur.execute(
        f"SELECT {column_list} FROM {qualified_name} "
        f"TABLESAMPLE SYSTEM ({_SAMPLE_PERCENT}) LIMIT {row_limit}"
    )
    rows = cur.fetchall()
    resolved_columns = tuple(columns) if columns else tuple(d[0] for d in (cur.description or []))

    row_hashes = list({_hash_row(tuple(row)) for row in rows})
    return RowSample(
        table_id=str(table.id),
        row_hashes=row_hashes,
        sampled_row_count=len(row_hashes),
        columns_used=resolved_columns,
    )
