"""Tests for scan-chunk Job resource sizing (mirrors test_native_resources.py)."""

from __future__ import annotations

from cryo.engines.native.resources import MAX_POD_CPU_MILLI, MAX_POD_MEM_MIB, ResourceCeilings
from cryo.scanner.resources import estimate_scan_resources


def test_no_prior_stats_uses_manifest_hint_floor() -> None:
    est = estimate_scan_resources(None, manifest_count_hint=None)
    assert est.mem_mib >= 512
    assert est.cpu_milli >= 250
    assert "cold_manifest_hint" in " ".join(est.reasons)


def test_cold_estimate_scales_with_manifest_hint() -> None:
    small = estimate_scan_resources(None, manifest_count_hint=1)
    large = estimate_scan_resources(None, manifest_count_hint=500)
    assert large.mem_mib > small.mem_mib
    assert large.cpu_milli >= small.cpu_milli


def test_prior_stats_scale_with_files_and_manifests() -> None:
    small = estimate_scan_resources({"total_files": 100, "manifest_count": 5})
    large = estimate_scan_resources(
        {
            "total_files": 500_000,
            "manifest_count": 50_000,
            "max_files_per_partition": 100_000,
        }
    )
    assert large.mem_mib > small.mem_mib
    assert large.cpu_milli > small.cpu_milli
    assert "prior_total_files=500000" in large.reasons


def test_never_exceeds_ceilings() -> None:
    est = estimate_scan_resources(
        {
            "total_files": 100_000_000,
            "manifest_count": 10_000_000,
            "max_files_per_partition": 5_000_000,
        }
    )
    assert est.mem_mib <= MAX_POD_MEM_MIB
    assert est.cpu_milli <= MAX_POD_CPU_MILLI


def test_respects_custom_ceilings() -> None:
    caps = ResourceCeilings(max_cpu_milli=1000, max_mem_mib=2048)
    est = estimate_scan_resources(
        {"total_files": 10_000_000, "manifest_count": 1_000_000},
        ceilings=caps,
    )
    assert est.mem_mib <= 2048
    assert est.cpu_milli <= 1000


def test_prior_stats_take_priority_over_manifest_hint() -> None:
    est = estimate_scan_resources(
        {"total_files": 100, "manifest_count": 5}, manifest_count_hint=999_999
    )
    assert "prior_total_files=100" in est.reasons
    assert est.mem_mib < 2048
