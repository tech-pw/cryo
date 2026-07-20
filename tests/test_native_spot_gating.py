"""Unit tests for native Job spot gating (scoping §6.5)."""

from __future__ import annotations

from cryo.engines.native.job_dispatch import (
    compute_job_resources,
    estimate_duration_seconds,
    spot_allowed_for_job,
)


def test_compute_job_resources_scales_with_chunk_size() -> None:
    small_cpu, small_mem = compute_job_resources(10)
    large_cpu, large_mem = compute_job_resources(500)
    assert large_cpu >= small_cpu
    assert large_mem >= small_mem
    assert large_cpu <= 4000
    assert large_mem <= 16384


def test_spot_denied_when_not_allowed() -> None:
    assert spot_allowed_for_job(chunk_size_gb=5.0, config={"k8s_spot_allow": False}) is False


def test_spot_allowed_for_small_checkpointed_chunk() -> None:
    assert (
        spot_allowed_for_job(
            chunk_size_gb=10.0,
            estimated_duration_seconds=600.0,
            config={
                "k8s_spot_allow": True,
                "k8s_spot_max_chunk_gb": 50.0,
                "k8s_spot_max_estimated_duration_seconds": 1800.0,
                "k8s_spot_require_checkpointing": True,
                "partial_progress_enabled": True,
            },
        )
        is True
    )


def test_spot_denied_for_large_chunk_even_when_allow_true() -> None:
    assert (
        spot_allowed_for_job(
            chunk_size_gb=200.0,
            config={
                "k8s_spot_allow": True,
                "k8s_spot_max_chunk_gb": 50.0,
                "partial_progress_enabled": True,
            },
        )
        is False
    )


def test_spot_denied_without_checkpointing_when_required() -> None:
    assert (
        spot_allowed_for_job(
            chunk_size_gb=10.0,
            estimated_duration_seconds=300.0,
            config={
                "k8s_spot_allow": True,
                "k8s_spot_require_checkpointing": True,
                "partial_progress_enabled": False,
            },
        )
        is False
    )


def test_estimate_duration_seconds_monotone() -> None:
    assert estimate_duration_seconds(10) < estimate_duration_seconds(100)
