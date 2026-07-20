"""Unit tests for Governor native pack-on-admit helpers."""

from __future__ import annotations

from cryo.domain.models import CapacitySnapshot
from cryo.engines.native.resources import MAX_POD_MEM_MIB
from cryo.governor.native_admit import cluster_headroom, destination_uses_k8s_dispatch
from cryo.worker.worker import DestinationConfig


def test_destination_uses_k8s_dispatch() -> None:
    assert not destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="native", config={})
    )
    assert destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="native", config={"k8s_dispatch": True})
    )
    assert not destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="trino", config={"k8s_dispatch": True})
    )


def test_cluster_headroom() -> None:
    live = {
        "default": CapacitySnapshot(
            capacity=5,
            used=2,
            cpu_used_milli=2000,
            mem_used_mib=4096,
        )
    }
    slots, cpu, mem = cluster_headroom(
        live,
        config={
            "slot_cap": 5,
            "max_cluster_cpu_milli": 20000,
            "max_cluster_mem_mib": 5 * MAX_POD_MEM_MIB,
        },
    )
    assert slots == 3
    assert cpu == 18000
    assert mem == 5 * MAX_POD_MEM_MIB - 4096
