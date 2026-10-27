"""ExternalSecret support and IRSA annotations on the component ServiceAccounts.

Both exist because an install that let the chart drop them lost its database Secret and
its S3 role in one sync: with target.creationPolicy=Owner, removing the ExternalSecret
deletes the Secret every pod reads, and the API runs under the -native ServiceAccount, not
the base one, so that SA's annotation is where its S3 role lives.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"
ROLE = "arn:aws:iam::123456789012:role/cryo"
IRSA = "eks\\.amazonaws\\.com/role-arn"

ESO = [
    "--set", "global.envFromSecret=cryo-env",
    "--set", "externalSecret.enabled=true",
    "--set", "externalSecret.secretStore.region=us-east-1",
    "--set", "externalSecret.data[0].secretKey=CRYO_DATABASE_URL",
    "--set", "externalSecret.data[0].remoteRef.key=cryo/prod",
    "--set", "externalSecret.data[0].remoteRef.property=CRYO_DATABASE_URL",
    "--set-string", f"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn={ROLE}",
]  # fmt: skip


def without(prefix: str) -> list[str]:
    """ESO minus one --set flag and its value."""
    args: list[str] = []
    for flag, value in zip(ESO[::2], ESO[1::2], strict=True):
        if not value.startswith(prefix):
            args += [flag, value]
    return args


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    return subprocess.run(
        ["helm", "template", "cryo", str(CHART), "--set", "global.env.CRYO_ENVIRONMENT=dev", *args],
        capture_output=True,
        text=True,
    )


def render(*args: str) -> dict[str, dict]:
    result = _helm(*args)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def test_external_secret_is_off_by_default() -> None:
    docs = render()
    assert not [k for k in docs if k.startswith(("ExternalSecret/", "SecretStore/"))]


def test_external_secret_targets_the_env_secret_through_the_chart_store() -> None:
    docs = render(*ESO)
    store = docs["SecretStore/cryo-secret-store"]
    aws = store["spec"]["provider"]["aws"]
    assert aws["region"] == "us-east-1"
    assert aws["auth"]["jwt"]["serviceAccountRef"]["name"] == "cryo"

    external = docs["ExternalSecret/cryo-external-secret"]["spec"]
    assert external["secretStoreRef"] == {"name": "cryo-secret-store", "kind": "SecretStore"}
    assert external["target"]["name"] == "cryo-env"
    assert external["data"] == [
        {
            "secretKey": "CRYO_DATABASE_URL",
            "remoteRef": {"key": "cryo/prod", "property": "CRYO_DATABASE_URL"},
        }
    ]


def test_existing_store_skips_the_store_and_the_region_check() -> None:
    docs = render(
        *without("externalSecret.secretStore.region"),
        "--set", "externalSecret.secretStore.create=false",
        "--set", "externalSecret.secretStore.name=shared-store",
    )  # fmt: skip
    assert not [k for k in docs if k.startswith("SecretStore/")]
    ref = docs["ExternalSecret/cryo-external-secret"]["spec"]["secretStoreRef"]
    assert ref["name"] == "shared-store"


@pytest.mark.parametrize(
    ("drop", "message"),
    [
        ("externalSecret.secretStore.region", "secretStore.region must be set"),
        ("global.envFromSecret", "global.envFromSecret must be set"),
        ("externalSecret.data[0].secretKey", "CRYO_DATABASE_URL mapping"),
        ("serviceAccount.annotations", "role-arn must be set"),
    ],
)
def test_external_secret_refuses_an_incomplete_config(drop: str, message: str) -> None:
    result = _helm(*without(drop))
    assert result.returncode != 0
    assert message in result.stderr


def test_component_service_accounts_carry_their_own_annotations() -> None:
    docs = render(
        "--set", "native.enabled=true",
        "--set", "spark.k8s.enabled=true",
        "--set", "analysis.enabled=true",
        "--set-string", f"native.serviceAccountAnnotations.{IRSA}={ROLE}",
        "--set-string", f"spark.k8s.serviceAccountAnnotations.{IRSA}={ROLE}",
        "--set-string", f"analysis.serviceAccountAnnotations.{IRSA}={ROLE}",
        "--set-string", f"governor.serviceAccount.annotations.{IRSA}={ROLE}",
        "--set", "imagePullSecrets[0]=regcred",
    )  # fmt: skip
    for name in ("cryo-native", "cryo-spark", "cryo-analysis", "cryo-governor"):
        sa = docs[f"ServiceAccount/{name}"]
        assert sa["metadata"]["annotations"] == {"eks.amazonaws.com/role-arn": ROLE}, name
        assert sa["imagePullSecrets"] == [{"name": "regcred"}], name


def test_component_service_accounts_have_no_annotations_by_default() -> None:
    docs = render("--set", "native.enabled=true", "--set", "spark.k8s.enabled=true")
    for name in ("cryo-native", "cryo-spark", "cryo-governor"):
        assert "annotations" not in docs[f"ServiceAccount/{name}"]["metadata"], name


def test_redis_listens_on_the_configured_port() -> None:
    redis = render("--set", "redis.port=6380")["Deployment/cryo-redis"]
    args = redis["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[args.index("--port") + 1] == "6380"


def test_maintenance_runs_as_native_when_only_spark_k8s_is_on() -> None:
    """-native holds the Job/Secret bindings; it exists with native or spark.k8s on."""
    docs = render("--set", "maintenance.enabled=true", "--set", "spark.k8s.enabled=true")
    cron = next(d for k, d in docs.items() if k.startswith("CronJob/") and "maintenance" in k)
    assert cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["serviceAccountName"] == (
        "cryo-native"
    )
