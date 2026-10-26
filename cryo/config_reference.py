"""Generate the docs-site configuration reference from the code that reads configuration.

Two includes are written for ``site/_src/pages/configuration.html``:

* every ``CRYO_*`` environment variable: each ``Settings`` field, plus the handful of
  variables read straight from ``os.environ`` outside ``Settings``;
* every top-level Helm value in ``deploy/helm/cryo/values.yaml`` and its direct children.

``--check`` fails when the committed includes are stale, when code reads a ``CRYO_*``
variable outside ``Settings`` that has no entry in ``EXTRA_ENV``, when a new ``Settings``
field has no description, or when a new top-level Helm value has no entry in
``HELM_DESCRIPTIONS``.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_core import PydanticUndefined

from cryo.config import Settings

ROOT = Path(__file__).resolve().parents[1]
ENV_OUTPUT = ROOT / "site" / "_src" / "includes" / "configuration-env.html"
HELM_OUTPUT = ROOT / "site" / "_src" / "includes" / "configuration-helm.html"
VALUES_FILE = ROOT / "deploy" / "helm" / "cryo" / "values.yaml"
CODE_ROOTS = (ROOT / "cryo", ROOT / "cryo_spark_runner.py")

ENV_PREFIX = "CRYO_"
MAX_DESCRIPTION = 320

# Descriptions for Settings fields that have no Field(description=...). Prefer adding the
# description in cryo/config.py; this is for the core settings whose meaning is fixed.
FIELD_DESCRIPTIONS: dict[str, str] = {
    "app_name": "Display name used in API metadata.",
    "environment": (
        "Deployment environment: dev, test, staging or prod. prod turns off the dev token "
        "route, and the Helm chart requires every enabled image to be pinned by digest."
    ),
    "database_url": "SQLAlchemy URL of the PostgreSQL database that holds the cryo schema.",
    "redis_url": "URL of the Redis used for the job queue, leases and short-lived markers.",
    "secret_key": (
        "Signs Cryo's HS256 tokens when CRYO_JWT_SECRET is unset, and derives the key that "
        "encrypts stored connection secrets."
    ),
    "cors_allow_origins": (
        "Origins allowed to call the API from a browser, as a JSON list. Not needed when the "
        "UI is served through the web pod's /api proxy."
    ),
    "scan_num_chunks": "Number of chunks a fleet scan is split into.",
    "maintenance_lane_max_cpu_milli": (
        "Upper bound on one batch-maintenance lane pod's CPU, in millicores."
    ),
    "maintenance_lane_max_mem_mib": (
        "Upper bound on one batch-maintenance lane pod's memory, in MiB."
    ),
    "maintenance_autocomp_w_file_reduction": (
        "Weight of file-count reduction in the AutoComp table ranking. The compute-cost "
        "weight is one minus this value."
    ),
    "offpeak_start_ist": (
        "Start hour (IST) of the off-peak window. Equal start and end disables the gate."
    ),
    "offpeak_end_ist": (
        "End hour (IST) of the off-peak window. Equal start and end disables the gate."
    ),
    "smtp_port": "SMTP server port for email notifications.",
    "smtp_user": "SMTP user name.",
    "smtp_password": "SMTP password.",
    "smtp_use_tls": "Use STARTTLS for SMTP.",
}

# Settings fields that predate the description requirement. Do not add to this list: give a
# new field a Field(description=...) instead.
UNDESCRIBED_BASELINE: frozenset[str] = frozenset(
    {
        "scan_small_file_mb",
        "scan_needs_compaction_threshold",
        "maintenance_cooldown_hours",
        "maintenance_poll_seconds",
        "maintenance_rescan_enabled",
        "maintenance_slack_enabled",
        "scan_dead_table_lookback_days",
        "cost_price_per_gb_month",
        "cost_use_gib",
        "cost_scan_tier",
        "cost_scan_max_snapshots",
        "cost_scan_max_manifests",
        "cost_full_scan_every_hours",
        "cost_orphan_min_age_hours",
        "cost_scan_max_list_keys",
        "default_retention_days",
        "default_retain_last",
        "s3_get_usd_per_1k",
        "s3_list_usd_per_1k",
        "s3_put_usd_per_1k",
        "query_gets_per_file",
        "default_per_replica_optimize_cap",
        "default_emr_slot_cap",
        "default_native_slot_cap",
        "wave_wait_sec",
        "assistant_max_tool_calls",
        "assistant_max_parallel_reads",
        "assistant_turn_timeout_s",
        "assistant_proposal_ttl_s",
        "assistant_max_active_turns_per_user",
        "assistant_retention_days",
    }
)

SECURITY_NOTES: dict[str, str] = {
    "environment": "Set prod for any install other people can reach.",
    "secret_key": (
        "The default is published. Set a random value of at least 32 characters, and back it "
        "up: changing it makes stored secrets unreadable."
    ),
    "jwt_secret": "Set a separate random value so token signing can be rotated on its own.",
    "allow_dev_token_in_prod": "Re-opens the unauthenticated dev token route. Leave unset.",
    "oidc_client_secret": "Keep in the Kubernetes Secret.",
    "database_url": "Contains the database password. Keep in the Kubernetes Secret.",
    "redis_url": "Keep in the Kubernetes Secret if it carries a password.",
    "smtp_password": "Keep in the Kubernetes Secret.",
    "llm_allowed_origins": "Each listed origin can receive AI provider requests. Keep it short.",
    "cors_allow_origins": "Never list *.",
}

# Settings a production install sets explicitly.
PRODUCTION: frozenset[str] = frozenset(
    {
        "environment",
        "database_url",
        "redis_url",
        "secret_key",
        "jwt_secret",
        "oidc_issuer_url",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_redirect_uri",
    }
)

# CRYO_* variables read with os.environ / os.getenv outside Settings.
EXTRA_ENV: dict[str, str] = {
    "CRYO_ANALYSIS_IMAGE": "Image for analysis Jobs, when the caller passes none.",
    "CRYO_ANALYSIS_IMAGE_PULL_POLICY": "Pull policy for analysis Jobs. Default IfNotPresent.",
    "CRYO_ANALYSIS_SERVICE_ACCOUNT": "ServiceAccount for analysis Jobs. Defaults to default.",
    "CRYO_BACKUP_KEY": (
        "Required by python -m cryo.operations.backup: 32 random bytes, URL-safe base64. "
        "Encrypts and decrypts backups. Store it apart from the backups."
    ),
    "CRYO_ENGINE_BINARY": "Path to the native engine binary, overriding the one on PATH.",
    "CRYO_GRAFANA_TOKEN": "Grafana API token for the daily and health-check reports.",
    "CRYO_K8S_NAMESPACE": "Namespace for native Jobs when POD_NAMESPACE is unset.",
    "CRYO_NATIVE_IMAGE": "Image for native and analysis Jobs, when the destination sets none.",
    "CRYO_NATIVE_IMAGE_PULL_POLICY": "Pull policy for native Jobs, when the destination sets none.",
    "CRYO_NATIVE_IMAGE_PULL_SECRETS": (
        "Pull secrets for native Jobs, when the destination sets none."
    ),
    "CRYO_NATIVE_SERVICE_ACCOUNT": (
        "ServiceAccount for native Jobs, when the destination sets none."
    ),
    "CRYO_NATIVE_TTL_SECONDS": (
        "ttlSecondsAfterFinished for native Jobs, when the destination sets none."
    ),
    "CRYO_PAYLOAD_B64": "Internal. Job payload passed by Cryo to its own Job pods.",
    "CRYO_PAYLOAD_FILE": "Internal. Path of the Job payload Cryo mounts into its own Job pods.",
    "CRYO_SCANNER_JOB_DISPATCH_BATCH_SIZE": (
        "Tables per scanner Job when scans dispatch as Jobs. Default 1."
    ),
    "CRYO_SPARK_DRIVER_POD_CLEANUP_TTL_SECONDS": (
        "Age after which finished Spark driver pods are deleted. Default 3600."
    ),
    "CRYO_SPARK_K8S_SLOT_CAP": "Override for the Spark-on-Kubernetes slot cap.",
    "CRYO_STATE_DIR": "Directory for node-local state files. Default /tmp/cryo.",
}

HELM_DESCRIPTIONS: dict[str, str] = {
    "image": "The Cryo image for the API, workers, governor, scheduler, scanner and CronJobs.",
    "imagePullSecrets": "Pull secrets added to every pod.",
    "serviceAccount": "The ServiceAccount Cryo's pods run as. Annotate it for IRSA.",
    "podSecurityContext": "Pod security context applied to every Cryo pod.",
    "containerSecurityContext": "Container security context applied to every Cryo container.",
    "global": (
        "Environment for every process: env (plain values), envFromSecret and envFromConfigMap."
    ),
    "observability": "Prometheus scrape annotations and the metrics port.",
    "api": (
        "The API Deployment: replicas, Uvicorn workers, probes, resources and Ingress annotations."
    ),
    "worker": "The maintenance worker Deployment.",
    "governor": "The admission governor Deployment (leader-elected).",
    "scheduler": "The cron scheduler Deployment (leader-elected).",
    "scannerWorker": "The ad-hoc scan consumer Deployment and its HPA or KEDA autoscaling.",
    "scanner": "The fleet-scan CronJob: schedule, chunks, and Job dispatch.",
    "maintenance": "The nightly batch-maintenance CronJob and its Spark lanes. Off by default.",
    "backup": "The encrypted offsite backup CronJob. Off by default.",
    "native": "Native engine Jobs, their RBAC, KEDA and Karpenter settings. Off by default.",
    "analysis": "Analysis-runner RBAC for scheduler-dispatched analysis Jobs. Off by default.",
    "spark": "Cryo-managed Spark on Kubernetes (spark.k8s). Off by default.",
    "redis": "A single in-namespace Redis with no volume. Set CRYO_REDIS_URL yourself.",
    "migration": "The pre-install/pre-upgrade migration Job image and retries.",
    "web": "The UI Deployment and Service. Off by default.",
    "service": "The API Service type and port.",
}

_ENV_READ = re.compile(
    r"""(?:os\.environ\.get|os\.getenv|os\.environ\[|environ\.get)\(?\s*["'](CRYO_[A-Z0-9_]+)["']"""
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(`])")


@dataclass(frozen=True)
class EnvRow:
    name: str
    default: str
    description: str
    security: str
    production: bool


def _format_default(value: Any) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return json.dumps(items if not isinstance(items, tuple) else list(items))
    return str(value)


def _summarize(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_DESCRIPTION:
        return text
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    if len(first) <= MAX_DESCRIPTION:
        return first
    cut = first[:MAX_DESCRIPTION].rsplit(" ", 1)[0].rstrip(",;:")
    return f"{cut}…"


def settings_rows() -> list[EnvRow]:
    rows: list[EnvRow] = []
    for name, field in Settings.model_fields.items():
        if field.default is PydanticUndefined and field.default_factory is None:
            default = "required"
        else:
            default = _format_default(field.get_default(call_default_factory=True))
        description = field.description or FIELD_DESCRIPTIONS.get(name, "")
        rows.append(
            EnvRow(
                name=f"{ENV_PREFIX}{name.upper()}",
                default=default,
                description=_summarize(description) if description else "",
                security=SECURITY_NOTES.get(name, ""),
                production=name in PRODUCTION,
            )
        )
    return sorted(rows, key=lambda row: row.name)


def env_reads_outside_settings() -> set[str]:
    settings_names = {f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields}
    found: set[str] = set()
    for root in CODE_ROOTS:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            for match in _ENV_READ.finditer(path.read_text(encoding="utf-8")):
                found.add(match.group(1))
    return found - settings_names


def helm_values(text: str) -> list[tuple[str, list[str]]]:
    """Top-level keys of values.yaml, in file order, with their direct child keys."""
    keys: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = re.match(r"^([A-Za-z][\w-]*):", line)
        if top:
            keys.append((top.group(1), []))
            continue
        child = re.match(r"^  ([A-Za-z][\w.-]*):", line)
        if child and keys:
            keys[-1][1].append(child.group(1))
    return keys


def problems() -> list[str]:
    out: list[str] = []
    read = env_reads_outside_settings()
    for name in sorted(read - set(EXTRA_ENV)):
        out.append(f"{name} is read outside Settings; describe it in EXTRA_ENV")
    for name in sorted(set(EXTRA_ENV) - read):
        out.append(f"{name} is in EXTRA_ENV but no longer read; remove it")
    for name, field in Settings.model_fields.items():
        if field.description or name in FIELD_DESCRIPTIONS or name in UNDESCRIBED_BASELINE:
            continue
        out.append(f"Settings.{name} has no description; add Field(description=...)")
    for key, _children in helm_values(VALUES_FILE.read_text(encoding="utf-8")):
        if key not in HELM_DESCRIPTIONS:
            out.append(f"Helm value {key} has no entry in HELM_DESCRIPTIONS")
    return out


def _group(name: str) -> str:
    return name.removeprefix(ENV_PREFIX).split("_", 1)[0].lower()


def render_env() -> str:
    rows = settings_rows()
    counts: dict[str, int] = {}
    for row in rows:
        counts[_group(row.name)] = counts.get(_group(row.name), 0) + 1
    groups: dict[str, list[EnvRow]] = {}
    for row in rows:
        group = _group(row.name)
        groups.setdefault(group if counts[group] >= 3 else "other", []).append(row)
    order = sorted(g for g in groups if g != "other") + (["other"] if "other" in groups else [])

    esc = html.escape
    parts = [
        "<!-- Generated by `python -m cryo.config_reference`. Do not edit. -->",
        f"<p>{len(rows)} settings, grouped by the first word of their name.</p>",
    ]
    for group in order:
        title = "Other" if group == "other" else f"CRYO_{group.upper()}_*"
        parts.append(f'<h3 id="env-{group}">{esc(title)}</h3>')
        parts.append('<div class="tbl">\n<table>')
        parts.append(
            "  <thead><tr><th>Variable</th><th>Default</th><th>What it does</th></tr></thead>"
        )
        parts.append("  <tbody>")
        for row in groups[group]:
            notes = []
            if row.production:
                notes.append("<strong>Set in production.</strong>")
            if row.description:
                notes.append(esc(row.description))
            else:
                notes.append("<em>Not described yet.</em>")
            if row.security:
                notes.append(f"<strong>Security:</strong> {esc(row.security)}")
            parts.append(
                f"    <tr><td><code>{esc(row.name)}</code></td>"
                f"<td><code>{esc(row.default)}</code></td><td>{' '.join(notes)}</td></tr>"
            )
        parts.append("  </tbody>\n</table>\n</div>")

    extras = sorted(env_reads_outside_settings() & set(EXTRA_ENV))
    parts.append('<h3 id="env-outside-settings">Read outside Settings</h3>')
    parts.append(
        "<p>These are read with <code>os.environ</code> by one module rather than through "
        "<code>Settings</code>. Most are set by Cryo itself on the Jobs it creates.</p>"
    )
    parts.append('<div class="tbl">\n<table>')
    parts.append("  <thead><tr><th>Variable</th><th>What it does</th></tr></thead>")
    parts.append("  <tbody>")
    for name in extras:
        parts.append(
            f"    <tr><td><code>{esc(name)}</code></td><td>{esc(EXTRA_ENV[name])}</td></tr>"
        )
    parts.append("  </tbody>\n</table>\n</div>")
    return "\n".join(parts) + "\n"


def render_helm() -> str:
    esc = html.escape
    values = helm_values(VALUES_FILE.read_text(encoding="utf-8"))
    parts = [
        "<!-- Generated by `python -m cryo.config_reference`. Do not edit. -->",
        '<div class="tbl">\n<table>',
        "  <thead><tr><th>Value</th><th>What it configures</th><th>Keys</th></tr></thead>",
        "  <tbody>",
    ]
    for key, children in values:
        keys = ", ".join(f"<code>{esc(child)}</code>" for child in children) or "—"
        parts.append(
            f"    <tr><td><code>{esc(key)}</code></td>"
            f"<td>{esc(HELM_DESCRIPTIONS.get(key, ''))}</td><td>{keys}</td></tr>"
        )
    parts.append("  </tbody>\n</table>\n</div>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the docs-site configuration reference")
    parser.add_argument("--check", action="store_true", help="Fail when the output is stale")
    args = parser.parse_args(argv)

    issues = problems()
    for issue in issues:
        print(issue, file=sys.stderr)

    outputs = {ENV_OUTPUT: render_env(), HELM_OUTPUT: render_helm()}
    if args.check:
        stale = [
            path
            for path, rendered in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != rendered
        ]
        for path in stale:
            print(
                f"{path.relative_to(ROOT)} is stale; run `python -m cryo.config_reference` "
                "and `node site/scripts/build.mjs`",
                file=sys.stderr,
            )
        return 1 if issues or stale else 0

    for path, rendered in outputs.items():
        path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {path.relative_to(ROOT)}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
