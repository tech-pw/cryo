"""Compute Cryo's real static Kubernetes resource footprint from the Helm chart's own
committed defaults.

The README/amoro-evaluation.md claim "lighter to operate (no Mixed-Iceberg coupling)" and "no
JVM-as-sidecar dependency" -- true statements about architecture, but previously unbacked by a
number. This script makes the Cryo side of that claim self-verifying: it parses
deploy/helm/cryo/values.yaml directly (the actual shipped defaults, not a hand-copied snapshot
that can drift from the chart), so it stays accurate as resource requests change.

Deliberately prints NO specific Amoro resource-request numbers. docs/amoro-evaluation.md's own
source list (its §7) never actually cites a CPU/memory figure for AMS or an optimizer pool --
every other doc in this repo that mentions Amoro cites its architecture (minor/major/full
taxonomy, Quota/Balanced scheduling) but not a sizing number either. Fabricating one here would
be exactly the "docs asserting something the repo doesn't back up" failure mode
docs/OSS_READINESS.md §1.1 already names as the worst credibility risk for this project. Get a
real number from https://amoro.apache.org/docs/latest/deployment/ (or by installing Amoro) and
add it to docs/amoro-evaluation.md §7 first if a comparison number is wanted -- then wire that
cited number into this script's AMORO_PUBLISHED dict, which is intentionally left empty until
that happens.

Usage:
    python scripts/deployment_footprint.py [--values PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_VALUES_PATH = (
    Path(__file__).resolve().parent.parent / "deploy" / "helm" / "cryo" / "values.yaml"
)

# Always-on components (docs/oss-launch-plan-2026-09-25.md §3, "Installation profiles").
# Optional/conditional paths -- scanner CronJob, maintenance CronJob, backup, native, spark --
# run on a schedule or opt-in flag, not continuously, so they are excluded from a *static
# baseline* footprint the same way scannerWorker's KEDA-scale-to-zero path is: this script
# measures steady-state idle floor, not peak burst capacity. Same component set for both
# documented profiles today -- evaluation vs. production is about Postgres/Redis being bundled
# vs. external, not a different app-pod resource tier.
BASELINE_COMPONENTS = ("api", "worker", "governor", "scheduler", "scannerWorker")

# Deliberately empty. No document in this repo (docs/amoro-evaluation.md's own §7 source list
# included) cites a specific Amoro CPU/memory sizing figure -- every existing reference is
# architectural (minor/major/full taxonomy, Quota/Balanced scheduling), never a resource
# number. Populate this with {"component name": {"cpu_milli": N, "mem_mib": N}} only after
# adding the same cited source to docs/amoro-evaluation.md §7; see this module's docstring.
AMORO_PUBLISHED: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class ComponentFootprint:
    name: str
    replicas: int
    request_cpu_milli: int
    request_mem_mib: int

    @property
    def total_cpu_milli(self) -> int:
        return self.replicas * self.request_cpu_milli

    @property
    def total_mem_mib(self) -> int:
        return self.replicas * self.request_mem_mib


def _parse_cpu_milli(value: Any) -> int:
    """K8s CPU quantity ('250m', '1', 1) -> millicores. Duplicated (not imported) from
    cryo.engines.native.resources._parse_cpu_milli deliberately -- that helper is a private,
    engine-internal implementation detail of native-engine pod sizing, not a public shared
    utility; this script has no other reason to depend on that module."""
    if isinstance(value, (int, float)):
        return int(value * 1000) if isinstance(value, float) and value < 1 else int(value)
    text = str(value).strip().lower()
    if text.endswith("m"):
        return int(float(text[:-1]))
    return int(float(text) * 1000)


def _parse_memory_mib(value: Any) -> int:
    """K8s memory quantity ('768Mi', '1Gi') -> MiB. See _parse_cpu_milli's docstring for why
    this is a local copy, not an import."""
    text = str(value).strip().lower()
    if text.endswith("gi"):
        return int(float(text[:-2]) * 1024)
    if text.endswith("g"):
        return int(float(text[:-1]) * 1024)
    if text.endswith("mi"):
        return int(float(text[:-2]))
    if text.endswith("m"):
        return int(float(text[:-1]))
    return int(float(text) / (1024 * 1024))


def load_component_footprints(
    values_path: Path, component_names: tuple[str, ...]
) -> list[ComponentFootprint]:
    values: dict[str, Any] = yaml.safe_load(values_path.read_text())
    footprints = []
    for name in component_names:
        block = values.get(name)
        if not block:
            raise KeyError(f"{name!r} not found in {values_path} -- chart layout may have changed")
        requests = block.get("resources", {}).get("requests")
        if not requests:
            raise KeyError(f"{name!r} has no resources.requests in {values_path}")
        # scannerWorker's `replicas` is the fallback when its HPA/KEDA autoscaler is disabled;
        # its autoscaling.minReplicas is the honest steady-state floor when either is enabled
        # (the common case per the chart's own committed default, autoscaling.enabled: true).
        replicas = block.get("replicas", 1)
        autoscaling = block.get("autoscaling", {})
        if autoscaling.get("enabled"):
            replicas = autoscaling.get("minReplicas", replicas)
        footprints.append(
            ComponentFootprint(
                name=name,
                replicas=int(replicas),
                request_cpu_milli=_parse_cpu_milli(requests["cpu"]),
                request_mem_mib=_parse_memory_mib(requests["memory"]),
            )
        )
    return footprints


def render_report(footprints: list[ComponentFootprint], *, as_json: bool) -> str:
    total_cpu = sum(f.total_cpu_milli for f in footprints)
    total_mem = sum(f.total_mem_mib for f in footprints)

    if as_json:
        payload = {
            "cryo": {
                "source": "deploy/helm/cryo/values.yaml (parsed live, not hand-copied)",
                "components": [
                    {
                        "name": f.name,
                        "replicas": f.replicas,
                        "request_cpu_milli_per_replica": f.request_cpu_milli,
                        "request_mem_mib_per_replica": f.request_mem_mib,
                        "total_cpu_milli": f.total_cpu_milli,
                        "total_mem_mib": f.total_mem_mib,
                    }
                    for f in footprints
                ],
                "total_cpu_cores": round(total_cpu / 1000, 2),
                "total_mem_gib": round(total_mem / 1024, 2),
            },
            "amoro_published": {
                "components": AMORO_PUBLISHED,
                "note": (
                    "Empty: no cited Amoro resource-request figure exists in this repo. See "
                    "this script's module docstring for how to add one honestly."
                    if not AMORO_PUBLISHED
                    else "Published minimums, not independently benchmarked here."
                ),
            },
        }
        return json.dumps(payload, indent=2)

    lines = ["Cryo static baseline (parsed live from deploy/helm/cryo/values.yaml):", ""]
    lines.append(
        f"{'component':<16} {'replicas':>8} {'cpu/replica':>12} {'mem/replica':>12} "
        f"{'total cpu':>10} {'total mem':>10}"
    )
    for f in footprints:
        lines.append(
            f"{f.name:<16} {f.replicas:>8} {f.request_cpu_milli:>9}m {f.request_mem_mib:>9}Mi "
            f"{f.total_cpu_milli / 1000:>9.2f} {f.total_mem_mib / 1024:>9.2f}Gi"
        )
    lines.append("")
    lines.append(
        f"TOTAL: {total_cpu / 1000:.2f} CPU cores, {total_mem / 1024:.2f} GiB memory "
        "(requests, steady-state idle floor)"
    )
    lines.append("")
    if AMORO_PUBLISHED:
        lines.append("Apache Amoro, published minimums (NOT independently benchmarked here):")
        for name, spec in AMORO_PUBLISHED.items():
            lines.append(
                f"  {name}: {spec['cpu_milli'] / 1000:.1f} CPU, {spec['mem_mib'] / 1024:.1f} GiB"
            )
    else:
        lines.append(
            "Apache Amoro comparison: NOT INCLUDED. No document in this repo cites a specific"
            " Amoro CPU/memory sizing figure -- fabricating one here would misrepresent"
            " unverified numbers as fact. To add a real comparison: get a cited figure from"
            " https://amoro.apache.org/docs/latest/deployment/ (or an actual local install),"
            " add the citation to docs/amoro-evaluation.md §7, then populate this script's"
            " AMORO_PUBLISHED dict with the same cited numbers."
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--values", type=Path, default=DEFAULT_VALUES_PATH)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    footprints = load_component_footprints(args.values, BASELINE_COMPONENTS)
    print(render_report(footprints, as_json=args.json))


if __name__ == "__main__":
    main()
