from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_NEEDS_WORK_STAGES = frozenset({"needs_work", "eligible", "selected"})
_ELIGIBLE_STAGES = frozenset({"eligible", "selected"})


def decision_funnel(
    summary: dict[str, Any] | None,
    history: Sequence[Mapping[str, Any] | Any] | None = None,
) -> list[dict[str, int | str | None]]:
    """Stages we can defend from a stored batch summary. Missing stages stay unrecorded."""
    stages: list[dict[str, int | str | None]]
    if not summary or summary.get("pipeline") != "batch":
        stages = [
            {"stage": "needs_work", "count": None, "reason": "not recorded"},
            {"stage": "eligible", "count": None, "reason": "not recorded"},
            {"stage": "selected", "count": None, "reason": "not recorded"},
            {"stage": "ran", "count": None, "reason": "not recorded"},
            {"stage": "succeeded", "count": None, "reason": "not recorded"},
        ]
    else:
        selected = summary.get("selected")
        succeeded = summary.get("succeeded")
        failed = summary.get("failed")
        over_budget = summary.get("over_budget")
        ran = None
        if isinstance(succeeded, int) and isinstance(failed, int):
            partial = summary.get("partial")
            ran = succeeded + failed + (partial if isinstance(partial, int) else 0)
        stages = [
            {"stage": "needs_work", "count": None, "reason": "not recorded"},
            {"stage": "eligible", "count": None, "reason": "not recorded"},
            {
                "stage": "selected",
                "count": selected if isinstance(selected, int) else None,
                "reason": (
                    f"over budget {over_budget}" if isinstance(over_budget, int) else "not recorded"
                ),
            },
            {"stage": "ran", "count": ran, "reason": None if ran is not None else "not recorded"},
            {
                "stage": "succeeded",
                "count": succeeded if isinstance(succeeded, int) else None,
                "reason": f"failed {failed}" if isinstance(failed, int) else "not recorded",
            },
        ]
    if history is None:
        return stages
    needs_work, eligible = _history_counts(history)
    for row in stages:
        if row["stage"] == "needs_work":
            row["count"] = needs_work
            row["reason"] = None
        elif row["stage"] == "eligible":
            row["count"] = eligible
            row["reason"] = None
    return stages


def _history_counts(history: Sequence[Mapping[str, Any] | Any]) -> tuple[int, int]:
    needs_work = 0
    eligible = 0
    for row in history:
        stage = _stage_of(row)
        weight = 1
        if isinstance(row, Mapping) and isinstance(row.get("count"), int):
            weight = row["count"]
        elif not isinstance(row, Mapping):
            raw_count = getattr(row, "count", None)
            if isinstance(raw_count, int):
                weight = raw_count
        if stage in _NEEDS_WORK_STAGES:
            needs_work += weight
        if stage in _ELIGIBLE_STAGES:
            eligible += weight
    return needs_work, eligible


def _stage_of(row: Mapping[str, Any] | Any) -> str | None:
    if isinstance(row, Mapping):
        value = row.get("stage")
    else:
        value = getattr(row, "stage", None)
    return value if isinstance(value, str) else None
