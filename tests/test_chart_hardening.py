"""Chart hardening (#261): daemon probes, opt-in HPA memory, PDB, NetworkPolicy, Ingress."""

from __future__ import annotations

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
        ["helm", "template", "cryo", str(CHART), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


def _deployment(docs: list[dict], name: str) -> dict:
    return next(d for d in _kind(docs, "Deployment") if d["metadata"]["name"] == name)


def _container(docs: list[dict], name: str) -> dict:
    return _deployment(docs, name)["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize(
    ("deployment", "stall"),
    [("cryo-scheduler", "3600"), ("cryo-worker", "0"), ("cryo-scanner-worker", "3600")],
)
def test_daemons_have_healthz_probes_and_stall_env(deployment: str, stall: str) -> None:
    container = _container(render(), deployment)
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {"path": "/healthz", "port": 8081}
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["CRYO_DAEMON_TICK_STALL_SECONDS"] == stall
    assert env["CRYO_HEALTH_PORT"] == "8081"


def test_daemon_probes_can_be_turned_off() -> None:
    container = _container(render("--set", "scheduler.probe.enabled=false"), "cryo-scheduler")
    assert "readinessProbe" not in container
    assert "livenessProbe" not in container


def test_scanner_hpa_scales_on_cpu_only_by_default() -> None:
    hpa = _kind(render(), "HorizontalPodAutoscaler")[0]
    assert [m["resource"]["name"] for m in hpa["spec"]["metrics"]] == ["cpu"]


def test_scanner_hpa_memory_target_is_opt_in() -> None:
    docs = render("--set", "scannerWorker.autoscaling.targetMemoryUtilizationPercentage=90")
    metrics = _kind(docs, "HorizontalPodAutoscaler")[0]["spec"]["metrics"]
    memory = next(m for m in metrics if m["resource"]["name"] == "memory")
    assert memory["resource"]["target"]["averageUtilization"] == 90


def test_hardening_objects_are_off_by_default() -> None:
    docs = render()
    for kind in ("PodDisruptionBudget", "NetworkPolicy", "Ingress"):
        assert not _kind(docs, kind), kind


def test_pdbs_cover_rendered_components() -> None:
    docs = render("--set", "podDisruptionBudget.enabled=true")
    names = {d["metadata"]["name"] for d in _kind(docs, "PodDisruptionBudget")}
    # web is not rendered by default, so neither is its PDB.
    assert names == {
        "cryo-api",
        "cryo-worker",
        "cryo-scanner-worker",
        "cryo-governor",
    }
    pdb = next(d for d in _kind(docs, "PodDisruptionBudget") if d["metadata"]["name"] == "cryo-api")
    assert pdb["spec"]["maxUnavailable"] == 1
    assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/component"] == "api"
    web_docs = render("--set", "podDisruptionBudget.enabled=true", "--set", "web.enabled=true")
    assert "cryo-web" in {d["metadata"]["name"] for d in _kind(web_docs, "PodDisruptionBudget")}


def test_network_policy_locks_redis_to_the_release() -> None:
    docs = render("--set", "networkPolicy.enabled=true")
    policies = {d["metadata"]["name"]: d for d in _kind(docs, "NetworkPolicy")}
    # No api.from and no egress rules: only the Redis policy renders.
    assert set(policies) == {"cryo-redis"}
    rule = policies["cryo-redis"]["spec"]["ingress"][0]
    release = {"app.kubernetes.io/name": "cryo", "app.kubernetes.io/instance": "cryo"}
    assert rule["from"] == [{"podSelector": {"matchLabels": release}}]
    assert rule["ports"] == [{"port": 6379, "protocol": "TCP"}]


def test_network_policy_api_and_egress() -> None:
    docs = render(
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "networkPolicy.api.from[0].namespaceSelector.matchLabels.team=edge",
        "--set",
        "networkPolicy.egress[0].ports[0].port=5432",
    )
    policies = {d["metadata"]["name"]: d for d in _kind(docs, "NetworkPolicy")}
    assert set(policies) == {"cryo-redis", "cryo-api", "cryo-egress"}
    api_from = policies["cryo-api"]["spec"]["ingress"][0]["from"]
    assert {"namespaceSelector": {"matchLabels": {"team": "edge"}}} in api_from
    egress = policies["cryo-egress"]["spec"]["egress"]
    assert egress[0]["ports"][0]["port"] == 53
    assert {"ports": [{"port": 5432}]} in egress


def test_ingress_routes_to_web_with_tls_and_sse_annotations() -> None:
    docs = render(
        "--set",
        "ingress.enabled=true",
        "--set",
        "web.enabled=true",
        "--set",
        "ingress.className=nginx",
        "--set",
        "ingress.tls[0].secretName=cryo-tls",
        "--set",
        "ingress.tls[0].hosts[0]=cryo.example.com",
    )
    ingress = _kind(docs, "Ingress")[0]
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert ingress["spec"]["tls"] == [{"secretName": "cryo-tls", "hosts": ["cryo.example.com"]}]
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "cryo-web"
    annotations = ingress["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"


def test_ingress_routes_to_api_without_web() -> None:
    ingress = _kind(render("--set", "ingress.enabled=true"), "Ingress")[0]
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "cryo-api", "port": {"number": 8000}}
