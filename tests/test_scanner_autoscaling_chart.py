"""Exercise scaler ownership and the service referenced by the rendered KEDA trigger."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"


def render(*args: str) -> list[dict]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    result = subprocess.run(
        ["helm", "template", "cryo", str(CHART), "-f", str(CHART / "values-prod.yaml"), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@pytest.mark.parametrize("hpa,keda", [(False, False), (True, False), (False, True), (True, True)])
def test_one_controller_owns_scanner_replicas(hpa: bool, keda: bool) -> None:
    docs = render(
        "--set",
        f"scannerWorker.autoscaling.enabled={str(hpa).lower()}",
        "--set",
        f"scannerWorker.autoscaling.keda.enabled={str(keda).lower()}",
    )
    scanner = [d for d in docs if d["metadata"]["name"] == "cryo-scanner-worker"]
    deployment = next(d for d in scanner if d["kind"] == "Deployment")
    controllers = [
        d["kind"] for d in scanner if d["kind"] in {"HorizontalPodAutoscaler", "ScaledObject"}
    ]
    assert controllers == (["ScaledObject"] if keda else ["HorizontalPodAutoscaler"] if hpa else [])
    assert ("replicas" in deployment["spec"]) == (not hpa and not keda)


def test_production_scanner_scales_to_zero_and_scale_down_is_bounded() -> None:
    docs = render("-f", str(CHART / "values-prod.yaml"))
    scanner = {d["kind"]: d for d in docs if d["metadata"]["name"] == "cryo-scanner-worker"}
    assert "HorizontalPodAutoscaler" not in scanner
    deployment = scanner["Deployment"]
    assert "replicas" not in deployment["spec"]
    spec = scanner["ScaledObject"]["spec"]
    assert (spec["minReplicaCount"], spec["maxReplicaCount"]) == (0, 8)
    assert spec["triggers"][0]["metadata"]["targetValue"] == "1"
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "3Gi"}
    behavior = spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]
    assert behavior["stabilizationWindowSeconds"] == 300
    assert behavior["policies"] == [{"type": "Pods", "value": 1, "periodSeconds": 60}]


def test_scanner_trigger_polls_a_service_that_survives_scaling_to_zero() -> None:
    """The trigger must not point at the Deployment it scales.

    At minReplicaCount 0 the scanner-worker Service has no endpoints, so a self-referential
    trigger is unreachable exactly when it needs to say "work arrived, scale up" -- the
    Deployment would never leave zero. Assert the URL resolves to the API Service instead,
    and that the API is not itself an autoscaled-to-zero target.
    """
    docs = render("-f", str(CHART / "values-prod.yaml"))
    by_name_kind = {(d["metadata"]["name"], d["kind"]): d for d in docs}
    trigger = by_name_kind[("cryo-scanner-worker", "ScaledObject")]["spec"]["triggers"][0]
    url = trigger["metadata"]["url"]

    api_service = by_name_kind[("cryo-api", "Service")]
    port = api_service["spec"]["ports"][0]
    assert url == f"http://cryo-api.default.svc.cluster.local:{port['port']}/scale-metric"
    assert trigger["metadata"]["valueLocation"] == "value"

    api_deployment = by_name_kind[("cryo-api", "Deployment")]
    assert api_deployment["spec"]["replicas"] >= 1
    api_labels = api_deployment["spec"]["template"]["metadata"]["labels"]
    assert all(api_labels[k] == v for k, v in api_service["spec"]["selector"].items())
    api_container = api_deployment["spec"]["template"]["spec"]["containers"][0]
    assert any(p["containerPort"] == port["port"] for p in api_container["ports"])


def test_a_zero_floor_is_not_swallowed_by_a_template_default() -> None:
    """Go templates treat 0 as empty, so `minReplicas | default 3` would turn scale-to-zero
    back into three always-on pods with no error anywhere. Pin the rendered value."""
    docs = render(
        "--set",
        "scannerWorker.autoscaling.keda.enabled=true",
        "--set",
        "scannerWorker.autoscaling.keda.minReplicas=0",
    )
    scaled = next(
        d
        for d in docs
        if d["kind"] == "ScaledObject" and d["metadata"]["name"] == "cryo-scanner-worker"
    )
    assert scaled["spec"]["minReplicaCount"] == 0
