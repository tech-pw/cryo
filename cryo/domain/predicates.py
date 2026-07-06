from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SUPPORTED_FIELDS = frozenset(
    {
        "source_id",
        "schema_name",
        "table_name",
        "is_active",
        "needs_compaction",
        "compaction_priority",
        "grade",
        "overall_score",
        "total_files",
        "small_files_count",
        "total_partitions",
        "total_size_gb",
        "partition_col",
        "partition_col_type",
        "query_count_30d",
        "projected_savings_usd_month",
        "projected_monthly_s3_cost_usd",
        "reclaimable_bytes",
        "estimated_monthly_s3_cost_usd",
        "cost_precision",
    }
)
LEAF_OPERATORS = frozenset({"=", "!=", "<", "<=", ">", ">=", "in", "like", "glob"})
LIKE_ESCAPE = "\\"


def evaluate_predicate(predicate: Mapping[str, Any] | None, row: Mapping[str, Any]) -> bool:
    if not predicate:
        return True

    if "all" in predicate:
        return all(_required_evaluate(child, row) for child in _children(predicate["all"]))
    if "any" in predicate:
        return any(_required_evaluate(child, row) for child in _children(predicate["any"]))
    if "not" in predicate:
        return not _required_evaluate(_child(predicate["not"]), row)

    return _evaluate_leaf(predicate, row)


def _required_evaluate(predicate: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    if not predicate:
        raise ValueError("empty predicate is not allowed inside compound predicates")
    return evaluate_predicate(predicate, row)


def _evaluate_leaf(predicate: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    field_name = _field(predicate)
    op = _operator(predicate)
    expected = predicate.get("value")
    actual = row.get(field_name)

    if op == "=":
        return bool(actual == expected)
    if op == "!=":
        return bool(actual != expected)
    if op in {"<", "<=", ">", ">="}:
        if actual is None or expected is None:
            return False
        comparison = _numeric_compare(actual, expected)
        if op == "<":
            return comparison < 0
        if op == "<=":
            return comparison <= 0
        if op == ">":
            return comparison > 0
        return comparison >= 0
    if op == "in":
        if not isinstance(expected, list | tuple | set | frozenset):
            raise ValueError("in predicate value must be a collection")
        return bool(actual in expected)
    if op == "like":
        return _like_matches(actual, expected)
    if op == "glob":
        return _like_matches(actual, _glob_to_like(str(expected)))

    raise ValueError(f"Unsupported filter operator: {op}")


def _field(predicate: Mapping[str, Any]) -> str:
    field_name = predicate.get("field")
    if not isinstance(field_name, str):
        raise ValueError("leaf predicate must include a string field")
    if field_name not in SUPPORTED_FIELDS:
        raise ValueError(f"Unsupported filter field: {field_name}")
    return field_name


def _operator(predicate: Mapping[str, Any]) -> str:
    op = str(predicate.get("op") or "=").lower()
    if op not in LEAF_OPERATORS:
        raise ValueError(f"Unsupported filter operator: {op}")
    return op


def _children(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("compound predicate children must be a list")
    if not all(isinstance(child, Mapping) for child in value):
        raise ValueError("compound predicate children must be objects")
    return value


def _child(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("not predicate must be an object")
    return value


def _numeric_compare(actual: Any, expected: Any) -> int:
    left = float(actual)
    right = float(expected)
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _like_matches(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return False
    return re.fullmatch(_like_to_regex(str(expected)), str(actual), flags=re.DOTALL) is not None


def _like_to_regex(pattern: str) -> str:
    regex: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == LIKE_ESCAPE and index + 1 < len(pattern):
            regex.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        if char == "%":
            regex.append(".*")
        elif char == "_":
            regex.append(".")
        else:
            regex.append(re.escape(char))
        index += 1
    return "".join(regex)


def _glob_to_like(value: str) -> str:
    translated: list[str] = []
    for char in value:
        if char == "*":
            translated.append("%")
        elif char == "?":
            translated.append("_")
        elif char in {LIKE_ESCAPE, "%", "_"}:
            translated.append(f"{LIKE_ESCAPE}{char}")
        else:
            translated.append(char)
    return "".join(translated)
