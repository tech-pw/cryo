"""Learned cost model with conformal-prediction-style confidence bands.

Lightweight ridge regression trained on ``audit_log.actual_bytes_freed`` vs heuristic
predictions. Falls back to the heuristic when confidence is low (Amazon Stage pattern).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

GIB = 1024**3
_MIN_TRAINING_ROWS = 15
_CONFIDENCE_COVERAGE = 0.90


@dataclass(frozen=True)
class CostPrediction:
    predicted_usd: float
    heuristic_usd: float
    lower_usd: float
    upper_usd: float
    used_learned: bool
    confidence: float
    abstention_reason: str | None = None


@dataclass
class _RidgeModel:
    weights: list[float]
    bias: float
    residual_quantile: float
    feature_min: list[float] | None = None
    feature_max: list[float] | None = None

    def predict(self, features: list[float]) -> float:
        total = self.bias
        for w, x in zip(self.weights, features, strict=True):
            total += w * x
        return max(0.0, total)

    def contains(self, features: list[float]) -> bool:
        if self.feature_min is None or self.feature_max is None:
            return False
        return all(
            lower <= value <= upper
            for value, lower, upper in zip(
                features,
                self.feature_min,
                self.feature_max,
                strict=True,
            )
        )


def extract_features(row: dict[str, Any]) -> list[float]:
    """Feature vector from audit / table stats for savings prediction."""
    return [
        float(row.get("predicted_bytes_freed", 0)) / GIB,
        float(row.get("small_files_count", 0)) / 1000.0,
        float(row.get("total_size_gb", 0)) / 100.0,
        float(row.get("delete_file_pct", 0)) / 100.0,
        float(row.get("input_files_count", 0)) / 1000.0,
        1.0 if row.get("action") == "optimize" else 0.0,
        1.0 if row.get("action") == "expire_snapshots" else 0.0,
    ]


def train_cost_model(
    rows: Sequence[dict[str, Any]],
    *,
    price_per_gb_month: float = 0.023,
    ridge_lambda: float = 0.1,
) -> _RidgeModel | None:
    """Train ridge regression on realized savings rows."""
    if len(rows) < _MIN_TRAINING_ROWS:
        return None

    feature_rows = [extract_features(row) for row in rows]
    targets = [
        (float(row.get("actual_bytes_freed") or 0) / GIB) * price_per_gb_month for row in rows
    ]
    dim = len(feature_rows[0])
    weights = [0.0] * dim
    bias = sum(targets) / len(targets)

    # Closed-form ridge: w = (X'X + λI)^-1 X'y — use simple iterative fit for portability
    learning_rate = 0.05
    for _ in range(200):
        for features, target in zip(feature_rows, targets, strict=True):
            pred = bias + sum(w * x for w, x in zip(weights, features, strict=True))
            error = target - pred
            bias += learning_rate * error
            for i in range(dim):
                weights[i] += learning_rate * (error * features[i] - ridge_lambda * weights[i])

    residuals = sorted(
        abs(target - (bias + sum(w * x for w, x in zip(weights, features, strict=True))))
        for features, target in zip(feature_rows, targets, strict=True)
    )
    idx = int(_CONFIDENCE_COVERAGE * (len(residuals) - 1))
    q = residuals[idx] if residuals else 0.0

    return _RidgeModel(
        weights=weights,
        bias=bias,
        residual_quantile=q,
        feature_min=[min(row[index] for row in feature_rows) for index in range(dim)],
        feature_max=[max(row[index] for row in feature_rows) for index in range(dim)],
    )


def predict_savings(
    row: dict[str, Any],
    model: _RidgeModel | None,
    *,
    price_per_gb_month: float = 0.023,
    min_confidence: float = 0.6,
) -> CostPrediction:
    """Predict monthly USD savings with conformal band; fall back to heuristic when unsure."""
    predicted_bytes = float(row.get("predicted_bytes_freed") or row.get("reclaim_bytes") or 0)
    heuristic_usd = round((predicted_bytes / GIB) * price_per_gb_month, 4)

    if model is None:
        return CostPrediction(
            predicted_usd=heuristic_usd,
            heuristic_usd=heuristic_usd,
            lower_usd=heuristic_usd,
            upper_usd=heuristic_usd,
            used_learned=False,
            confidence=0.0,
            abstention_reason="model_unavailable",
        )

    features = extract_features(row)
    if not model.contains(features):
        return CostPrediction(
            predicted_usd=heuristic_usd,
            heuristic_usd=heuristic_usd,
            lower_usd=heuristic_usd,
            upper_usd=heuristic_usd,
            used_learned=False,
            confidence=0.0,
            abstention_reason="outside_training_domain",
        )
    learned = round(model.predict(features), 4)
    band = model.residual_quantile
    lower = round(max(0.0, learned - band), 4)
    upper = round(learned + band, 4)

    # Confidence: narrower band relative to prediction magnitude
    magnitude = max(learned, heuristic_usd, 0.001)
    confidence = round(max(0.0, 1.0 - band / magnitude), 4)
    use_learned = confidence >= min_confidence

    predicted = learned if use_learned else heuristic_usd
    return CostPrediction(
        predicted_usd=predicted,
        heuristic_usd=heuristic_usd,
        lower_usd=lower if use_learned else heuristic_usd,
        upper_usd=upper if use_learned else heuristic_usd,
        used_learned=use_learned,
        confidence=confidence,
        abstention_reason=None if use_learned else "low_confidence",
    )


def evaluate_model(
    rows: Sequence[dict[str, Any]],
    model: _RidgeModel,
    *,
    price_per_gb_month: float = 0.023,
) -> dict[str, Any]:
    """Held-out evaluation: MAE vs heuristic and calibration of confidence bands."""
    if not rows:
        return {"mae_learned": 0.0, "mae_heuristic": 0.0, "beats_heuristic": False}

    learned_errors: list[float] = []
    heuristic_errors: list[float] = []
    in_band = 0
    abstentions = 0

    for row in rows:
        actual = (float(row.get("actual_bytes_freed") or 0) / GIB) * price_per_gb_month
        heuristic = (float(row.get("predicted_bytes_freed") or 0) / GIB) * price_per_gb_month
        heuristic_errors.append(abs(actual - heuristic))
        features = extract_features(row)
        if not model.contains(features):
            abstentions += 1
            continue
        learned = model.predict(features)
        learned_errors.append(abs(actual - learned))
        if (
            max(0.0, learned - model.residual_quantile)
            <= actual
            <= (learned + model.residual_quantile)
        ):
            in_band += 1

    mae_learned = sum(learned_errors) / len(learned_errors) if learned_errors else 0.0
    mae_heuristic = sum(heuristic_errors) / len(heuristic_errors)
    return {
        "mae_learned": round(mae_learned, 4),
        "mae_heuristic": round(mae_heuristic, 4),
        "beats_heuristic": bool(learned_errors) and mae_learned < mae_heuristic,
        "band_coverage": round(in_band / len(learned_errors), 4) if learned_errors else 0.0,
        "n_samples": len(rows),
        "n_evaluated": len(learned_errors),
        "abstentions": abstentions,
        "abstention_rate": round(abstentions / len(rows), 4),
    }
