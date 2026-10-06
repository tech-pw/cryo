from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def explain_health_change(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    preset_name: str | None = None,
) -> str:
    """Narrate a deterministic before/after health diff in plain English."""
    lines: list[str] = []
    grade_before = before.get("grade")
    grade_after = after.get("grade")
    score_before = before.get("overall_score")
    score_after = after.get("overall_score")

    if grade_before != grade_after:
        lines.append(
            f"Health grade changed from {grade_before} to {grade_after} "
            f"(score {score_before} → {score_after})."
        )
    else:
        lines.append(f"Health grade remains {grade_after} (score {score_before} → {score_after}).")

    for metric, label in _METRIC_LABELS.items():
        old = before.get(metric)
        new = after.get(metric)
        if old is None or new is None:
            continue
        if _changed(old, new):
            direction = "increased" if _float(new) > _float(old) else "decreased"
            lines.append(f"{label} {direction} from {_format(old)} to {_format(new)}.")

    if preset_name:
        lines.append(f"Recommended preset to address regressions: {preset_name}.")
    return " ".join(lines)


_METRIC_LABELS = {
    "small_files_count": "Small file count",
    "delete_file_pct": "Delete file percentage",
    "manifest_count": "Manifest count",
    "metadata_to_data_ratio": "Metadata-to-data ratio",
    "total_snapshots": "Snapshot count",
}


def _changed(old: Any, new: Any) -> bool:
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return bool(abs(float(new) - float(old)) > 1e-9)
    return bool(old != new)


def _float(value: Any) -> float:
    return float(value)


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
