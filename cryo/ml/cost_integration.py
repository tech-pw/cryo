"""Cache and apply the learned cost model on live savings projections."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings
from cryo.db.models import AuditLog, TableStats
from cryo.domain.enums import OperationType
from cryo.domain.models import SavingsProjection
from cryo.ml.cost_model import _RidgeModel, predict_savings, train_cost_model

_STATE_DIR = Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))
_MODEL_CACHE = _STATE_DIR / "cost_model_cache.json"


def _state_dir() -> Path:
    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))


def _model_cache_path() -> Path:
    return _state_dir() / "cost_model_cache.json"


def _ensure_state_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


def refresh_cost_model_cache(session: Session, settings: Settings) -> bool:
    """Retrain from audit_log realized savings and persist to disk."""
    rows = session.execute(
        select(AuditLog, TableStats)
        .join(TableStats, TableStats.table_id == AuditLog.table_id)
        .where(AuditLog.actual_bytes_freed.isnot(None))
        .order_by(AuditLog.ts.desc())
        .limit(500)
    )
    training: list[dict[str, Any]] = []
    for audit, stats in rows:
        training.append(
            {
                "action": audit.action,
                "actual_bytes_freed": audit.actual_bytes_freed,
                "predicted_bytes_freed": audit.actual_bytes_freed,
                "small_files_count": stats.small_files_count,
                "total_size_gb": stats.total_size_gb,
                "delete_file_pct": stats.delete_file_pct,
                "input_files_count": audit.input_files_count,
            }
        )
    model = train_cost_model(training, price_per_gb_month=settings.cost_price_per_gb_month)
    if model is None:
        return False
    _ensure_state_dir()
    _model_cache_path().write_text(
        json.dumps(
            {
                "weights": model.weights,
                "bias": model.bias,
                "residual_quantile": model.residual_quantile,
                "n_samples": len(training),
            }
        ),
        encoding="utf-8",
    )
    return True


def load_cached_cost_model() -> _RidgeModel | None:
    path = _model_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    weights = payload.get("weights")
    if not isinstance(weights, list):
        return None
    return _RidgeModel(
        weights=[float(w) for w in weights],
        bias=float(payload.get("bias", 0)),
        residual_quantile=float(payload.get("residual_quantile", 0)),
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
