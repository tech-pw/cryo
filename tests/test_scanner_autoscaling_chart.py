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


def test_production_scanner_metric_is_reachable_and_scale_down_is_bounded() -> None:
    docs = render("-f", str(CHART / "values-prod.yaml"))
    scanner = {d["kind"]: d for d in docs if d["metadata"]["name"] == "cryo-scanner-worker"}
    assert "HorizontalPodAutoscaler" not in scanner
    deployment = scanner["Deployment"]
    assert "replicas" not in deployment["spec"]
    spec = scanner["ScaledObject"]["spec"]
    assert (spec["minReplicaCount"], spec["maxReplicaCount"]) == (1, 8)
    assert spec["triggers"][0]["metadata"]["targetValue"] == "1"
    service = scanner["Service"]
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert all(labels[k] == v for k, v in service["spec"]["selector"].items())
    port = service["spec"]["ports"][0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert any(p["name"] == port["targetPort"] for p in container["ports"])
    url = spec["triggers"][0]["metadata"]["url"]
    assert f"cryo-scanner-worker.default.svc.cluster.local:{port['port']}/scale-metric" in url
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "3Gi"}
    behavior = spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]
    assert behavior["stabilizationWindowSeconds"] == 300
    assert behavior["policies"] == [{"type": "Pods", "value": 1, "periodSeconds": 60}]
