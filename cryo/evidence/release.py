from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RELEASE_DOSSIER_SCHEMA_VERSION = "cryo.release-evidence/v1"


@dataclass(frozen=True, slots=True)
class EvidenceSection:
    name: str
    description: str
    required_paths: tuple[str, ...]


REPOSITORY_SECTIONS = (
    EvidenceSection(
        "capabilities",
        "Versioned support matrix and its executable source.",
        (
            "docs/CAPABILITIES.md",
            "cryo/capabilities/manifest.py",
            "tests/test_capabilities.py",
        ),
    ),
    EvidenceSection(
        "benchmark_and_faults",
        "Deterministic customer-shaped benchmark and fault matrix.",
        (
            "docs/CRYOBENCH.md",
            "cryo/benchmarks/fleet.py",
            "cryo/benchmarks/fault_matrix.py",
            "tests/test_cryobench.py",
        ),
    ),
    EvidenceSection(
        "safety_and_rollout",
        "Durable intent safety and cohort auto-halt contracts.",
        (
            "docs/SAFE_ROLLOUT.md",
            "cryo/rollout/safety.py",
            "tests/test_rollout_safety.py",
            "tests/test_durable_intents.py",
        ),
    ),
    EvidenceSection(
        "slo_and_recovery",
        "Published SLOs and authenticated backup/restore proof tooling.",
        (
            "docs/SLOS.md",
            "docs/BACKUP_RESTORE.md",
            "tests/test_backup.py",
        ),
    ),
    EvidenceSection(
        "supply_chain",
        "Locked dependencies and signed release workflow.",
        (
            "requirements.lock",
            "uv.lock",
            "web/package-lock.json",
            ".github/workflows/release-supply-chain.yml",
        ),
    ),
    EvidenceSection(
        "maintenance_decision_record",
        "Stable privacy-safe decision evidence export.",
        (
            "docs/MAINTENANCE_DECISION_RECORD.md",
            "docs/schemas/maintenance-decision-record-v1.schema.json",
            "cryo/evidence/mdr.py",
            "tests/test_maintenance_decision_record.py",
        ),
    ),
)

EXTERNAL_GATES = (
    "representative_30_day_soak",
    "production_90_day_stop_ship_history",
    "two_measured_restore_drills",
    "production_upgrade_and_rollback",
    "external_security_review",
    "external_iceberg_correctness_review",
    "three_production_case_studies",
    "three_independent_mdr_consumers",
    "three_repeat_external_contributors",
)


def build_release_dossier(
    root: Path,
    *,
    version: str,
    revision: str,
) -> dict[str, Any]:
    root = root.resolve()
    sections = [_section_payload(root, section) for section in REPOSITORY_SECTIONS]
    repository_complete = all(section["status"] == "repository_verified" for section in sections)
    dossier: dict[str, Any] = {
        "schema_version": RELEASE_DOSSIER_SCHEMA_VERSION,
        "release": {
            "version": version,
            "revision": revision,
        },
        "repository_evidence": sections,
        "external_evidence": [
            {
                "gate": gate,
                "status": "missing",
                "reason": "Requires independently verifiable deployed or external evidence.",
            }
            for gate in EXTERNAL_GATES
        ],
        "graduation": {
            "repository_complete": repository_complete,
            "external_complete": False,
            "ga_eligible": False,
            "blockers": list(EXTERNAL_GATES),
        },
    }
    dossier["integrity"] = {
        "sha256": _sha256_bytes(_canonical_json(dossier)),
        "canonicalization": "sorted compact JSON",
    }
    return dossier


def write_release_dossier(
    root: Path,
    output: Path,
    *,
    version: str,
    revision: str,
) -> dict[str, Any]:
    dossier = build_release_dossier(root, version=version, revision=revision)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dossier, indent=2, sort_keys=True) + "\n")
    return dossier


def _section_payload(root: Path, section: EvidenceSection) -> dict[str, Any]:
    artifacts = []
    missing = []
    for relative_path in section.required_paths:
        path = root / relative_path
        if not path.is_file():
            missing.append(relative_path)
            continue
        artifacts.append(
            {
                "path": relative_path,
                "sha256": _sha256_bytes(path.read_bytes()),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "name": section.name,
        "description": section.description,
        "status": "repository_verified" if not missing else "incomplete",
        "artifacts": artifacts,
        "missing": missing,
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
