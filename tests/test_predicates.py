from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from cryo.api.predicates import compile_predicate, evaluate_predicate
from cryo.scheduler.rules import evaluate_predicate as scheduler_evaluate_predicate

BASE_ROW: dict[str, Any] = {
    "schema_name": "mview",
    "table_name": "orders_daily",
    "is_active": True,
    "needs_compaction": True,
    "compaction_priority": "critical",
    "grade": "F",
    "overall_score": 55,
    "total_files": 1_000,
    "small_files_count": 200,
    "total_partitions": 20,
    "total_size_gb": 150.5,
    "partition_col": "ds",
    "partition_col_type": "date",
    "query_count_30d": 25,
}


def _compile_sql(predicate: Mapping[str, Any]) -> str:
    clause = compile_predicate(predicate)
    assert clause is not None
    return str(
        clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.parametrize(
    ("predicate", "miss"),
    [
        ({"field": "schema_name", "op": "=", "value": "mview"}, {"schema_name": "raw"}),
        ({"field": "table_name", "op": "!=", "value": "tmp"}, {"table_name": "tmp"}),
        ({"field": "overall_score", "op": "<", "value": 60}, {"overall_score": 60}),
        ({"field": "overall_score", "op": "<=", "value": 55}, {"overall_score": 56}),
        ({"field": "total_size_gb", "op": ">", "value": 100}, {"total_size_gb": 100}),
        ({"field": "total_files", "op": ">=", "value": 1_000}, {"total_files": 999}),
        ({"field": "grade", "op": "in", "value": ["D", "F"]}, {"grade": "A"}),
        ({"field": "table_name", "op": "like", "value": "orders%"}, {"table_name": "events"}),
        ({"field": "schema_name", "op": "glob", "value": "mv?ew*"}, {"schema_name": "raw"}),
    ],
)
def test_leaf_predicates_compile_and_evaluate(
    predicate: Mapping[str, Any], miss: Mapping[str, Any]
) -> None:
    assert _compile_sql(predicate)
    assert evaluate_predicate(predicate, BASE_ROW)
    assert scheduler_evaluate_predicate(predicate, BASE_ROW)
    assert not evaluate_predicate(predicate, BASE_ROW | dict(miss))


def test_compound_predicates_compile_and_evaluate_with_not() -> None:
    predicate = {
        "all": [
            {"field": "is_active", "op": "=", "value": True},
            {
                "any": [
                    {"field": "grade", "op": "in", "value": ["D", "F"]},
                    {"field": "total_size_gb", "op": ">=", "value": 200},
                ]
            },
            {"not": {"field": "table_name", "op": "glob", "value": "tmp*"}},
        ]
    }

    sql = _compile_sql(predicate)

    assert " AND " in sql
    assert " OR " in sql
    assert "NOT" in sql
    assert evaluate_predicate(predicate, BASE_ROW)
    assert not evaluate_predicate(predicate, BASE_ROW | {"table_name": "tmp_orders"})


def test_empty_top_level_predicates_keep_backwards_compatible_defaults() -> None:
    assert compile_predicate(None) is None
    assert evaluate_predicate(None, BASE_ROW)
    assert scheduler_evaluate_predicate(None, BASE_ROW)


@pytest.mark.parametrize(
    ("predicate", "message"),
    [
        ({"field": "table_id", "op": "=", "value": "t1"}, "Unsupported filter field"),
        ({"field": "grade", "op": "contains", "value": "F"}, "Unsupported filter operator"),
        ({"all": {"field": "grade", "op": "=", "value": "F"}}, "children must be a list"),
        ({"not": []}, "not predicate must be an object"),
        ({"field": "grade", "op": "in", "value": "F"}, "in predicate value"),
    ],
)
def test_predicate_validation_is_shared(predicate: Mapping[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        compile_predicate(predicate)

    with pytest.raises(ValueError, match=message):
        evaluate_predicate(predicate, BASE_ROW)
