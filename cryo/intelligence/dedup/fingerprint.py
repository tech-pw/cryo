"""Stage 1: cheap, metadata-only candidate generation (docs/TABLE_DEDUPLICATION_DESIGN.md
SS3, SS10).

Everything here reads only already-populated Cryo metadata (TableColumn, TableStats) --
no Trino query, no data read. This is deliberately the cheapest possible stage, meant to
run across the whole fleet (~13,225 tables) and cut the O(n^2) pair space down by several
orders of magnitude before Stage 2's statistical scoring (dedup.scoring) or Stage 3's
exact Trino verification (dedup.verification) ever touch a candidate pair.

Blocking strategy follows R2D2 (arXiv:2312.13427, SIGMOD 2024, see design doc SS5): bucket
by schema fingerprint first (exact schema match is the strongest free filter), then within
a bucket only pair tables whose row counts/byte sizes fall within a WIDE tolerance band --
wide, not tight, because a tight near-equal-size requirement would wrongly exclude genuine
containment pairs (design doc SS9: orders_us subset-of orders_global), which is exactly the
kind of pair R2D2's min-max pruning invariant (row_count(subset) <= row_count(superset)) is
designed to still catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Table, TableColumn, TableStats

# Tightened from an original 0.02 after a real production run, 2026-08-13: this fleet has
# a single schema bucket of 1,667 tables (spanning many unrelated source products --
# pw_bq, curiousjr_bq, xylem_bq, ...) that share a generic BigQuery-ingestion schema shape
# by coincidence, not because they're related. At 0.02 that one bucket alone produced
# 1.39M of 1.41M total candidate pairs -- schema-identical does not mean related when
# hundreds of genuinely unrelated tables share a common ingestion format, which the design
# doc's SS9 false-positive section names directly ("coincidental schema similarity,
# unrelated data") but the original threshold badly underestimated for this fleet's shape.
# 0.9 still leaves 51,511 candidates from that one bucket alone (verified by direct
# simulation against real fleet data) -- tables clustered near similar small sizes still
# pass a strict ratio -- so this alone doesn't fully solve the problem; naming_pattern_hint
# and Stage 2's _MIN_METADATA_SCORE_FOR_SAMPLING gate (cryo.intelligence.dedup.pipeline)
# are what keep Stage 2's real Trino sampling cost bounded on top of this.
_MIN_SIZE_RATIO = 0.9
# Naming-convention patterns from the real fleet scan (docs/TABLE_DEDUPLICATION_DESIGN.md
# SS2/SS8) -- used here only as an ADDITIONAL blocking relaxation (a pair matching a naming
# pattern is included even outside the size-ratio band, since e.g. a `_temp` table is often
# much smaller than its parent by design, not because it's unrelated) and later as one
# input to Stage 2's composite score. Never a standalone accept/reject signal.
_NAMING_PATTERN_SUFFIXES: tuple[str, ...] = (
    "_backup",
    "_bak",
    "_temp",
    "_tmp",
    "_old",
    "_orig",
    "_copy",
    "_dup",
    "_test",
    "_staging",
    "_wip",
    "_dedup",
    "_dtmp",
)


@dataclass(frozen=True, slots=True)
class SchemaFingerprint:
    table_id: str
    schema_name: str
    table_name: str
    # Order-insensitive: {(name, type), ...} -- catches column reordering.
    unordered_columns: frozenset[tuple[str, str]]
    # Order-sensitive: (name, type), (name, type), ... -- a CTAS-produced backup almost
    # always preserves exact column order; a hand-rebuilt "similar" table often doesn't,
    # which is itself a discriminating signal (design doc SS3.1).
    ordered_columns: tuple[tuple[str, str], ...]
    total_size_gb: float
    total_records: int | None
    total_files: int

    @property
    def schema_bucket_key(self) -> frozenset[tuple[str, str]]:
        """Exact-match bucketing key -- tables with identical column (name, type) sets
        land in the same bucket regardless of column order."""
        return self.unordered_columns


def build_schema_fingerprints(session: Session) -> list[SchemaFingerprint]:
    """Build one fingerprint per active table with both stats and columns populated.

    Tables missing TableStats (never successfully scanned) or with zero TableColumn rows
    are skipped -- there is nothing to fingerprint yet, and including them would only
    produce false "identical empty schema" bucket collisions.

    Selects specific columns (not full ORM entities via select(Table)/select(TableStats)/
    select(TableColumn)) deliberately -- confirmed live, 2026-08-13: against this fleet's
    real 654,590 TableColumn rows (13,725 active tables), loading all three as full ORM
    objects into three separate in-memory dicts simultaneously OOM-killed the API pod
    (6Gi limit) running this function. Column-selected tuples skip SQLAlchemy's identity
    map and instrumentation overhead per row, which is where the memory actually went --
    ORM object overhead, not the data itself (654K rows of two short strings each is a
    few tens of MB of raw data).
    """
    table_rows = session.execute(
        select(Table.id, Table.schema_name, Table.table_name).where(Table.is_active.is_(True))
    ).all()

    stats_by_table: dict[Any, tuple[float, int | None, int]] = {
        table_id: (total_size_gb, total_records, total_files)
        for table_id, total_size_gb, total_records, total_files in session.execute(
            select(
                TableStats.table_id,
                TableStats.total_size_gb,
                TableStats.total_records,
                TableStats.total_files,
            )
        ).all()
    }

    columns_by_table: dict[str, list[tuple[str, str]]] = {}
    for table_id, column_name, data_type in session.execute(
        select(TableColumn.table_id, TableColumn.column_name, TableColumn.data_type)
    ).yield_per(5000):
        columns_by_table.setdefault(str(table_id), []).append((column_name, data_type))

    fingerprints: list[SchemaFingerprint] = []
    for table_id, schema_name, table_name in table_rows:
        stats = stats_by_table.get(table_id)
        columns = columns_by_table.get(str(table_id))
        if stats is None or not columns:
            continue
        total_size_gb, total_records, total_files = stats
        # TableColumn has no explicit ordinal position column -- fall back to name order,
        # which loses true DDL column order but keeps the ordered-tuple comparison at
        # least deterministic and comparable across two tables' fingerprints. Noted as a
        # known gap: a real ordinal_position column would make SS3.1's order-sensitive
        # signal meaningfully stronger (open question, design doc SS11).
        ordered = tuple(sorted(columns))
        fingerprints.append(
            SchemaFingerprint(
                table_id=str(table_id),
                schema_name=schema_name,
                table_name=table_name,
                unordered_columns=frozenset(ordered),
                ordered_columns=ordered,
                total_size_gb=total_size_gb,
                total_records=total_records,
                total_files=total_files,
            )
        )
    return fingerprints


def _naming_pattern_hint(a: SchemaFingerprint, b: SchemaFingerprint) -> bool:
    """True if, within the same schema, one table's name is the other's with a known
    backup/temp/copy/etc suffix stripped -- the real parent-copy pair shape confirmed
    live against this fleet (design doc SS2)."""
    if a.schema_name != b.schema_name:
        return False
    names = {a.table_name, b.table_name}
    for name in list(names):
        for suffix in _NAMING_PATTERN_SUFFIXES:
            if name.endswith(suffix) and name[: -len(suffix)] in names - {name}:
                return True
            # versioned suffix (_v1, _v2, ...) needs a numeric check, not a fixed suffix
        stripped = name
        for token_start in ("_v",):
            idx = name.rfind(token_start)
            if idx > 0 and name[idx + len(token_start) :].isdigit():
                stripped = name[:idx]
                if stripped in names - {name}:
                    return True
    return False


def _sorted_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _size_ratio_ok(a: SchemaFingerprint, b: SchemaFingerprint) -> bool:
    small, large = sorted([a.total_size_gb, b.total_size_gb])
    if large <= 0:
        return small == large  # both effectively empty tables
    return (small / large) >= _MIN_SIZE_RATIO


@dataclass(frozen=True, slots=True)
class CandidatePair:
    table_a: SchemaFingerprint
    table_b: SchemaFingerprint
    schema_exact_match: bool
    naming_pattern_hint: bool


@dataclass(slots=True)
class BlockingResult:
    candidates: list[CandidatePair] = field(default_factory=list)
    total_tables_considered: int = 0
    total_pairs_possible: int = 0


def generate_candidate_pairs(fingerprints: list[SchemaFingerprint]) -> BlockingResult:
    """Stage 1 blocking: bucket by exact schema match, then filter by size-ratio band or
    naming-pattern hint. Returns far fewer pairs than the full n^2 cross product -- see
    module docstring for why (design doc SS10 expects 2-4 orders of magnitude reduction
    from schema-bucket blocking alone).
    """
    result = BlockingResult(total_tables_considered=len(fingerprints))
    result.total_pairs_possible = len(fingerprints) * (len(fingerprints) - 1) // 2

    buckets: dict[frozenset[tuple[str, str]], list[SchemaFingerprint]] = {}
    for fp in fingerprints:
        buckets.setdefault(fp.schema_bucket_key, []).append(fp)

    seen_pairs: set[tuple[str, str]] = set()

    # Exact-schema-match buckets: every pair within a bucket is a candidate (subject to
    # the size-ratio band), since identical schema is the strongest free filter.
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for a, b in combinations(bucket, 2):
            if not (_size_ratio_ok(a, b) or _naming_pattern_hint(a, b)):
                continue
            key = _sorted_pair(a.table_id, b.table_id)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            result.candidates.append(
                CandidatePair(
                    table_a=a,
                    table_b=b,
                    schema_exact_match=True,
                    naming_pattern_hint=_naming_pattern_hint(a, b),
                )
            )

    # Naming-pattern pairs across DIFFERENT schema buckets: a real parent-copy pair whose
    # copy has a slightly different schema (e.g. one extra audit column) would otherwise be
    # missed entirely by exact-bucket blocking. Only pursued for same-schema-name-prefix
    # candidates (cheap: same source schema_name, name-suffix relationship), not a full
    # second n^2 pass.
    by_schema_name: dict[str, list[SchemaFingerprint]] = {}
    for fp in fingerprints:
        by_schema_name.setdefault(fp.schema_name, []).append(fp)
    for group in by_schema_name.values():
        if len(group) < 2:
            continue
        for a, b in combinations(group, 2):
            key = _sorted_pair(a.table_id, b.table_id)
            if key in seen_pairs:
                continue
            if not _naming_pattern_hint(a, b):
                continue
            seen_pairs.add(key)
            result.candidates.append(
                CandidatePair(
                    table_a=a,
                    table_b=b,
                    schema_exact_match=(a.schema_bucket_key == b.schema_bucket_key),
                    naming_pattern_hint=True,
                )
            )

    return result


def schema_jaccard_similarity(a: SchemaFingerprint, b: SchemaFingerprint) -> float:
    """Jaccard similarity over (column_name, data_type) sets -- 1.0 for identical schemas,
    0.0 for no shared columns at all. Order-insensitive; see ordered_edit_similarity for
    the order-sensitive complement."""
    if not a.unordered_columns and not b.unordered_columns:
        return 1.0
    union = a.unordered_columns | b.unordered_columns
    if not union:
        return 1.0
    intersection = a.unordered_columns & b.unordered_columns
    return len(intersection) / len(union)
