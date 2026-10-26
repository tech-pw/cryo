"""Every Cryo workload must run under an explicit ServiceAccount.

Falling through to `default` is silent and expensive: on EKS the pod then has no IRSA
role, so Cryo authenticates to S3 as the node instance role. Onboarding still reports a
healthy catalog connection -- the failure only surfaces later, as AccessDenied on scans.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"

# Workloads that talk to S3 or the Kubernetes API, so an unset ServiceAccount is a real
# defect rather than cosmetic. The web pod is static nginx and is deliberately excluded.
WORKLOADS = {"cryo-api", "cryo-governor", "cryo-scheduler", "cryo-worker"}


def render(*args: str) -> list[dict]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    result = subprocess.run(
        ["helm", "template", "cryo", str(CHART), "--set", "global.env.CRYO_ENVIRONMENT=dev", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _deployments(docs: list[dict]) -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d
        for d in docs
        if d.get("kind") == "Deployment" and d["metadata"]["name"] in WORKLOADS
    }


def test_workloads_get_an_explicit_service_account_by_default() -> None:
    """With every optional feature off -- the default install -- pods still name an SA."""
    docs = render()
    deployments = _deployments(docs)
    assert WORKLOADS <= set(deployments), f"missing workloads: {WORKLOADS - set(deployments)}"

    for name, deployment in deployments.items():
        account = deployment["spec"]["template"]["spec"].get("serviceAccountName")
        assert account, f"{name} has no serviceAccountName, so it falls back to `default`"
        assert account == "cryo", name


def test_chart_creates_that_service_account() -> None:
    """The name the pods reference must actually be rendered, or they get `default`."""
    accounts = {d["metadata"]["name"] for d in render() if d.get("kind") == "ServiceAccount"}
    assert "cryo" in accounts


def test_irsa_annotation_lands_on_the_service_account() -> None:
    """The documented production path for S3 access."""
    arn = "arn:aws:iam::111122223333:role/example-cryo-role"
    docs = render("--set", f"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={arn}")
    account = next(d for d in docs if d.get("kind") == "ServiceAccount")
    assert account["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == arn


def test_privileged_service_accounts_still_win_when_dispatch_is_enabled() -> None:
    """Enabling native/spark must not regress the Job-dispatch RBAC those paths need."""
    docs = render("--set", "native.enabled=true", "--set", "spark.k8s.enabled=true")
    deployments = _deployments(docs)

    assert deployments["cryo-governor"]["spec"]["template"]["spec"]["serviceAccountName"] == (
        "cryo-governor"
    )
    for name in ("cryo-api", "cryo-worker", "cryo-scheduler"):
        assert deployments[name]["spec"]["template"]["spec"]["serviceAccountName"] == (
            "cryo-native"
        ), name


def test_an_externally_managed_service_account_can_be_reused() -> None:
    """create=false + name= lets a cluster keep owning the SA (and its IRSA binding)."""
    docs = render(
        "--set", "serviceAccount.create=false", "--set", "serviceAccount.name=platform-cryo"
    )
    assert not [d for d in docs if d.get("kind") == "ServiceAccount"]
    for deployment in _deployments(docs).values():
        assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "platform-cryo"
