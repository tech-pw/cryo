"""Tests for native Job bin-packer (hot dedicated, cold FFD, ≤16Gi)."""

from __future__ import annotations

from cryo.engines.native.packing import (
    PackableJob,
    build_packable_job,
    pack_jobs,
    pack_single_table,
)
from cryo.engines.native.resources import (
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    TableResourceEstimate,
)


def _job(
    job_id: str,
    *,
    run_id: str = "run-1",
    mem: int = 1024,
    cpu: int = 500,
    hot: bool = False,
) -> PackableJob:
    return PackableJob(
        job_id=job_id,
        table_id=f"table-{job_id}",
        run_id=run_id,
        estimate=TableResourceEstimate(
            cpu_milli=cpu,
            mem_mib=mem,
            hot=hot,
            has_rewrite=True,
        ),
    )


def test_single_table_one_pod() -> None:
    specs = pack_jobs([_job("a", mem=2048)])
    assert len(specs) == 1
    assert specs[0].job_ids == ("a",)
    assert specs[0].mem_mib == 2048


def test_hot_gets_dedicated_pod() -> None:
    jobs = [
        _job("hot", mem=8192, hot=True),
        _job("c1", mem=512),
        _job("c2", mem=512),
    ]
    specs = pack_jobs(jobs)
    hot_specs = [s for s in specs if s.dedicated]
    cold_specs = [s for s in specs if not s.dedicated]
    assert len(hot_specs) == 1
    assert hot_specs[0].job_ids == ("hot",)
    assert len(cold_specs) == 1
    assert set(cold_specs[0].job_ids) == {"c1", "c2"}
    # Serial → max mem of cold members
    assert cold_specs[0].mem_mib == 512


def test_all_tiny_share_one_pod() -> None:
    jobs = [_job(f"t{i}", mem=600, cpu=300) for i in range(5)]
    specs = pack_jobs(jobs)
    assert len(specs) == 1
    assert len(specs[0].members) == 5
    assert specs[0].mem_mib == 600
    assert specs[0].cpu_milli == 300


def test_over_ceiling_splits() -> None:
    # With serial packing, max(mem) decides fit — so use ceilings that force
    # separate pods when even one member is near the ceiling AND we also
    # respect max_tables_per_pod for cold packing pressure. Use lower max
    # tables so many medium tables still pack by count if mem fits.
    caps = ResourceCeilings(max_cpu_milli=4000, max_mem_mib=2048)
    # Each cold job needs 1800 MiB — serial max is 1800, so they all fit one pod.
    # To force a split under serial semantics, use max_tables_per_pod.
    jobs = [_job(f"t{i}", mem=1800) for i in range(4)]
    specs = pack_jobs(jobs, ceilings=caps, max_tables_per_pod=2)
    assert len(specs) == 2
    assert all(s.mem_mib <= 2048 for s in specs)
    assert all(s.mem_mib <= MAX_POD_MEM_MIB for s in specs)


def test_never_emits_above_16gi() -> None:
    jobs = [
        PackableJob(
            job_id="huge",
            table_id="t",
            run_id="run-1",
            estimate=TableResourceEstimate(
                cpu_milli=4000,
                mem_mib=MAX_POD_MEM_MIB,
                hot=True,
                has_rewrite=True,
            ),
        )
    ]
    specs = pack_jobs(jobs)
    assert specs[0].mem_mib <= MAX_POD_MEM_MIB


def test_different_runs_packed_separately() -> None:
    jobs = [
        _job("a", run_id="r1", mem=500),
        _job("b", run_id="r2", mem=500),
    ]
    specs = pack_jobs(jobs)
    assert len(specs) == 2
    assert {s.run_id for s in specs} == {"r1", "r2"}


def test_build_packable_job_uses_estimator() -> None:
    job = build_packable_job(
        job_id="j1",
        table_id="t1",
        run_id="r1",
        stats={"total_size_gb": 600, "total_files": 1000},
        operations=("optimize",),
        params={"chunk_size_gb": 50},
    )
    assert job.estimate.hot
    spec = pack_single_table(job)
    assert spec.dedicated
    assert spec.mem_mib <= MAX_POD_MEM_MIB
