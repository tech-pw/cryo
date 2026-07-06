"""Variance-aware Query Performance Prediction (CLOUD 2026, arXiv:2606.03464).

The paper observes that query runtime variance on shared clusters can be 2x and that a QPP
model can shrink prediction error by ~80% if it accounts for *co-tenant load*, data
locality, and caching in addition to the work-set size. Cryo's Governor already probes
live ``system.runtime.queries`` for capacity; this module adds a lightweight predictor
that sits next to that and refuses admissions whose predicted p95 exceeds the per-cluster
target by more than 1.5x.

Design:

* :class:`QPPFeatures` is the (deterministic) feature vector for a single hypothetical
  query. It's stable across processes because the fields are all primitives.
* :class:`QPPModel` implements two predictors -- a heuristic baseline (used until enough
  observations exist) and a small linear regression trainer over :class:`QPPObservation`
  rows. The heuristic lives in :func:`heuristic_predict`; the trainer is in
  :func:`linear_regression_predict` and is intentionally tiny (closed-form normal
  equation on 4 features) so we don't pull in numpy/scikit-learn.
* :class:`QPPStore` persists observations into ``cryo.qpp_observations`` and lets the
  scheduler record completions asynchronously.
"""

from __future__ import annotations

import hashlib
import logging
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# Reference per-file baseline used by the heuristic predictor. Calibrated against the
# reference Trino maintenance workloads observed in the dbt-macros port: a 100-MB OPTIMIZE
# on a single Trino coordinator runs in roughly 30-60s, so 50 ms per file is a reasonable
# starting point. The actual number doesn't matter much -- the regression trainer refines
# it once enough observations accumulate.
DEFAULT_PER_FILE_MS = 50.0


@dataclass(frozen=True)
class QPPFeatures:
    """Deterministic feature vector for a single hypothetical query.

    ``query_text_hash`` is a SHA-256 hex prefix so callers don't have to scrub raw SQL
    before passing it through. The numeric fields are deliberately simple; the variance
    the paper talks about shows up mostly in ``cotenant_load_pct`` and ``is_cached``.
    """

    query_text_hash: str
    table_id: uuid.UUID
    partition_count: int
    files_scanned: int
    is_cached: bool
    cotenant_load_pct: float
    hour_of_day: int
    day_of_week: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_text_hash": self.query_text_hash,
            "table_id": str(self.table_id),
            "partition_count": int(self.partition_count),
            "files_scanned": int(self.files_scanned),
            "is_cached": bool(self.is_cached),
            "cotenant_load_pct": float(self.cotenant_load_pct),
            "hour_of_day": int(self.hour_of_day),
            "day_of_week": int(self.day_of_week),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QPPFeatures:
        return cls(
            query_text_hash=str(data.get("query_text_hash", "")),
            table_id=uuid.UUID(str(data["table_id"])),
            partition_count=int(data.get("partition_count") or 0),
            files_scanned=int(data.get("files_scanned") or 0),
            is_cached=bool(data.get("is_cached", False)),
            cotenant_load_pct=float(data.get("cotenant_load_pct") or 0.0),
            hour_of_day=int(data.get("hour_of_day") or 0),
            day_of_week=int(data.get("day_of_week") or 0),
        )


@dataclass(frozen=True)
class QPPPrediction:
    """Output of :meth:`QPPModel.predict`."""

    p50_ms: float
    p95_ms: float
    p99_ms: float
    confidence: float  # in [0, 1]; higher means we trust the prediction more.

    def exceeds_target(self, target_p95_ms: float, *, tolerance: float = 1.5) -> bool:
        """Return True if the predicted p95 exceeds ``target * tolerance``.

        The 1.5x multiplier is the paper's recommended headroom for cluster variance --
        anything inside that band is treated as "within noise".
        """
        return self.p95_ms > target_p95_ms * tolerance


