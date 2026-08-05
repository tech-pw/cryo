"""Cache and apply the learned cost model on live savings projections."""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy.orm import Session

from cryo.config import Settings
from cryo.domain.enums import OperationType
from cryo.domain.models import SavingsProjection
from cryo.intelligence.outcomes import OUTCOME_SCHEMA_VERSION, build_outcome_dataset
from cryo.ml.cost_model import _RidgeModel, predict_savings, train_cost_model

logger = logging.getLogger(__name__)

COST_MODEL_CACHE_VERSION = "cryo.cost-model-cache/v2"
_EXPECTED_WEIGHT_COUNT = 7


def _state_dir() -> Path:
    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))


def _model_cache_path() -> Path:
    return _state_dir() / "cost_model_cache.json"


def _ensure_state_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


def refresh_cost_model_cache(session: Session, settings: Settings) -> bool:
    """Retrain from audit_log realized savings and persist to disk."""
    dataset = build_outcome_dataset(session)
    training = dataset.calibration_rows()
    model = train_cost_model(training, price_per_gb_month=settings.cost_price_per_gb_month)
    if model is None:
        return False
    _ensure_state_dir()
    payload = {
        "cache_version": COST_MODEL_CACHE_VERSION,
        "weights": model.weights,
        "bias": model.bias,
        "residual_quantile": model.residual_quantile,
        "feature_min": model.feature_min,
        "feature_max": model.feature_max,
        "n_samples": len(training),
        "outcome_schema_version": OUTCOME_SCHEMA_VERSION,
        "lineage_eligible": dataset.eligible_count,
        "lineage_complete": dataset.complete_count,
        "lineage_completeness": dataset.completeness,
    }
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=_state_dir(),
        prefix=".cost-model-",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(payload))
        temporary_path = Path(temporary.name)
    temporary_path.replace(_model_cache_path())
    return True


def load_cached_cost_model() -> _RidgeModel | None:
    path = _model_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Cost model cache is unreadable; learned predictions are disabled")
        return None
    if (
        payload.get("cache_version") != COST_MODEL_CACHE_VERSION
        or payload.get("outcome_schema_version") != OUTCOME_SCHEMA_VERSION
    ):
        logger.warning("Cost model cache schema is incompatible; retraining is required")
        return None
    weights = payload.get("weights")
    if not isinstance(weights, list) or len(weights) != _EXPECTED_WEIGHT_COUNT:
        logger.warning("Cost model cache has an invalid feature vector; retraining is required")
        return None
    try:
        numeric_weights = [float(weight) for weight in weights]
        bias = float(payload["bias"])
        residual_quantile = float(payload["residual_quantile"])
        feature_min = [float(value) for value in payload["feature_min"]]
        feature_max = [float(value) for value in payload["feature_max"]]
    except (KeyError, TypeError, ValueError):
        logger.warning("Cost model cache contains invalid coefficients; retraining is required")
        return None
    if len(feature_min) != _EXPECTED_WEIGHT_COUNT or len(feature_max) != _EXPECTED_WEIGHT_COUNT:
        logger.warning("Cost model cache has invalid domain bounds; retraining is required")
        return None
    if not all(
        math.isfinite(value)
        for value in [*numeric_weights, bias, residual_quantile, *feature_min, *feature_max]
    ):
        logger.warning("Cost model cache contains non-finite coefficients; retraining is required")
        return None
    return _RidgeModel(
        weights=numeric_weights,
        bias=bias,
        residual_quantile=residual_quantile,
        feature_min=feature_min,
        feature_max=feature_max,
    )


def apply_learned_cost(
    projection: SavingsProjection,
    stats: dict[str, Any],
    settings: Settings,
) -> SavingsProjection:
    """Adjust optimize savings using cached learned model; heuristic fallback when unsure."""
    model = load_cached_cost_model()
    if model is None:
        return projection

    raw = dict(stats.get("raw") or {})
    reclaim_optimize = int(
        raw.get("reclaim_optimize_bytes") or stats.get("reclaim_optimize_bytes") or 0
    )
    if reclaim_optimize <= 0:
        return projection

    row = {
        "action": "optimize",
        "predicted_bytes_freed": reclaim_optimize,
        "reclaim_bytes": reclaim_optimize,
        "small_files_count": stats.get("small_files_count", 0),
        "total_size_gb": stats.get("total_size_gb", 0),
        "delete_file_pct": stats.get("delete_file_pct", 0),
        "input_files_count": stats.get("small_files_count", 0),
        "actual_bytes_freed": reclaim_optimize,
    }
    pred = predict_savings(row, model, price_per_gb_month=settings.cost_price_per_gb_month)

    by_operation = dict(projection.by_operation)
    heuristic_optimize = by_operation.get(OperationType.OPTIMIZE, 0.0)
    by_operation[OperationType.OPTIMIZE] = pred.predicted_usd

    projected_savings = round(sum(by_operation.values()), 4)
    assumptions = dict(projection.assumptions)
    assumptions["learned_cost"] = {
        "used_learned": pred.used_learned,
        "confidence": pred.confidence,
        "heuristic_optimize_usd": heuristic_optimize,
        "learned_optimize_usd": pred.predicted_usd,
        "lower_usd": pred.lower_usd,
        "upper_usd": pred.upper_usd,
        "abstention_reason": pred.abstention_reason,
    }

    return SavingsProjection(
        precision=projection.precision,
        current_monthly_cost_usd=projection.current_monthly_cost_usd,
        projected_monthly_cost_usd=round(
            max(0.0, projection.current_monthly_cost_usd - projected_savings), 4
        ),
        projected_savings_usd_month=projected_savings,
        reclaimable_bytes=projection.reclaimable_bytes,
        by_operation=by_operation,
        query_request_savings_usd_month=projection.query_request_savings_usd_month,
        assumptions=assumptions,
    )
