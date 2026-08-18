"""Tests for scan-chunk Job creation (mirrors test_native_batch_and_dispatch.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryo.engines.native.resources import MAX_POD_MEM_MIB
from cryo.scanner.job_dispatch import create_scan_job


def test_create_scan_job_clamps_above_16gi() -> None:
    with patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(None, None)):
        result = create_scan_job(
            {"run_id": "r1", "chunk_id": 0, "num_chunks": 1},
            config={},
            cpu_milli=99999,
            mem_mib=99999,
        )
    assert result.mem_mib <= MAX_POD_MEM_MIB
    assert result.ok is False


def test_create_scan_job_uses_scan_entrypoint_and_payload_secret() -> None:
    batch_api = MagicMock()
    created_manifest: dict = {}

    def _create_job(body, namespace):
        created_manifest.update(body)
        return SimpleNamespace(metadata=SimpleNamespace(uid="job-uid"))

    batch_api.create_namespaced_job.side_effect = _create_job
    core_api = MagicMock()
    with (
        patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(object(), object())),
        patch("cryo.engines.native.job_dispatch._in_cluster_configured", return_value=True),
        patch("cryo.engines.native.job_dispatch._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.native.job_dispatch._new_k8s_core_client", return_value=core_api),
    ):
        result = create_scan_job(
            {"run_id": "r1", "chunk_id": 0, "num_chunks": 1, "_already_claimed": True},
            config={"k8s_namespace": "cryo"},
            cpu_milli=500,
            mem_mib=1024,
            run_id="r1",
        )

    assert result.ok is True
    assert result.job_name is not None
    assert result.job_name.startswith("cryo-scan-")
    container = created_manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "cryo.scanner.job_runner"]
    scan_labels = created_manifest["spec"]["template"]["metadata"]["labels"]
    assert scan_labels["eks-workload-name"] == "cryo-scanner"
    assert scan_labels["cryo-engine"] == "scanner"
    assert scan_labels["cryo-operation"] == "scan"
    assert container["resources"]["requests"]["cpu"] == "500m"
    assert container["resources"]["requests"]["memory"] == "1024Mi"
    core_api.create_namespaced_secret.assert_called_once()


def test_create_scan_job_name_includes_run_id() -> None:
    batch_api = MagicMock()
    batch_api.create_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(uid="job-uid")
    )
    core_api = MagicMock()
    with (
        patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(object(), object())),
        patch("cryo.engines.native.job_dispatch._in_cluster_configured", return_value=True),
        patch("cryo.engines.native.job_dispatch._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.native.job_dispatch._new_k8s_core_client", return_value=core_api),
    ):
        result = create_scan_job(
            {"run_id": "abcdef12-0000-0000-0000-000000000000"},
            config={},
            cpu_milli=250,
            mem_mib=512,
            run_id="abcdef12-0000-0000-0000-000000000000",
        )
    assert result.ok is True
    assert result.job_name is not None
    assert result.job_name.startswith("cryo-scan-abcdef12-")
