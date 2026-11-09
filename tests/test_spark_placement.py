"""Spark pod placement: tolerations, packing affinity, and the dedicated Spark NodePool (#447)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cryo.engines.spark.k8s import SparkK8sClient, SparkK8sConfig, _parse_tolerations

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"


def _wrapper_pod(cfg: SparkK8sConfig) -> dict[str, Any]:
    batch_api = MagicMock()
    with patch("cryo.engines.spark.k8s._new_k8s_batch_client", return_value=batch_api):
        SparkK8sClient(cfg).submit_spark_sql("SELECT 1", catalog="sample")
    return batch_api.create_namespaced_job.call_args.kwargs["body"]["spec"]["template"]["spec"]


def test_parse_tolerations() -> None:
    assert _parse_tolerations("cryo.io/workload=spark:NoSchedule, dedicated:NoExecute") == (
        {"key": "cryo.io/workload", "operator": "Equal", "value": "spark", "effect": "NoSchedule"},
        {"key": "dedicated", "operator": "Exists", "effect": "NoExecute"},
    )
    assert _parse_tolerations("") == ()
    assert _parse_tolerations(None) == ()


def test_default_submit_has_no_pod_templates() -> None:
    pod = _wrapper_pod(SparkK8sConfig(namespace="cryo"))
    command = pod["containers"][0]["command"]
    assert command[0] == "/opt/spark/bin/spark-submit"
    assert not any("podTemplateFile" in arg for arg in command)
    assert "volumes" not in pod and "tolerations" not in pod


def test_tolerations_and_packing_reach_wrapper_driver_and_executors() -> None:
    cfg = SparkK8sConfig.from_config(
        {
            "namespace": "cryo",
            "spark_k8s": {"tolerations": "cryo.io/workload=spark:NoSchedule", "pack_pods": "true"},
        }
    )
    pod = _wrapper_pod(cfg)
    container = pod["containers"][0]
    tolerations = [
        {"key": "cryo.io/workload", "operator": "Equal", "value": "spark", "effect": "NoSchedule"}
    ]
    assert pod["tolerations"] == tolerations

    assert container["command"][:2] == ["/bin/sh", "-c"]
    assert container["command"][4] == "/opt/spark/bin/spark-submit"
    for role in ("driver", "executor"):
        conf = f"spark.kubernetes.{role}.podTemplateFile=/tmp/cryo-pod-templates/{role}.json"
        assert conf in container["command"]
        assert f"$CRYO_{role.upper()}_POD_TEMPLATE" in container["command"][2]

    env = {e["name"]: e["value"] for e in container["env"]}
    template = json.loads(env["CRYO_EXECUTOR_POD_TEMPLATE"])
    assert template["spec"]["tolerations"] == tolerations
    terms = template["spec"]["affinity"]["podAffinity"][
        "preferredDuringSchedulingIgnoredDuringExecution"
    ]
    assert terms[0]["podAffinityTerm"]["matchLabelKeys"] == ["spark-app-selector"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in template["spec"]["affinity"]


def _render(*args: str) -> list[dict[str, Any]]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    result = subprocess.run(
        [
            "helm",
            "template",
            "cryo",
            str(CHART),
            "--set",
            "global.env.CRYO_ENVIRONMENT=dev",
            "--set",
            "spark.k8s.enabled=true",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _worker_env(docs: list[dict[str, Any]]) -> dict[str, str]:
    worker = next(
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "cryo-worker"
    )
    container = worker["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_chart_default_has_no_spark_nodepool_and_keeps_placement() -> None:
    docs = _render()
    assert not [d for d in docs if d["kind"] == "NodePool"]
    env = _worker_env(docs)
    assert env["CRYO_SPARK_K8S_NODE_SELECTOR"] == (
        "karpenter.sh/capacity-type=on-demand,kubernetes.io/arch=amd64"
    )
    assert env["CRYO_SPARK_K8S_TOLERATIONS"] == ""
    assert env["CRYO_SPARK_K8S_PACK_PODS"] == "false"


def test_chart_spark_nodepool_only_reclaims_empty_nodes() -> None:
    docs = _render(
        "--set",
        "spark.k8s.karpenter.installNodePool=true",
        "--set",
        "spark.k8s.karpenter.nodeClassName=warehouse-access",
    )
    pool = next(d for d in docs if d["kind"] == "NodePool")
    assert pool["spec"]["disruption"]["consolidationPolicy"] == "WhenEmpty"
    assert pool["spec"]["disruption"]["budgets"] == [{"nodes": "20%"}]
    assert pool["spec"]["template"]["spec"]["nodeClassRef"]["name"] == "warehouse-access"
    assert pool["spec"]["template"]["spec"]["taints"] == [
        {"key": "cryo.io/workload", "value": "spark", "effect": "NoSchedule"}
    ]
    env = _worker_env(docs)
    assert env["CRYO_SPARK_K8S_NODE_SELECTOR"].endswith(",cryo.io/workload=spark")
    assert env["CRYO_SPARK_K8S_TOLERATIONS"] == "cryo.io/workload=spark:NoSchedule"
    assert env["CRYO_SPARK_K8S_PACK_PODS"] == "true"


def test_chart_spark_nodepool_requires_a_node_class() -> None:
    with pytest.raises(subprocess.CalledProcessError) as err:
        _render("--set", "spark.k8s.karpenter.installNodePool=true")
    assert "nodeClassName is required" in err.value.stderr


def test_spot_executors_keep_the_driver_on_demand() -> None:
    cfg = SparkK8sConfig.from_config(
        {
            "namespace": "cryo",
            "spark_k8s": {
                "node_selector": "karpenter.sh/capacity-type=on-demand",
                "executor_capacity_types": "spot,on-demand",
            },
        }
    )
    pod = _wrapper_pod(cfg)
    command = pod["containers"][0]["command"]
    assert "spark.kubernetes.driver.node.selector.karpenter.sh/capacity-type=on-demand" in command
    assert not any(c.startswith("spark.kubernetes.node.selector.karpenter.sh/") for c in command)
    assert "spark.kubernetes.node.selector.kubernetes.io/arch=amd64" in command
    for conf in (
        "spark.decommission.enabled=true",
        "spark.storage.decommission.shuffleBlocks.enabled=true",
    ):
        assert conf in command

    env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
    executor = json.loads(env["CRYO_EXECUTOR_POD_TEMPLATE"])["spec"]["affinity"]["nodeAffinity"]
    required = executor["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert required[0]["matchExpressions"][0]["values"] == ["spot", "on-demand"]
    preferred = executor["preferredDuringSchedulingIgnoredDuringExecution"][0]["preference"]
    assert preferred["matchExpressions"][0]["values"] == ["spot"]
    driver = json.loads(env["CRYO_DRIVER_POD_TEMPLATE"])["spec"]
    assert "affinity" not in driver


def test_on_demand_executor_types_skip_decommission() -> None:
    cfg = SparkK8sConfig.from_config(
        {"namespace": "cryo", "spark_k8s": {"executor_capacity_types": "on-demand"}}
    )
    command = _wrapper_pod(cfg)["containers"][0]["command"]
    assert not any("decommission" in c for c in command)
