"""Bin-pack table resource estimates into 1..N native K8s Job dispatch specs.

Rules (serial-within-shared-pod):
- Hot tables get a dedicated pod.
- Cold tables pack first-fit-decreasing under CPU/mem ceilings (≤16Gi mem).
- Shared-pod request = max(cpu), max(mem) of members (serial execution).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from cryo.engines.native.resources import (
    MAX_POD_CPU_MILLI,
    MAX_POD_MEM_MIB,
    ResourceCeilings,
    TableResourceEstimate,
    estimate_table_resources,
)


@dataclass(frozen=True)
class PackableJob:
    """One MaintenanceJob candidate for packing."""

    job_id: str
    table_id: str
    run_id: str
    estimate: TableResourceEstimate
    stats: Mapping[str, Any] = field(default_factory=dict)
    plan: Mapping[str, Any] | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchSpec:
    """One K8s Job that will run 1..M table jobs serially."""

    run_id: str
    members: tuple[PackableJob, ...]
    cpu_milli: int
    mem_mib: int
    dedicated: bool = False

    @property
    def job_ids(self) -> tuple[str, ...]:
        return tuple(m.job_id for m in self.members)

    @property
    def table_ids(self) -> tuple[str, ...]:
        return tuple(m.table_id for m in self.members)


def pack_jobs(
    jobs: Sequence[PackableJob],
    *,
    ceilings: ResourceCeilings | None = None,
    max_tables_per_pod: int = 32,
) -> list[DispatchSpec]:
    """Pack jobs from a single run into 1..N DispatchSpecs.

    Jobs from different run_ids are packed separately (grouped by run_id).
    """
    caps = ceilings or ResourceCeilings()
    by_run: dict[str, list[PackableJob]] = {}
    for job in jobs:
        by_run.setdefault(job.run_id, []).append(job)

    specs: list[DispatchSpec] = []
    for run_id, run_jobs in by_run.items():
        specs.extend(
            _pack_single_run(
                run_id,
                run_jobs,
                caps=caps,
                max_tables_per_pod=max_tables_per_pod,
            )
        )
    return specs


def pack_single_table(
    job: PackableJob,
    *,
    ceilings: ResourceCeilings | None = None,
) -> DispatchSpec:
    """One table → one DispatchSpec (still clamped to 16Gi)."""
    caps = ceilings or ResourceCeilings()
    cpu = min(job.estimate.cpu_milli, caps.max_cpu_milli, MAX_POD_CPU_MILLI)
    mem = min(job.estimate.mem_mib, caps.max_mem_mib, MAX_POD_MEM_MIB)
    return DispatchSpec(
        run_id=job.run_id,
        members=(job,),
        cpu_milli=cpu,
        mem_mib=mem,
        dedicated=job.estimate.hot,
    )


def build_packable_job(
    *,
    job_id: str,
    table_id: str,
    run_id: str,
    stats: Mapping[str, Any] | None = None,
    plan: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    operations: Sequence[Any] | None = None,
    ceilings: ResourceCeilings | None = None,
) -> PackableJob:
    estimate = estimate_table_resources(
        stats,
        operations=operations,
        plan=plan,
        params=params,
        ceilings=ceilings,
    )
    return PackableJob(
        job_id=str(job_id),
        table_id=str(table_id),
        run_id=str(run_id),
        estimate=estimate,
        stats=dict(stats or {}),
        plan=dict(plan) if plan else None,
        params=dict(params or {}),
    )


def _pack_single_run(
    run_id: str,
    jobs: Sequence[PackableJob],
    *,
    caps: ResourceCeilings,
    max_tables_per_pod: int,
) -> list[DispatchSpec]:
    if not jobs:
        return []
    if len(jobs) == 1:
        return [pack_single_table(jobs[0], ceilings=caps)]

    hot = [j for j in jobs if j.estimate.hot]
    cold = [j for j in jobs if not j.estimate.hot]
    cold.sort(key=lambda j: (j.estimate.mem_mib, j.estimate.cpu_milli), reverse=True)

    specs: list[DispatchSpec] = []
    for job in hot:
        specs.append(pack_single_table(job, ceilings=caps))

    # First-fit decreasing into open bins.
    bins: list[list[PackableJob]] = []
    for job in cold:
        placed = False
        for bin_jobs in bins:
            if len(bin_jobs) >= max_tables_per_pod:
                continue
            trial = bin_jobs + [job]
            cpu, mem = _serial_pod_request(trial)
            if cpu <= caps.max_cpu_milli and mem <= caps.max_mem_mib:
                bin_jobs.append(job)
                placed = True
                break
        if not placed:
            bins.append([job])

    for bin_jobs in bins:
        cpu, mem = _serial_pod_request(bin_jobs)
        assert mem <= MAX_POD_MEM_MIB
        assert cpu <= MAX_POD_CPU_MILLI
        specs.append(
            DispatchSpec(
                run_id=run_id,
                members=tuple(bin_jobs),
                cpu_milli=cpu,
                mem_mib=mem,
                dedicated=False,
            )
        )
    return specs


def _serial_pod_request(members: Sequence[PackableJob]) -> tuple[int, int]:
    """Serial execution → request = peak member, not sum."""
    cpu = max((m.estimate.cpu_milli for m in members), default=0)
    mem = max((m.estimate.mem_mib for m in members), default=0)
    cpu = min(cpu, MAX_POD_CPU_MILLI)
    mem = min(mem, MAX_POD_MEM_MIB)
    return cpu, mem


def as_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