@dataclass
class QPPModel:
    """A small variance-aware QPP model.

    The model maintains an in-memory cache of training observations (typically the last
    ``window_size`` rows) and falls back to the heuristic predictor until at least
    ``min_observations`` rows are available. The linear-regression trainer uses 4 features
    (``files_scanned``, ``partition_count``, ``cotenant_load_pct``, ``is_cached``) for
    stable closed-form solution.
    """

    per_file_ms: float = DEFAULT_PER_FILE_MS
    cotenant_coefficient: float = 1.0
    cache_speedup: float = 0.6  # cached queries run ~40% faster (heuristic).
    min_observations: int = 20
    window_size: int = 200
    _weights: list[float] = field(default_factory=list)
    _last_train_size: int = 0

    def predict(self, features: QPPFeatures) -> QPPPrediction:
        """Predict p50/p95/p99 ms for the supplied ``features``."""
        if len(self._weights) >= 4 and self._last_train_size >= self.min_observations:
            return linear_regression_predict(features, self._weights)
        return heuristic_predict(
            features, per_file_ms=self.per_file_ms, cotenant=self.cotenant_coefficient,
            cache_speedup=self.cache_speedup,
        )

    def train(self, observations: Iterable[tuple[QPPFeatures, float]]) -> None:
        """Fit the linear regression weights from ``(features, actual_p95_ms)`` pairs.

        Empty input is a no-op (keeps the last weights). More than ``window_size`` rows
        are truncated to the most recent ones, which keeps the model responsive to recent
        cluster changes without unbounded growth.
        """
        rows: list[tuple[QPPFeatures, float]] = []
        for row in observations:
            rows.append(row)
            if len(rows) > self.window_size:
                rows = rows[-self.window_size :]
        if len(rows) < 4:
            return
        weights = _fit_linear_regression(rows)
        if weights is None:
            return
        self._weights = weights
        self._last_train_size = len(rows)

    def confidence(self) -> float:
        """Return a [0, 1] confidence interval.

        Bounded by :attr:`min_observations` -- below that we report 0 because the heuristic
        is unreliable; beyond it we approach 1.0 as the training set grows.
        """
        if self._last_train_size < self.min_observations:
            return 0.0
        return min(1.0, math.log10(self._last_train_size / self.min_observations + 1))


def heuristic_predict(
    features: QPPFeatures,
    *,
    per_file_ms: float = DEFAULT_PER_FILE_MS,
    cotenant: float = 1.0,
    cache_speedup: float = 0.6,
) -> QPPPrediction:
    """Deterministic heuristic predictor.

    ``p95 = files_scanned * per_file_ms * (1 + cotenant * cotenant_load_pct) * cache_factor``

    with the cache factor ``1.0`` for cold queries and ``cache_speedup`` for warm ones.
    p99 is 1.4x p95 and p50 is 0.6x p95 (typical maintenance-workload ratios from the
    reference Trino runs).
    """
    load_factor = 1.0 + max(0.0, cotenant) * max(0.0, features.cotenant_load_pct)
    cache_factor = cache_speedup if features.is_cached else 1.0
    base = features.files_scanned * max(1.0, per_file_ms)
    p95 = base * load_factor * cache_factor
    p50 = p95 * 0.6
    p99 = p95 * 1.4
    confidence = 0.3  # Heuristic baseline is uncalibrated; treat as low confidence.
    return QPPPrediction(
        p50_ms=max(1.0, p50),
        p95_ms=max(1.0, p95),
        p99_ms=max(1.0, p99),
        confidence=confidence,
    )


def linear_regression_predict(features: QPPFeatures, weights: Sequence[float]) -> QPPPrediction:
    """Predict using the closed-form linear regression weights."""
    if len(weights) < 4:
        return heuristic_predict(features)
    bias, w_files, w_parts, w_load = weights[:4]
    cache_factor = 0.6 if features.is_cached else 1.0
    p95 = (
        bias
        + w_files * features.files_scanned
        + w_parts * features.partition_count
        + w_load * features.cotenant_load_pct
    ) * cache_factor
    p95 = max(1.0, p95)
    return QPPPrediction(
        p50_ms=max(1.0, p95 * 0.6),
        p95_ms=p95,
        p99_ms=max(1.0, p95 * 1.4),
        confidence=0.7,  # trained model -- higher confidence than the heuristic.
    )


def _fit_linear_regression(
    rows: Sequence[tuple[QPPFeatures, float]],
) -> list[float] | None:
    """Closed-form OLS over 4 features (with intercept) using only the stdlib.

    Returns ``[bias, w_files, w_parts, w_load]`` or ``None`` if the normal equation
    is singular (e.g. degenerate inputs).
    """
    n = len(rows)
    if n < 4:
        return None
    # Build X^T X and X^T y manually.
    xtx = [[0.0] * 4 for _ in range(4)]
    xty = [0.0] * 4
    for features, y in rows:
        x = [1.0, float(features.files_scanned), float(features.partition_count),
             float(features.cotenant_load_pct)]
        for i in range(4):
            xty[i] += x[i] * y
            for j in range(4):
                xtx[i][j] += x[i] * x[j]
    try:
        weights = _solve_4x4(xtx, xty)
    except ValueError as exc:
        logger.debug("QPP regression singular: %s", exc)
        return None
    return weights


