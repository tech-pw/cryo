"""Pivoted Query Synthesis probes (cryo/testing/pqs.py)."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from cryo.testing.pqs import (
    And,
    Cmp,
    Column,
    IsNull,
    Not,
    Or,
    Probe,
    evaluate,
    expected_rows,
    generate_rows,
    multiset,
    pivot_probes,
    random_predicate,
    rectify,
    render,
    shrink,
    to_pyiceberg,
)

COLUMNS = (
    Column("id", "long"),
    Column("amount", "double"),
    Column("name", "string"),
    Column("day", "date"),
    Column("flag", "boolean"),
)


def test_three_valued_logic() -> None:
    row = {"a": None, "b": 1}
    unknown = Cmp("=", "a", 1)
    true = Cmp("=", "b", 1)
    false = Cmp("=", "b", 2)
    assert evaluate(unknown, row) is None
    assert evaluate(Not(unknown), row) is None
    assert evaluate(And(unknown, false), row) is False
    assert evaluate(And(unknown, true), row) is None
    assert evaluate(Or(unknown, true), row) is True
    assert evaluate(Or(unknown, false), row) is None
    assert evaluate(IsNull("a"), row) is True


def test_rectified_predicates_are_always_true_on_their_pivot() -> None:
    rng = random.Random(20260924)
    rows = generate_rows(COLUMNS, 200, rng)
    for _ in range(3000):
        pivot = rng.choice(rows)
        predicate = rectify(random_predicate(COLUMNS, pivot, rng, depth=4), pivot)
        assert evaluate(predicate, pivot) is True, render(predicate)


def test_shrink_finds_the_smallest_failing_sub_predicate() -> None:
    pivot = {"id": 5, "name": None}
    culprit = Cmp("=", "id", 5)
    probe = Probe(pivot, And(Or(culprit, IsNull("name")), Not(Cmp(">", "id", 9))))

    def fails(candidate: Probe) -> bool:
        return "id = 5" in render(candidate.predicate)

    assert shrink(probe, fails).predicate == culprit


def _arrow_schema(with_note: bool) -> pa.Schema:
    fields = [
        pa.field("id", pa.int64()),
        pa.field("amount", pa.float64()),
        pa.field("name", pa.string()),
        pa.field("day", pa.date32()),
        pa.field("flag", pa.bool_()),
    ]
    if with_note:
        fields.append(pa.field("note", pa.int64()))
    return pa.schema(fields)


def _local_table(tmp_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    """A PyIceberg table spread over many small files, with a column added midway so older
    files carry no values (or metrics) for it."""
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.types import LongType

    catalog = SqlCatalog(
        "pqs", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=f"file://{tmp_path}"
    )
    catalog.create_namespace("pqs")
    table = catalog.create_table("pqs.fixture", schema=_arrow_schema(False))
    rng = random.Random(7)
    rows: list[dict[str, Any]] = []
    for batch in range(12):
        chunk = generate_rows(COLUMNS, 25, rng)
        if batch == 6:
            with table.update_schema() as update:
                update.add_column("note", LongType())
        with_note = batch >= 6
        for row in chunk:
            row["note"] = rng.choice([None, 1, 2]) if with_note else None
        payload = (
            chunk if with_note else [{k: v for k, v in r.items() if k != "note"} for r in chunk]
        )
        table.append(pa.Table.from_pylist(payload, schema=_arrow_schema(with_note)))
        rows.extend(chunk)
    return table, rows


def _scan(table: Any, predicate: Any) -> list[dict[str, Any]]:
    return table.scan(row_filter=to_pyiceberg(predicate)).to_arrow().to_pylist()


def _failures(table: Any, rows: list[dict[str, Any]], probes: list[Probe]) -> list[str]:
    failures = []
    for probe in probes:
        got = multiset(_scan(table, probe.predicate))
        if got != multiset(expected_rows(rows, probe.predicate)):
            missing = multiset([probe.pivot]) - got
            failures.append(f"{'pivot missing: ' if missing else ''}{render(probe.predicate)}")
    return failures


@pytest.fixture()
def local_table(tmp_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    return _local_table(tmp_path)


def test_probes_agree_with_pyiceberg_pruning_across_many_files(
    local_table: tuple[Any, list[dict[str, Any]]],
) -> None:
    table, rows = local_table
    columns = (*COLUMNS, Column("note", "long"))
    probes = pivot_probes(rows, columns, random.Random(11), count=150, depth=3)

    assert _failures(table, rows, probes) == []


def test_probes_agree_after_a_rewrite_materializes_an_evolved_column(
    local_table: tuple[Any, list[dict[str, Any]]],
) -> None:
    table, rows = local_table
    columns = (*COLUMNS, Column("note", "long"))
    probes = pivot_probes(rows, columns, random.Random(5), count=100, depth=3)
    before = [multiset(_scan(table, p.predicate)) for p in probes]

    table.overwrite(pa.Table.from_pylist(rows, schema=_arrow_schema(True)))

    assert [multiset(_scan(table, p.predicate)) for p in probes] == before
    assert _failures(table, rows, probes) == []


def test_probes_detect_a_rewrite_that_loses_a_row(
    local_table: tuple[Any, list[dict[str, Any]]],
) -> None:
    table, rows = local_table
    victim = next(r for r in rows if r["id"] is not None and r["name"] is not None)
    survivors = [r for r in rows if r is not victim]
    table.overwrite(pa.Table.from_pylist(survivors, schema=_arrow_schema(True)))

    probes = [
        Probe(victim, rectify(Cmp("=", "id", victim["id"]), victim)),
        *pivot_probes([victim], COLUMNS, random.Random(3), count=20),
    ]
    failures = _failures(table, rows, probes)

    assert failures and all(f.startswith("pivot missing") for f in failures)
