"""Rewrite-duration model after Ernest (Venkataraman et al., NSDI 2016).

Ernest predicts a job's runtime on ``m`` machines from its input scale ``s`` with a small
non-negative linear model over terms that mirror how analytics jobs spend time:

    t = θ0 + θ1·(s/m) + θ2·log(m) + θ3·m,   θ ≥ 0 (fit by non-negative least squares)

(fixed serial cost, parallel work, tree-shaped aggregation, all-to-all communication). Cryo's
batch lanes run Spark in local mode on 1, 2 or 4 cores, so ``m`` is the lane's cores and ``s``
the GiB a rewrite statement rewrote.

Where this differs from the paper: Ernest picks its training runs with optimal experiment
design on small data samples. Cryo cannot run side experiments against production tables, so
it learns passively from the rewrite statements batch runs already completed. The fit is only
used when it beats the pooled-throughput estimate on held-out, newer statements.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MIN_TRAIN_SAMPLES = 20
MIN_TEST_SAMPLES = 5
HOLDOUT_FRACTION = 0.25
# Relative holdout-error improvement Ernest needs before it replaces the pooled rate, so float
# noise between two near-exact estimators never flips the choice.
PREFERENCE_MARGIN = 0.05
_TERMS = ("serial", "parallel", "log_machines", "machines")


@dataclass(frozen=True)
class DurationSample:
    gib: float
    cores: float
    seconds: float

    @classmethod
    def from_summary(cls, entry: Mapping[str, Any]) -> DurationSample | None:
        try:
            gib, cores, seconds = (float(entry[k]) for k in ("gib", "cores", "seconds"))
        except (KeyError, TypeError, ValueError):
            return None
        if gib <= 0 or cores <= 0 or seconds <= 0:
            return None
        return cls(gib=gib, cores=cores, seconds=seconds)


def features(gib: float, cores: float) -> tuple[float, float, float, float]:
    m = max(cores, 1.0)
    return (1.0, gib / m, math.log(m), m)


@dataclass(frozen=True)
class DurationModel:
    theta: tuple[float, float, float, float]
    n_samples: int

    def predict_seconds(self, gib: float, cores: float) -> float:
        return sum(t * x for t, x in zip(self.theta, features(gib, cores), strict=True))

    def as_dict(self) -> dict[str, Any]:
        return {
            "theta": {name: round(t, 6) for name, t in zip(_TERMS, self.theta, strict=True)},
            "n_samples": self.n_samples,
        }


@dataclass(frozen=True)
class Calibration:
    model: DurationModel
    n_train: int
    n_test: int
    ernest_mape: float
    rate_mape: float
    rate_gb_per_hour: float

    @property
    def preferred(self) -> bool:
        return self.n_test >= MIN_TEST_SAMPLES and self.ernest_mape < self.rate_mape * (
            1 - PREFERENCE_MARGIN
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.model.as_dict(),
            "n_train": self.n_train,
            "n_test": self.n_test,
            "holdout_mape_ernest": round(self.ernest_mape, 4),
            "holdout_mape_rate": round(self.rate_mape, 4),
            "rate_gb_per_hour": round(self.rate_gb_per_hour, 3),
            "preferred": self.preferred,
        }


def fit(samples: Sequence[DurationSample]) -> DurationModel | None:
    if len(samples) < MIN_TRAIN_SAMPLES:
        return None
    rows = [features(s.gib, s.cores) for s in samples]
    theta = nnls(rows, [s.seconds for s in samples])
    if theta is None:
        return None
    return DurationModel(theta=theta, n_samples=len(samples))


def calibrate(samples: Sequence[DurationSample]) -> Calibration | None:
    """Fit on the older statements, score both estimators on the newest ``HOLDOUT_FRACTION``.

    ``samples`` must be in completion order, so the holdout is a later time period rather than
    a random split that would let the model see the future.
    """
    n_test = max(MIN_TEST_SAMPLES, int(len(samples) * HOLDOUT_FRACTION))
    train, test = samples[:-n_test], samples[-n_test:]
    model = fit(train)
    if model is None:
        return None
    total_hours = sum(s.seconds for s in train) / 3600
    rate = sum(s.gib for s in train) / total_hours if total_hours > 0 else 0.0
    if rate <= 0:
        return None
    return Calibration(
        model=model,
        n_train=len(train),
        n_test=len(test),
        ernest_mape=_mape(test, lambda s: model.predict_seconds(s.gib, s.cores)),
        rate_mape=_mape(test, lambda s: s.gib / rate * 3600),
        rate_gb_per_hour=rate,
    )


def nnls(
    rows: Sequence[Sequence[float]], targets: Sequence[float]
) -> tuple[float, float, float, float] | None:
    """Exact non-negative least squares by enumerating active sets.

    With four terms there are 15 non-empty supports; the NNLS solution is the unconstrained
    least-squares solution on one of them, so solving each and keeping the best non-negative
    one is exact and avoids an iterative solver.
    """
    width = len(rows[0]) if rows else 0
    best: tuple[float, list[float]] | None = None
    for size in range(1, width + 1):
        for support in itertools.combinations(range(width), size):
            coef = _least_squares([[r[j] for j in support] for r in rows], targets)
            if coef is None or any(c < 0 for c in coef):
                continue
            theta = [0.0] * width
            for j, c in zip(support, coef, strict=True):
                theta[j] = c
            sse = sum(
                (y - sum(t * x for t, x in zip(theta, r, strict=True))) ** 2
                for r, y in zip(rows, targets, strict=True)
            )
            if best is None or sse < best[0]:
                best = (sse, theta)
    if best is None:
        return None
    t = best[1]
    return (t[0], t[1], t[2], t[3])


def _least_squares(rows: Sequence[Sequence[float]], targets: Sequence[float]) -> list[float] | None:
    k = len(rows[0])
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    aty = [sum(r[i] * y for r, y in zip(rows, targets, strict=True)) for i in range(k)]
    return _solve(ata, aty)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) <= 1e-12 * scale:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for r in range(n):
            if r != col:
                factor = aug[r][col] / aug[col][col]
                for c in range(col, n + 1):
                    aug[r][c] -= factor * aug[col][c]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def _mape(samples: Iterable[DurationSample], predict: Any) -> float:
    errors = [abs(predict(s) - s.seconds) / s.seconds for s in samples]
    return sum(errors) / len(errors) if errors else math.inf
