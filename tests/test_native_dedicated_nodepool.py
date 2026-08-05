"""Tests for the opt-in cryo.io/workload=native-engine dedicated NodePool scheduling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryo.engines.native.job_dispatch import create_native_job


def _create_with_config(config: dict) -> dict:
    batch_api = MagicMock()
    created: dict = {}

    def _create_job(body, namespace):
        created.update(body)
        return SimpleNamespace(metadata=SimpleNamespace(uid="job-uid"))

    batch_api.create_namespaced_job.side_effect = _create_job
    core_api = MagicMock()
    with (
        patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(object(), object())),
        patch("cryo.engines.native.job_dispatch._in_cluster_configured", return_value=True),
        patch("cryo.engines.native.job_dispatch._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.native.job_dispatch._new_k8s_core_client", return_value=core_api),
    ):
        result = create_native_job(
            {"table_ref": "a.b"},
            config=config,
            cpu_milli=500,
            mem_mib=1024,
        )
    assert result.ok is True
    return created


def test_default_scheduling_has_no_dedicated_nodepool_label() -> None:
    manifest = _create_with_config({})
    pod_spec = manifest["spec"]["template"]["spec"]
    assert "cryo.io/workload" not in pod_spec["nodeSelector"]
    assert not any(t.get("key") == "cryo.io/workload" for t in pod_spec["tolerations"])


def test_dedicated_nodepool_opt_in_adds_label_and_toleration() -> None:
    manifest = _create_with_config({"k8s_dedicated_nodepool": True})
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"]["cryo.io/workload"] == "native-engine"
    assert any(
        t
        == {
            "key": "cryo.io/workload",
            "operator": "Equal",
            "value": "native-engine",
            "effect": "NoSchedule",
        }
        for t in pod_spec["tolerations"]
    )


def test_dedicated_nodepool_env_var_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("CRYO_NATIVE_DEDICATED_NODEPOOL", "true")
    manifest = _create_with_config({})
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"]["cryo.io/workload"] == "native-engine"