def _solve_4x4(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-Jordan elimination for a 4x4 linear system. Raises ``ValueError`` if singular.

    The threshold ``1e-9`` is a deliberate choice -- a numerically singular normal
    equation (very large condition number, common when a feature column is highly
    correlated with the intercept) produces garbage coefficients; we'd rather fail loudly
    and let the caller fall back to the heuristic than return wildly wrong weights.
    """
    n = 4
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for i in range(n):
        # Find pivot.
        pivot = i
        for k in range(i + 1, n):
            if abs(m[k][i]) > abs(m[pivot][i]):
                pivot = k
        if abs(m[pivot][i]) < 1e-9:
            raise ValueError("singular matrix")
        m[i], m[pivot] = m[pivot], m[i]
        # Normalize row.
        factor = m[i][i]
        for j in range(i, n + 1):
            m[i][j] /= factor
        # Eliminate other rows.
        for k in range(n):
            if k == i:
                continue
            factor = m[k][i]
            if factor == 0:
                continue
            for j in range(i, n + 1):
                m[k][j] -= factor * m[i][j]
    return [m[i][n] for i in range(n)]


def hash_query_text(text: str | None) -> str:
    """Stable 16-char hash of a query string, suitable for ``QPPFeatures.query_text_hash``."""
    if not text:
        return "0" * 16
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class QPPStore:
    """Persistence layer for :class:`QPPObservation` rows.

    Kept separate from the model so a multi-replica Governor can write observations
    asynchronously (via a worker thread or coroutine) without holding the model lock.
    """

    session_factory: Any
    min_observations: int = 20

    def record_observation(
        self,
        features: QPPFeatures,
        *,
        actual_p95_ms: float,
        predicted: QPPPrediction | None = None,
    ) -> uuid.UUID:
        """Persist a single observation. Returns the new row's id."""
        from cryo.db.models import QPPObservation

        now = datetime.now(UTC)
        with self.session_factory() as session:
            row = QPPObservation(
                observed_at=now,
                table_id=features.table_id,
                features=features.to_dict(),
                p50_ms=predicted.p50_ms if predicted else None,
                p95_ms=predicted.p95_ms if predicted else None,
                p99_ms=predicted.p99_ms if predicted else None,
                actual_p95_ms=float(actual_p95_ms),
                cotenant_load_pct=float(features.cotenant_load_pct),
                is_cached=bool(features.is_cached),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    def load_recent(
        self,
        table_id: uuid.UUID | None = None,
        *,
        limit: int = 200,
    ) -> list[tuple[QPPFeatures, float]]:
        """Load the most recent observations as ``(features, actual_p95_ms)`` pairs."""
        from sqlalchemy import select

        from cryo.db.models import QPPObservation

        stmt = select(QPPObservation).order_by(QPPObservation.observed_at.desc()).limit(limit)
        if table_id is not None:
            stmt = stmt.where(QPPObservation.table_id == table_id)
        pairs: list[tuple[QPPFeatures, float]] = []
        with self.session_factory() as session:
            for row in session.scalars(stmt).all():
                if row.actual_p95_ms is None:
                    continue
                try:
                    features = QPPFeatures.from_dict(row.features or {})
                except (KeyError, ValueError, TypeError) as exc:
                    logger.debug("QPP: skipping malformed observation: %s", exc)
                    continue
                pairs.append((features, float(row.actual_p95_ms)))
        # Reverse so the trainer sees oldest-first; the model's window keeps the tail.
        pairs.reverse()
        return pairs


def predict_admission(
    model: QPPModel,
    features: QPPFeatures,
    *,
    target_p95_ms: int,
    tolerance: float = 1.5,
) -> tuple[bool, QPPPrediction]:
    """Convenience: predict and decide whether to admit a maintenance job.

    Returns ``(admit, prediction)``. The Governor should refuse admission when
    ``admit is False`` and surface :attr:`QPPPrediction.p95_ms` to the operator.
    """
    prediction = model.predict(features)
    if prediction.exceeds_target(float(target_p95_ms), tolerance=tolerance):
        return False, prediction
    return True, prediction


__all__ = [
    "DEFAULT_PER_FILE_MS",
    "QPPFeatures",
    "QPPModel",
    "QPPPrediction",
    "QPPStore",
    "hash_query_text",
    "heuristic_predict",
    "linear_regression_predict",
    "predict_admission",
]