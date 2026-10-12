"""Stage 3: exact Trino verification for high-confidence candidate pairs
(docs/TABLE_DEDUPLICATION_DESIGN.md SS4.4, SS9, SS10).

Only reached for pairs scoring above a high Stage 2 threshold -- this is the one stage
that touches real data volume, so it must be gated hard. Computes an exact row-hash
EXCEPT/INTERSECT comparison via Trino to get a real overlap percentage and, critically,
establish CONTAINMENT DIRECTION (is A a subset of B, B a subset of A, or a true symmetric
near-duplicate) -- design doc SS9 is explicit that containment and duplication are
different actionable outcomes, and getting the direction wrong (recommending the superset
be dropped when only the subset is read) is a real, named risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cryo.db.model_utils import catalog_name_for_table
from cryo.db.models import Table
from cryo.engines.trino.client import connect, coordinators_from_config
from cryo.engines.trino.sql import quote_qualified_name


class VerifiedRelationship(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    CONTAINS_A_IN_B = "contains_a_in_b"  # table_a's rows are (almost) all present in table_b
    CONTAINS_B_IN_A = "contains_b_in_a"
    UNRELATED = "unrelated"  # Stage 2 score was high but Stage 3 disconfirmed it


# Thresholds chosen to match the design doc's stated confidence tiers (SS9/SS10): exact
# duplicates should be near-100% overlap both directions; containment requires one
# direction near-100% and the other meaningfully lower; anything below near-duplicate
# territory on BOTH directions means Stage 2's score was a false positive.
_EXACT_DUPLICATE_THRESHOLD = 0.98
_NEAR_DUPLICATE_THRESHOLD = 0.90
_CONTAINMENT_THRESHOLD = 0.95
_UNRELATED_THRESHOLD = 0.50


@dataclass(frozen=True, slots=True)
class VerificationResult:
    relationship: VerifiedRelationship
    # Fraction of table_a's sampled/hashed rows found in table_b, and vice versa. These
    # are the two directional numbers containment direction is derived from -- a symmetric
    # near-duplicate has both high; a containment pair has one much higher than the other.
    a_rows_found_in_b_pct: float
    b_rows_found_in_a_pct: float
    rows_compared_a: int
    rows_compared_b: int


def verify_pair_exact(
    table_a: Table,
    table_b: Table,
    *,
    trino_config: dict,
    row_limit: int = 500_000,
) -> VerificationResult:
    """Run the real Stage 3 comparison via Trino.

    Hashes every column's value per row (not just a natural key -- none is assumed known)
    with Trino's own hashing so the comparison runs server-side rather than pulling both
    tables' full row sets into this process. Bounded by row_limit for cost control on the
    fleet's largest tables (design doc SS4.4: "for very large tables, accept a sampled
    EXCEPT ... rather than a full scan") -- this makes the result an estimate on huge
    tables, but a much tighter one than Stage 2's MinHash sample, since Trino computes the
    true overlap over whatever rows are compared rather than estimating from a signature.
    """
    name_a = quote_qualified_name(
        catalog_name_for_table(table_a), table_a.schema_name, table_a.table_name
    )
    name_b = quote_qualified_name(
        catalog_name_for_table(table_b), table_b.schema_name, table_b.table_name
    )
    row_limit = int(row_limit)

    coordinators = coordinators_from_config(trino_config)
    conn = connect(coordinators[0])
    cur = conn.cursor()

    # to_hex(sha256(...)) over the row cast to varchar is a simple, schema-agnostic exact
    # row fingerprint -- works regardless of column types without needing per-table type
    # introspection here (dedup.sampling's row hashing does the equivalent in Python for
    # Stage 2's cheaper sample-based path; this is the Stage 3 server-side equivalent).
    cur.execute(
        f"""
        WITH a_hashes AS (
            SELECT to_hex(sha256(to_utf8(CAST(row(a.*) AS VARCHAR)))) AS row_hash
            FROM (SELECT * FROM {name_a} LIMIT {row_limit}) a
        ),
        b_hashes AS (
            SELECT to_hex(sha256(to_utf8(CAST(row(b.*) AS VARCHAR)))) AS row_hash
            FROM (SELECT * FROM {name_b} LIMIT {row_limit}) b
        ),
        a_count AS (SELECT count(*) AS n FROM a_hashes),
        b_count AS (SELECT count(*) AS n FROM b_hashes),
        a_in_b AS (
            SELECT count(*) AS n FROM a_hashes WHERE row_hash IN (SELECT row_hash FROM b_hashes)
        ),
        b_in_a AS (
            SELECT count(*) AS n FROM b_hashes WHERE row_hash IN (SELECT row_hash FROM a_hashes)
        )
        SELECT a_count.n, b_count.n, a_in_b.n, b_in_a.n
        FROM a_count, b_count, a_in_b, b_in_a
        """
    )
    row = cur.fetchone()
    rows_a, rows_b, a_in_b, b_in_a = row

    a_pct = (a_in_b / rows_a) if rows_a else 0.0
    b_pct = (b_in_a / rows_b) if rows_b else 0.0

    relationship = _classify_relationship(a_pct, b_pct)

    return VerificationResult(
        relationship=relationship,
        a_rows_found_in_b_pct=a_pct,
        b_rows_found_in_a_pct=b_pct,
        rows_compared_a=rows_a,
        rows_compared_b=rows_b,
    )


def _classify_relationship(a_pct: float, b_pct: float) -> VerifiedRelationship:
    if a_pct >= _EXACT_DUPLICATE_THRESHOLD and b_pct >= _EXACT_DUPLICATE_THRESHOLD:
        return VerifiedRelationship.EXACT_DUPLICATE
    if a_pct >= _NEAR_DUPLICATE_THRESHOLD and b_pct >= _NEAR_DUPLICATE_THRESHOLD:
        return VerifiedRelationship.NEAR_DUPLICATE
    if a_pct >= _CONTAINMENT_THRESHOLD and b_pct < _CONTAINMENT_THRESHOLD:
        # Nearly all of A's rows are in B, but not vice versa -- A is (close to) a subset of B.
        return VerifiedRelationship.CONTAINS_A_IN_B
    if b_pct >= _CONTAINMENT_THRESHOLD and a_pct < _CONTAINMENT_THRESHOLD:
        return VerifiedRelationship.CONTAINS_B_IN_A
    if a_pct < _UNRELATED_THRESHOLD and b_pct < _UNRELATED_THRESHOLD:
        return VerifiedRelationship.UNRELATED
    # Falls between containment and near-duplicate thresholds on at least one side --
    # still worth surfacing to a human as a near-duplicate rather than silently dropped,
    # per design doc SS7/SS9's bias toward recall over precision when the cost of a false
    # negative (missed duplicate) is much lower than a false positive (wrong drop).
    return VerifiedRelationship.NEAR_DUPLICATE
