"""Calibrated per-engine runtime and rewrite-byte prediction intervals.

Wave 3 initiative I4 (`docs/cryo-10-of-10-plan.md` Section 7.3) calls for "per-engine runtime,
rewritten-byte, request, and query-impact intervals" that meet the Section 5.3 calibration gates
(<=15% median / <=30% p90 MAPE; an 80% interval containing 75-85% of realized outcomes). This module
covers runtime (`AuditLog.duration_ms`) and measured rewrite output bytes
(`EffectReceipt.result.actual_bytes_written`), built directly on the
`cryo.intelligence.outcomes` (I1) lineage dataset. Request and query-impact intervals remain open.

Design, mirroring split-conformal prediction:

- rows must already be in chronological order (as returned by
  ``OutcomeDataset.calibration_rows()``); each ``(engine, action)`` group's *earlier* rows fit a
  point ratio (median ``actual / reference_feature``) and its *later* rows -- still strictly before
  any external holdout -- calibrate the nonconformity quantile band. No row that contributes to a
  fitted band is ever reused to score coverage, so evaluation cannot leak into calibration.
- predictions are ``reference_feature * ratio``, where the reference feature is the pre-action
  table-size estimate (rewrite bytes) or ``input_files_count`` (runtime). Both are produced
  upstream of the action outcome, so no target leaks into its own feature.
- engine-specific groups are preferred; a pooled cross-engine group for the same action is used only
  when the engine-specific sample is too small, and every prediction/evaluation record says which
  scope produced it so "per-engine" is never claimed without engine-specific evidence.
- insufficient rows, a missing reference feature, an out-of-training-domain feature value, or a
  band too wide relative to the point estimate all abstain rather than extrapolate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Any, Literal, get_args

IntervalTarget = Literal["runtime_ms", "rewrite_bytes"]
RUNTIME_MS: IntervalTarget = "runtime_ms"
REWRITE_BYTES: IntervalTarget = "rewrite_bytes"
INTERVAL_TARGETS: tuple[IntervalTarget, ...] = get_args(IntervalTarget)

IntervalScope = Literal["engine", "pooled"]

_MIN_GROUP_ROWS = 12
_MIN_CALIBRATION_ROWS = 4
_CALIBRATION_FRACTION = 0.3
DEFAULT_TARGET_COVERAGE = 0.80
_DEFAULT_MIN_CONFIDENCE = 0.4

_FEATURE_FIELD: dict[IntervalTarget, str] = {
    REWRITE_BYTES: "estimated_rewrite_bytes",
    RUNTIME_MS: "input_files_count",
}
_ACTUAL_FIELD: dict[IntervalTarget, str] = {
    REWRITE_BYTES: "actual_rewrite_bytes",
    RUNTIME_MS: "duration_ms",
}


def _feature_and_actual(row: dict[str, Any], target: IntervalTarget) -> tuple[float, float] | None:
    feature_raw = row.get(_FEATURE_FIELD[target])
    actual_raw = row.get(_ACTUAL_FIELD[target])
    if feature_raw is None or actual_raw is None:
        return None
    try:
        feature = float(feature_raw)
        actual = float(actual_raw)
    except (TypeError, ValueError):
        return None
    if feature <= 0 or actual < 0:
        return None
    return feature, actual


@dataclass(frozen=True, slots=True)
class _RatioGroup:
    engine: str | None
    action: str
    scope: IntervalScope
    point_ratio: float
    lower_ratio: float
    upper_ratio: float
    feature_min: float
    feature_max: float
    n_fit: int
    n_calibration: int


@dataclass(frozen=True, slots=True)
class IntervalModel:
    """Fitted split-conformal ratio models for one target, keyed by (engine, action)."""

    target: IntervalTarget
    target_coverage: float
    groups: dict[tuple[str | None, str], _RatioGroup]

    def group_for(self, engine: str | None, action: str) -> _RatioGroup | None:
        specific = self.groups.get((engine, action)) if engine else None
        return specific if specific is not None else self.groups.get((None, action))


@dataclass(frozen=True, slots=True)
class IntervalPrediction:
    target: IntervalTarget
    engine: str | None
    action: str
    scope: IntervalScope | None
    used_model: bool
    point_estimate: float
    lower: float
    upper: float
    target_coverage: float
    confidence: float
    abstention_reason: str | None = None


def _fit_group(
    engine: str | None,
    action: str,
    scope: IntervalScope,
    samples: list[tuple[float, float]],
    *,
    target_coverage: float,
    min_rows: int,
) -> _RatioGroup | None:
    if len(samples) < min_rows:
        return None
    calibration_size = max(_MIN_CALIBRATION_ROWS, round(len(samples) * _CALIBRATION_FRACTION))
    if calibration_size >= len(samples):
        return None
    fit_rows = samples[:-calibration_size]
    calib_rows = samples[-calibration_size:]
    if not fit_rows or len(calib_rows) < _MIN_CALIBRATION_ROWS:
        return None

    point_ratio = median(actual / feature for feature, actual in fit_rows)
    nonconformity = sorted(abs((actual / feature) - point_ratio) for feature, actual in calib_rows)
    rank = ceil((len(nonconformity) + 1) * target_coverage)
    idx = min(len(nonconformity) - 1, max(0, rank - 1))
    band = nonconformity[idx] if nonconformity else 0.0

    all_features = [feature for feature, _ in samples]
    return _RatioGroup(
        engine=engine,
        action=action,
        scope=scope,
        point_ratio=point_ratio,
        lower_ratio=max(0.0, point_ratio - band),
        upper_ratio=point_ratio + band,
        feature_min=min(all_features),
        feature_max=max(all_features),
        n_fit=len(fit_rows),
        n_calibration=len(calib_rows),
    )


def fit_interval_model(
    rows: Sequence[dict[str, Any]],
    target: IntervalTarget,
    *,
    min_group_rows: int = _MIN_GROUP_ROWS,
    target_coverage: float = DEFAULT_TARGET_COVERAGE,
) -> IntervalModel:
    """Fit split-conformal ratio bands per (engine, action) group.

    ``rows`` must be chronologically ordered lineage-complete calibration rows (as produced by
    ``OutcomeDataset.calibration_rows()``); ordering is what keeps calibration leakage-free -- see
    module docstring.
    """
    if not (0.0 < target_coverage < 1.0):
        raise ValueError("target_coverage must be between 0 and 1 exclusive")

    by_action: dict[str, list[tuple[float, float]]] = {}
    by_engine_action: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        pair = _feature_and_actual(row, target)
        if pair is None:
            continue
        action = str(row.get("action") or "")
        if not action:
            continue
        by_action.setdefault(action, []).append(pair)
        engine = row.get("engine")
        if engine:
            by_engine_action.setdefault((str(engine), action), []).append(pair)

    groups: dict[tuple[str | None, str], _RatioGroup] = {}
    for action, samples in by_action.items():
        pooled = _fit_group(
            None,
            action,
            "pooled",
            samples,
            target_coverage=target_coverage,
            min_rows=min_group_rows,
        )
        if pooled is not None:
            groups[(None, action)] = pooled
    for (engine, action), samples in by_engine_action.items():
        specific = _fit_group(
            engine,
            action,
            "engine",
            samples,
            target_coverage=target_coverage,
            min_rows=min_group_rows,
        )
        if specific is not None:
            groups[(engine, action)] = specific

    return IntervalModel(target=target, target_coverage=target_coverage, groups=groups)


def _abstain(
    target: IntervalTarget,
    engine: str | None,
    action: str,
    target_coverage: float,
    reason: str,
    *,
    scope: IntervalScope | None = None,
    confidence: float = 0.0,
) -> IntervalPrediction:
    return IntervalPrediction(
        target=target,
        engine=engine,
        action=action,
        scope=scope,
        used_model=False,
        point_estimate=0.0,
        lower=0.0,
        upper=0.0,
        target_coverage=target_coverage,
        confidence=confidence,
        abstention_reason=reason,
    )


def predict_interval(
    row: dict[str, Any],
    model: IntervalModel,
    *,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> IntervalPrediction:
    """Predict a calibrated interval for one row, or abstain with a stated reason."""
    action = str(row.get("action") or "")
    engine_raw = row.get("engine")
    engine = str(engine_raw) if engine_raw else None
    if not action:
        return _abstain(model.target, engine, action, model.target_coverage, "missing_action")

    group = model.group_for(engine, action)
    if group is None:
        return _abstain(
            model.target,
            engine,
            action,
            model.target_coverage,
            "insufficient_evidence",
        )

    feature_raw = row.get(_FEATURE_FIELD[model.target])
    if feature_raw is None:
        return _abstain(
            model.target,
            engine,
            action,
            model.target_coverage,
            "missing_feature",
            scope=group.scope,
        )
    try:
        feature = float(feature_raw)
    except (TypeError, ValueError):
        return _abstain(
            model.target,
            engine,
            action,
            model.target_coverage,
            "missing_feature",
            scope=group.scope,
        )
    if feature <= 0 or not (group.feature_min <= feature <= group.feature_max):
        return _abstain(
            model.target,
            engine,
            action,
            model.target_coverage,
            "outside_training_domain",
            scope=group.scope,
        )

    point = feature * group.point_ratio
    lower = max(0.0, feature * group.lower_ratio)
    upper = max(lower, feature * group.upper_ratio)
    magnitude = max(point, lower, 1e-9)
    confidence = round(max(0.0, 1.0 - (upper - lower) / (2 * magnitude)), 4)
    if confidence < min_confidence:
        return _abstain(
            model.target,
            engine,
            action,
            model.target_coverage,
            "low_confidence",
            scope=group.scope,
            confidence=confidence,
        )

    return IntervalPrediction(
        target=model.target,
        engine=engine,
        action=action,
        scope=group.scope,
        used_model=True,
        point_estimate=round(point, 4),
        lower=round(lower, 4),
        upper=round(upper, 4),
        target_coverage=model.target_coverage,
        confidence=confidence,
        abstention_reason=None,
    )


@dataclass(frozen=True, slots=True)
class GroupEvaluation:
    engine: str | None
    action: str
    scope: IntervalScope | None
    n_samples: int
    n_evaluated: int
    mape_median: float | None
    mape_p90: float | None
    interval_coverage: float | None


@dataclass(frozen=True, slots=True)
class IntervalEvaluation:
    target: IntervalTarget
    target_coverage: float
    n_samples: int
    n_evaluated: int
    abstentions: int
    abstention_rate: float
    mape_median: float | None
    mape_p90: float | None
    interval_coverage: float | None
    by_group: tuple[GroupEvaluation, ...]


def _median_and_p90(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)
    p90_idx = min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))
    return round(median(ordered), 4), round(ordered[p90_idx], 4)


def _absolute_percentage_error(actual: float, predicted: float) -> float:
    if actual > 0:
        return abs(actual - predicted) / actual
    return 0.0 if predicted == 0 else 1.0


def evaluate_interval_model(
    rows: Sequence[dict[str, Any]],
    model: IntervalModel,
    *,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> IntervalEvaluation:
    """Score a chronologically later holdout slice that never contributed to ``model``.

    Reports the Section 5.3 metrics -- median/p90 MAPE and interval coverage -- with abstentions
    counted separately rather than folded into accuracy, plus a per-(engine, action) breakdown so
    pooled-fallback scope is never mistaken for engine-specific calibration.
    """
    errors: list[float] = []
    in_band = 0
    abstentions = 0
    buckets: dict[tuple[str | None, str], dict[str, Any]] = {}

    for row in rows:
        prediction = predict_interval(row, model, min_confidence=min_confidence)
        key = (prediction.engine, prediction.action)
        bucket = buckets.setdefault(
            key, {"scope": prediction.scope, "n": 0, "errors": [], "in_band": 0}
        )
        bucket["n"] += 1
        if prediction.scope is not None:
            bucket["scope"] = prediction.scope

        if not prediction.used_model:
            abstentions += 1
            continue

        pair = _feature_and_actual(row, model.target)
        if pair is None:
            # Model predicted from the reference feature, but the outcome's actual target value
            # (e.g. duration_ms) is missing on this row -- cannot be scored either way.
            abstentions += 1
            continue

        _, actual = pair
        ape = _absolute_percentage_error(actual, prediction.point_estimate)
        errors.append(ape)
        bucket["errors"].append(ape)
        if prediction.lower <= actual <= prediction.upper:
            in_band += 1
            bucket["in_band"] += 1

    n_evaluated = len(errors)
    mape_median, mape_p90 = _median_and_p90(errors)
    by_group = tuple(
        GroupEvaluation(
            engine=engine,
            action=action,
            scope=bucket["scope"],
            n_samples=bucket["n"],
            n_evaluated=len(bucket["errors"]),
            mape_median=_median_and_p90(bucket["errors"])[0],
            mape_p90=_median_and_p90(bucket["errors"])[1],
            interval_coverage=(
                round(bucket["in_band"] / len(bucket["errors"]), 4) if bucket["errors"] else None
            ),
        )
        for (engine, action), bucket in sorted(
            buckets.items(), key=lambda item: (item[0][0] or "", item[0][1])
        )
        if bucket["n"] > 0
    )

    return IntervalEvaluation(
        target=model.target,
        target_coverage=model.target_coverage,
        n_samples=len(rows),
        n_evaluated=n_evaluated,
        abstentions=abstentions,
        abstention_rate=round(abstentions / len(rows), 4) if rows else 0.0,
        mape_median=mape_median,
        mape_p90=mape_p90,
        interval_coverage=round(in_band / n_evaluated, 4) if n_evaluated else None,
        by_group=by_group,
    )
