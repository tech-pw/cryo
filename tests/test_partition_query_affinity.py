"""Unit tests for cryo/planner/partition_query_affinity.py (AutoComp §A3.1)."""

from __future__ import annotations

from cryo.domain.enums import PartitionTransform
from cryo.planner.partition_query_affinity import MIN_COVERAGE_RATIO, compute_partition_affinity
from cryo.planner.partitions import PartitionFieldSpec

_DAY_FIELD = PartitionFieldSpec(
    base_column="event_date",
    transform=PartitionTransform.DAY,
    partition_field="event_date_day",
)
_IDENTITY_FIELD = PartitionFieldSpec(
    base_column="region",
    transform=PartitionTransform.IDENTITY,
    partition_field="region",
)
_BUCKET_FIELD = PartitionFieldSpec(
    base_column="user_id",
    transform=PartitionTransform.BUCKET,
    partition_field="user_id_bucket",
)

# 2025-05-01 .. 2025-05-04 epoch days, computed once so assertions read as facts, not magic
# numbers that happen to match the implementation.
_DAY_1 = 20209  # 2025-05-01
_DAY_2 = 20210  # 2025-05-02
_DAY_3 = 20211  # 2025-05-03
_DAY_4 = 20212  # 2025-05-04

_KNOWN_DAYS = {str(_DAY_1): _DAY_1, str(_DAY_2): _DAY_2, str(_DAY_3): _DAY_3, str(_DAY_4): _DAY_4}


def test_equality_on_base_column_hits_only_that_partition():
    queries = ["SELECT * FROM t WHERE event_date = DATE '2025-05-02'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {str(_DAY_2): 1.0}
    assert result.resolved_queries == 1
    assert result.total_queries == 1


def test_equality_on_derived_partition_column_needs_no_date_parsing():
    queries = [f"SELECT * FROM t WHERE event_date_day = {_DAY_3}"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {str(_DAY_3): 1.0}


def test_literal_first_equality_is_flipped_correctly():
    queries = ["SELECT * FROM t WHERE DATE '2025-05-02' = event_date"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {str(_DAY_2): 1.0}


def test_in_list_splits_credit_evenly_across_matched_partitions():
    queries = [f"SELECT * FROM t WHERE event_date_day IN ({_DAY_3}, {_DAY_4})"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {str(_DAY_3): 0.5, str(_DAY_4): 0.5}


def test_between_range_hits_every_known_partition_inside_it():
    queries = ["SELECT * FROM t WHERE event_date BETWEEN DATE '2025-05-01' AND DATE '2025-05-03'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    # day 4 (05-04) is outside the range and must not get credit
    assert result.hits == {str(_DAY_1): 1 / 3, str(_DAY_2): 1 / 3, str(_DAY_3): 1 / 3}
    assert str(_DAY_4) not in result.hits


def test_open_ended_range_only_matches_partitions_actually_present():
    queries = ["SELECT * FROM t WHERE event_date >= DATE '2025-05-03'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    # >= 05-03 matches day 3 and day 4 out of the known set, nothing beyond it (no invented
    # partitions for an unbounded range).
    assert set(result.hits) == {str(_DAY_3), str(_DAY_4)}
    assert result.hits[str(_DAY_3)] == 0.5
    assert result.hits[str(_DAY_4)] == 0.5


def test_query_not_referencing_partition_column_is_not_resolved():
    queries = ["SELECT * FROM t WHERE label = 'x'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {}
    assert result.total_queries == 1
    assert result.resolved_queries == 0


def test_predicate_matching_no_known_partition_is_unattributed_not_a_crash():
    queries = ["SELECT * FROM t WHERE event_date = DATE '2030-01-01'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {}
    assert result.resolved_queries == 0
    assert result.total_queries == 1


def test_unparseable_sql_is_unattributed_not_a_crash():
    queries = ["not even sql {{{"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {}
    assert result.total_queries == 1


def test_not_equal_gives_no_positive_attribution():
    # "!= one day" matches "almost everything" and isn't a useful hot-partition signal.
    queries = ["SELECT * FROM t WHERE event_date != DATE '2025-05-02'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.hits == {}
    assert result.resolved_queries == 0


def test_identity_transform_matches_string_partition_values_directly():
    known = {"us": "us", "eu": "eu", "apac": "apac"}
    queries = ["SELECT * FROM t WHERE region = 'us'"]
    result = compute_partition_affinity(queries, _IDENTITY_FIELD, known)
    assert result.hits == {"us": 1.0}


def test_bucket_transform_cannot_be_derived_from_base_column_literal():
    known = {"0": 0, "1": 1, "2": 2}
    queries = ["SELECT * FROM t WHERE user_id = 42"]
    result = compute_partition_affinity(queries, _BUCKET_FIELD, known)
    # Cryo doesn't know the engine's hash function -- must not invent a fake hit.
    assert result.hits == {}
    assert result.resolved_queries == 0


def test_bucket_transform_via_derived_column_still_works():
    known = {"0": 0, "1": 1, "2": 2}
    queries = ["SELECT * FROM t WHERE user_id_bucket = 1"]
    result = compute_partition_affinity(queries, _BUCKET_FIELD, known)
    assert result.hits == {"1": 1.0}


def test_coverage_ratio_and_trustworthy_threshold():
    queries = ["SELECT * FROM t WHERE event_date = DATE '2025-05-02'"] * 3 + [
        "SELECT * FROM t WHERE label = 'x'"
    ] * 17
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.total_queries == 20
    assert result.resolved_queries == 3
    assert result.coverage_ratio == 0.15
    assert MIN_COVERAGE_RATIO == 0.2
    assert result.is_trustworthy is False


def test_empty_query_list_is_not_trustworthy():
    result = compute_partition_affinity([], _DAY_FIELD, _KNOWN_DAYS)
    assert result.total_queries == 0
    assert result.coverage_ratio == 0.0
    assert result.is_trustworthy is False


def test_none_and_empty_query_texts_are_skipped():
    queries = [None, "", "SELECT * FROM t WHERE event_date = DATE '2025-05-02'"]
    result = compute_partition_affinity(queries, _DAY_FIELD, _KNOWN_DAYS)
    assert result.total_queries == 1
    assert result.hits == {str(_DAY_2): 1.0}
