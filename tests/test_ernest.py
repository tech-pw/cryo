"""Ernest rewrite-duration model (cryo/maintenance/ernest.py)."""

from __future__ import annotations

import math
import random

import pytest

from cryo.maintenance.ernest import (
    MIN_TRAIN_SAMPLES,
    DurationSample,
    calibrate,
    features,
    fit,
    nnls,
)


def _ernest(theta: tuple[float, ...], gib: float, cores: float) -> float:
    return sum(t * x for t, x in zip(theta, features(gib, cores), strict=True))


def test_nnls_recovers_a_noise_free_ernest_model() -> None:
    theta = (90.0, 240.0, 15.0, 4.0)
    rows, targets = [], []
    for gib in (0.1, 0.5, 2.0, 8.0, 30.0):
        for cores in (1, 2, 4):
            rows.append(features(gib, cores))
            targets.append(_ernest(theta, gib, cores))
    got = nnls(rows, targets)
    assert got is not None
    assert got == pytest.approx(theta, rel=1e-6)


def test_nnls_never_returns_a_negative_coefficient() -> None:
    # Time falls as the constant-ish third column grows, which unconstrained least squares
    # would fit with a negative weight.
    rows = [(1.0, float(i), 10.0 - i, 0.0) for i in range(8)]
    targets = [5.0 + 2.0 * i for i in range(8)]
    got = nnls(rows, targets)
    assert got is not None
    assert all(t >= 0 for t in got)


def test_fit_needs_enough_samples() -> None:
    few = [DurationSample(1.0, 1, 60.0)] * (MIN_TRAIN_SAMPLES - 1)
    assert fit(few) is None


def test_calibration_holds_out_the_newest_samples_and_prefers_the_better_model() -> None:
    rng = random.Random(7)
    samples = []
    for _ in range(80):
        gib = rng.choice((0.05, 0.3, 1.0, 6.0, 40.0))
        cores = rng.choice((1, 2, 4))
        seconds = (150 + 280 * gib / cores) * rng.uniform(0.95, 1.05)
        samples.append(DurationSample(gib, cores, seconds))

    cal = calibrate(samples)

    assert cal is not None
    assert cal.n_test == 20 and cal.n_train == 60
    assert cal.preferred
    assert cal.ernest_mape < 0.05 < cal.rate_mape
    summary = cal.as_dict()
    assert summary["theta"]["serial"] == pytest.approx(150, rel=0.15)
    assert summary["preferred"] is True


def test_a_pure_throughput_workload_does_not_switch_to_ernest_for_nothing() -> None:
    # Duration exactly proportional to GiB with one core count: the pooled rate is already
    # exact, so Ernest must not be preferred on a tie.
    samples = [DurationSample(g, 4, g * 144.0) for g in [0.5, 1, 2, 4, 8] * 6]
    cal = calibrate(samples)
    assert cal is not None
    assert cal.rate_mape < 1e-9
    assert not cal.preferred


@pytest.mark.parametrize(
    "entry",
    [
        {"gib": 1, "cores": 2},
        {"gib": 0, "cores": 1, "seconds": 5},
        {"gib": "x", "cores": 1, "seconds": 1},
    ],
)
def test_malformed_summary_entries_are_ignored(entry: dict[str, object]) -> None:
    assert DurationSample.from_summary(entry) is None


def test_duration_model_endpoint_reports_insufficient_samples(session) -> None:  # type: ignore[no-untyped-def]
    from cryo.api.extensions import get_duration_model

    response = get_duration_model(session)
    assert response.status == "insufficient_samples" and response.calibration is None
    assert response.configured == "rate"


def test_features_use_log_of_cores() -> None:
    assert features(8.0, 4) == (1.0, 2.0, math.log(4), 4.0)
