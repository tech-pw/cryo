from __future__ import annotations

from dataclasses import dataclass

from cryo.scanner.quality import ColumnQualityAccumulator, summarize_column_quality


@dataclass(frozen=True)
class _FakeDataFile:
    null_value_counts: dict[int, int]
    value_counts: dict[int, int]


@dataclass(frozen=True)
class _FakeField:
    name: str


class _FakeSchema:
    def __init__(self, names: dict[int, str]) -> None:
        self._names = names

    def find_field(self, field_id: int) -> _FakeField:
        return _FakeField(self._names[field_id])


def test_accumulator_computes_null_rate_across_multiple_files() -> None:
    schema = _FakeSchema({1: "user_id", 2: "email"})
    accum = ColumnQualityAccumulator(schema)

    accum.add(_FakeDataFile(null_value_counts={1: 0, 2: 10}, value_counts={1: 100, 2: 100}))
    accum.add(_FakeDataFile(null_value_counts={1: 5, 2: 20}, value_counts={1: 100, 2: 100}))

    columns = {c.column_name: c for c in accum.finish()}
    assert columns["user_id"].null_count == 5
    assert columns["user_id"].value_count == 200
    assert columns["user_id"].null_rate == 0.025
    assert columns["email"].null_count == 30
    assert columns["email"].value_count == 200
    assert columns["email"].null_rate == 0.15


def test_accumulator_resolves_unknown_field_id_to_stringified_id() -> None:
    schema = _FakeSchema({1: "user_id"})
    accum = ColumnQualityAccumulator(schema)

    accum.add(_FakeDataFile(null_value_counts={99: 1}, value_counts={99: 10}))

    columns = accum.finish()
    assert columns[0].column_name == "99"
    assert columns[0].null_rate == 0.1


def test_accumulator_handles_missing_schema() -> None:
    accum = ColumnQualityAccumulator(None)
    accum.add(_FakeDataFile(null_value_counts={1: 2}, value_counts={1: 20}))

    columns = accum.finish()
    assert columns[0].column_name == "1"
    assert columns[0].null_rate == 0.1


def test_accumulator_ignores_files_with_no_stats() -> None:
    accum = ColumnQualityAccumulator(None)
    accum.add(_FakeDataFile(null_value_counts={}, value_counts={}))

    assert accum.finish() == []


def test_accumulator_tracks_min_max_bounds() -> None:
    schema = _FakeSchema({1: "amount"})
    accum = ColumnQualityAccumulator(schema)
    accum.add(
        type(
            "_File",
            (),
            {
                "null_value_counts": {1: 0},
                "value_counts": {1: 50},
                "lower_bounds": {1: 10},
                "upper_bounds": {1: 100},
            },
        )()
    )
    accum.add(
        type(
            "_File",
            (),
            {
                "null_value_counts": {1: 0},
                "value_counts": {1: 50},
                "lower_bounds": {1: 5},
                "upper_bounds": {1: 200},
            },
        )()
    )
    columns = accum.finish()
    assert columns[0].min_value == 5
    assert columns[0].max_value == 200


def test_summarize_column_quality_finds_worst_offender() -> None:
    schema = _FakeSchema({1: "user_id", 2: "email", 3: "phone"})
    accum = ColumnQualityAccumulator(schema)
    accum.add(
        _FakeDataFile(
            null_value_counts={1: 0, 2: 50, 3: 0},
            value_counts={1: 100, 2: 100, 3: 100},
        )
    )

    summary = summarize_column_quality(accum.finish())

    assert summary["worst_null_column"] == "email"
    assert summary["max_null_rate"] == 0.5
    assert summary["avg_null_rate"] == round((0.0 + 0.5 + 0.0) / 3, 4)


def test_summarize_column_quality_handles_empty_input() -> None:
    summary = summarize_column_quality([])
    assert summary == {"avg_null_rate": 0.0, "max_null_rate": 0.0, "worst_null_column": None}


def test_summarize_column_quality_reports_no_worst_column_when_all_zero() -> None:
    schema = _FakeSchema({1: "user_id"})
    accum = ColumnQualityAccumulator(schema)
    accum.add(_FakeDataFile(null_value_counts={1: 0}, value_counts={1: 100}))

    summary = summarize_column_quality(accum.finish())
    assert summary["worst_null_column"] is None


@dataclass(frozen=True)
class _FakeDataFileWithBounds:
    null_value_counts: dict[int, int]
    value_counts: dict[int, int]
    lower_bounds: dict[int, bytes]
    upper_bounds: dict[int, bytes]


def test_accumulator_decodes_manifest_bounds_via_real_pyiceberg_types() -> None:
    """Bounds are serialized bytes per the Iceberg spec — verify decoding against real
    pyiceberg.conversions round-tripping, not a hand-rolled assumption about the byte format."""
    from pyiceberg.conversions import to_bytes
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField

    schema = Schema(NestedField(1, "amount", LongType()))
    accum = ColumnQualityAccumulator(schema)

    accum.add(
        _FakeDataFileWithBounds(
            null_value_counts={1: 0},
            value_counts={1: 100},
            lower_bounds={1: to_bytes(LongType(), -50)},
            upper_bounds={1: to_bytes(LongType(), 500)},
        )
    )
    accum.add(
        _FakeDataFileWithBounds(
            null_value_counts={1: 0},
            value_counts={1: 100},
            lower_bounds={1: to_bytes(LongType(), 10)},
            upper_bounds={1: to_bytes(LongType(), 1000)},
        )
    )

    [column] = accum.finish()
    assert column.min_value == -50
    assert column.max_value == 1000


def test_accumulator_ignores_undecodable_bounds_without_raising() -> None:
    schema = _FakeSchema({1: "user_id"})
    accum = ColumnQualityAccumulator(schema)

    accum.add(
        _FakeDataFileWithBounds(
            null_value_counts={1: 0},
            value_counts={1: 10},
            lower_bounds={1: b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"},
            upper_bounds={},
        )
    )

    [column] = accum.finish()
    # FakeSchema's find_field returns a name-only object with no field_type — decoding should
    # degrade to None rather than raise.
    assert column.min_value is None
