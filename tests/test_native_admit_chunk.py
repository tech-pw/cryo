"""Tests for chunk_size derived from packed pod memory."""

from __future__ import annotations

from cryo.engines.native.packing import build_packable_job, pack_jobs
from cryo.engines.native.resources import (
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    derive_chunk_params,
)


def test_shared_pod_chunk_matches_dispatch_mem() -> None:
    ceilings = ResourceCeilings(max_cpu_milli=4000, max_mem_mib=16384)
    jobs = [
        build_packable_job(
            job_id="a",
            table_id="ta",
            run_id="r1",
            stats={"total_size_gb": 30, "total_files": 100},
            operations=("optimize",),
            ceilings=ceilings,
        ),
        build_packable_job(
            job_id="b",
            table_id="tb",
            run_id="r1",
            stats={"total_size_gb": 40, "total_files": 200},
            operations=("optimize",),
            ceilings=ceilings,
        ),
    ]
    specs = pack_jobs(jobs, ceilings=ceilings)
    assert len(specs) == 1
    derived = chunk_size_gb_for_pod_mem(specs[0].mem_mib, {"total_files": 200})
    params = derive_chunk_params(specs[0].mem_mib, {"total_files": 200}, {"file_size_mb": 256})
    assert params["chunk_size_gb"] == derived
    assert derived >= 1.0
    assert params["max_bytes_per_group"] >= 256 * 1024 * 1024
    assert params["max_bytes_per_group"] <= 8 * 1024 * 1024 * 1024


def test_hot_table_dedicated_chunk_from_its_pod() -> None:
    ceilings = ResourceCeilings()
    hot = build_packable_job(
        job_id="hot",
        table_id="th",
        run_id="r1",
        stats={
            "total_size_gb": 2000,
            "total_files": 100_000,
            "max_files_per_partition": 60_000,
        },
        operations=("optimize",),
        ceilings=ceilings,
    )
    assert hot.estimate.hot
    specs = pack_jobs([hot], ceilings=ceilings)
    assert len(specs) == 1
    assert specs[0].dedicated
    chunk = chunk_size_gb_for_pod_mem(specs[0].mem_mib, hot.stats)
    # Large dedicated pod should get a large chunk budget.
    assert chunk >= 50.0
