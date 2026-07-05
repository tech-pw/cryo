"""TabPFN-style few-shot table health classification.

Uses metadata signatures (file count, delete ratio, manifest count, schema complexity)
with in-context k-NN classification — TabPFN (Nature 2025) pattern without requiring the
heavy TabPFN package at install time. Optional ``tabpfn`` import upgrades inference when
available.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Metadata signature feature order
_FEATURE_KEYS = (
    "total_files",
    "small_files_count",
    "delete_file_pct",
    "manifest_count",
    "total_snapshots",
    "partition_skew_ratio",
    "avg_file_size_mb",
    "overall_score",
)


@dataclass(frozen=True)
class HealthLabel:
    table_id: str
    needs_emergency_compaction: bool


@dataclass(frozen=True)
class HealthPrediction:
    table_id: str
    needs_emergency_compaction: bool
    probability: float
    method: str


def metadata_signature(stats: dict[str, Any]) -> list[float]:
    """Normalize table stats into a fixed feature vector."""
    return [
        float(stats.get(key, 0) or 0) / _scale(key)
        for key in _FEATURE_KEYS
    ]


def _scale(key: str) -> float:
    scales = {
        "total_files": 10_000.0,
        "small_files_count": 5_000.0,
        "delete_file_pct": 100.0,
        "manifest_count": 1_000.0,
        "total_snapshots": 500.0,
        "partition_skew_ratio": 20.0,
        "avg_file_size_mb": 512.0,
        "overall_score": 100.0,
    }
    return scales.get(key, 1.0)


def classify_health_few_shot(
    target: dict[str, Any],
    labeled_examples: Sequence[HealthLabel],
    example_stats: dict[str, dict[str, Any]],
    *,
    k: int = 5,
) -> HealthPrediction:
    """Few-shot classify whether a table needs emergency compaction."""
    table_id = str(target.get("table_id") or "")
    target_vec = metadata_signature(target)

    # Try optional TabPFN when installed
    tabpfn_result = _try_tabpfn(target_vec, labeled_examples, example_stats)
    if tabpfn_result is not None:
        return HealthPrediction(
            table_id=table_id,
            needs_emergency_compaction=tabpfn_result[0],
            probability=tabpfn_result[1],
            method="tabpfn",
        )

    # k-NN fallback (TabPFN-style in-context inference without training)
    neighbors: list[tuple[float, bool]] = []
    for label in labeled_examples:
        stats = example_stats.get(label.table_id, {})
        vec = metadata_signature(stats)
        dist = _euclidean(target_vec, vec)
        neighbors.append((dist, label.needs_emergency_compaction))

    neighbors.sort(key=lambda item: item[0])
    top = neighbors[: max(1, min(k, len(neighbors)))]
    if not top:
        return HealthPrediction(
            table_id=table_id,
            needs_emergency_compaction=_threshold_fallback(target),
            probability=0.5,
            method="threshold_fallback",
        )

    positive_weight = sum(1.0 / (1.0 + d) for d, label in top if label)
    total_weight = sum(1.0 / (1.0 + d) for d, _ in top)
    prob = positive_weight / total_weight if total_weight else 0.5
    return HealthPrediction(
        table_id=table_id,
        needs_emergency_compaction=prob >= 0.5,
        probability=round(prob, 4),
        method="knn_few_shot",
    )


def evaluate_classifier(
    labeled: Sequence[HealthLabel],
    stats_by_table: dict[str, dict[str, Any]],
    *,
    holdout_fraction: float = 0.25,
) -> dict[str, Any]:
    """Leave-one-out style evaluation vs static threshold rules."""
    if len(labeled) < 4:
        return {"accuracy": 0.0, "beats_threshold": False, "n_samples": len(labeled)}

    holdout_count = max(1, int(len(labeled) * holdout_fraction))
    holdout = labeled[-holdout_count:]
    train = labeled[:-holdout_count]
    train_stats = {label.table_id: stats_by_table[label.table_id] for label in train}

    correct = 0
    threshold_correct = 0
    for label in holdout:
        stats = stats_by_table[label.table_id]
        stats_with_id = {**stats, "table_id": label.table_id}
        pred = classify_health_few_shot(stats_with_id, train, train_stats)
        if pred.needs_emergency_compaction == label.needs_emergency_compaction:
            correct += 1
        if _threshold_fallback(stats) == label.needs_emergency_compaction:
            threshold_correct += 1

    n = len(holdout)
    accuracy = correct / n
    threshold_accuracy = threshold_correct / n
    return {
        "accuracy": round(accuracy, 4),
        "threshold_accuracy": round(threshold_accuracy, 4),
        "beats_threshold": accuracy > threshold_accuracy,
        "n_samples": n,
    }


def _threshold_fallback(stats: dict[str, Any]) -> bool:
    """Static rule baseline from health thresholds."""
    return (
        float(stats.get("small_files_count") or 0) > 500
        or float(stats.get("delete_file_pct") or 0) > 30
        or float(stats.get("overall_score") or 100) < 50
    )


def _euclidean(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) ** 0.5)


def _try_tabpfn(
    target_vec: list[float],
    labeled: Sequence[HealthLabel],
    example_stats: dict[str, dict[str, Any]],
) -> tuple[bool, float] | None:
    try:
        import numpy as np
        from tabpfn import TabPFNClassifier
    except ImportError:
        return None

    if len(labeled) < 2:
        return None

    x_train = np.array([metadata_signature(example_stats[label.table_id]) for label in labeled])
    y_train = np.array([int(label.needs_emergency_compaction) for label in labeled])
    x_test = np.array([target_vec])

    clf = TabPFNClassifier(device="cpu")
    clf.fit(x_train, y_train)
    proba = clf.predict_proba(x_test)[0]
    return bool(proba[1] >= 0.5), float(proba[1])
