from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryo.evidence.external import VerifiedExternalEvidence

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
    EvidenceSection(
        "upgrade_and_rollback",
        "Bidirectional migration rendering and fail-closed production-proof boundary.",
        (
            "docs/UPGRADE_ROLLBACK.md",
            "docs/SUPPORT_POLICY.md",
            "docs/EXTERNAL_EVIDENCE_ATTESTATION.md",
            "docs/schemas/external-evidence-v1.schema.json",
            "cryo/evidence/upgrade.py",
            "cryo/evidence/external.py",
            "scripts/build_upgrade_rollback_evidence.py",
            "tests/test_upgrade_rollback_evidence.py",
        ),
    ),
    EvidenceSection(
        "category_leadership_contracts",
        "Opt-in privacy-preserving aggregates and ecosystem connector contracts.",
        (
            "docs/PRIVACY_PRESERVING_BENCHMARKS.md",
            "docs/ECOSYSTEM_CONNECTORS.md",
            "docs/schemas/aggregate-fleet-benchmark-v1.schema.json",
            "docs/schemas/connector-manifest-v1.schema.json",
            "cryo/privacy/fleet_benchmark.py",
            "cryo/ecosystem/connectors.py",
            "tests/test_aggregate_fleet_benchmark.py",
            "tests/test_connector_contract.py",
        ),
    ),
)

EXTERNAL_GATES = (
    "six_design_partner_interviews",
    "wave_1_2_staffing_confirmed",
    "production_baseline_queries",
    "production_10000_attempt_fault_campaign",
    "deployed_p99_pause_propagation",
    "production_rpo_report",
    "representative_30_day_soak",
    "production_infrastructure_fault_drills",
    "deployed_rollout_halt_resume",
    "production_slo_history",
    "production_90_day_stop_ship_history",
    "two_measured_restore_drills",
    "production_upgrade_and_rollback",
    "production_lineage_90_pct",
    "calibrated_prediction_gates",
    "autocomp_holdout_vmy_lift",
    "delete_lifecycle_controlled_holdout",
    "three_design_partner_bounded_automation",
    "controlled_shadow_layout_holdout",
    "deployed_cross_engine_conformance",
    "approved_bounded_layout_canary",
    "external_security_review",
    "external_iceberg_correctness_review",
    "three_production_case_studies",
    "three_independent_mdr_consumers",
    "three_repeat_external_contributors",
    "two_qualifying_releases",
    "independent_category_adoption",
)
EXTERNAL_GATE_REQUIRED_COUNTS = {
    "six_design_partner_interviews": 6,
    "wave_1_2_staffing_confirmed": 1,
    "production_baseline_queries": 1,
    "production_10000_attempt_fault_campaign": 1,
    "deployed_p99_pause_propagation": 1,
    "production_rpo_report": 1,
    "representative_30_day_soak": 1,
    "production_infrastructure_fault_drills": 1,
    "deployed_rollout_halt_resume": 1,
    "production_slo_history": 1,
    "production_90_day_stop_ship_history": 1,
    "two_measured_restore_drills": 2,
    "production_upgrade_and_rollback": 1,
    "production_lineage_90_pct": 1,
    "calibrated_prediction_gates": 1,
    "autocomp_holdout_vmy_lift": 1,
    "delete_lifecycle_controlled_holdout": 1,
    "three_design_partner_bounded_automation": 3,
    "controlled_shadow_layout_holdout": 1,
    "deployed_cross_engine_conformance": 1,
    "approved_bounded_layout_canary": 1,
    "external_security_review": 1,
    "external_iceberg_correctness_review": 1,
    "three_production_case_studies": 3,
    "three_independent_mdr_consumers": 3,
    "three_repeat_external_contributors": 3,
    "two_qualifying_releases": 2,
    "independent_category_adoption": 1,
}


def build_release_dossier(
    root: Path,
    *,
    version: str,
    revision: str,
    external_evidence: tuple[VerifiedExternalEvidence, ...] = (),
) -> dict[str, Any]:
    root = root.resolve()
    sections = [_section_payload(root, section) for section in REPOSITORY_SECTIONS]
    repository_complete = all(section["status"] == "repository_verified" for section in sections)
    external_sections = _external_evidence_payload(revision, external_evidence)
    external_complete = all(section["status"] == "verified" for section in external_sections)
    dossier: dict[str, Any] = {
        "schema_version": RELEASE_DOSSIER_SCHEMA_VERSION,
        "release": {
            "version": version,
            "revision": revision,
        },
        "repository_evidence": sections,
        "external_evidence": external_sections,
        "graduation": {
            "repository_complete": repository_complete,
            "external_complete": external_complete,
            "ga_eligible": repository_complete and external_complete,
            "blockers": [
                section["gate"] for section in external_sections if section["status"] != "verified"
            ],
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
    external_evidence: tuple[VerifiedExternalEvidence, ...] = (),
) -> dict[str, Any]:
    dossier = build_release_dossier(
        root,
        version=version,
        revision=revision,
        external_evidence=external_evidence,
    )
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


def _external_evidence_payload(
    revision: str,
    evidence: tuple[VerifiedExternalEvidence, ...],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for gate in EXTERNAL_GATES:
        matching = {
            item.evidence_id: item
            for item in evidence
            if item.gate == gate and item.subject_revision == revision
        }
        required = EXTERNAL_GATE_REQUIRED_COUNTS[gate]
        accepted = sorted(matching.values(), key=lambda item: item.evidence_id)
        sections.append(
            {
                "gate": gate,
                "status": "verified" if len(accepted) >= required else "missing",
                "required_count": required,
                "verified_count": len(accepted),
                "evidence": [item.to_dict() for item in accepted],
                "reason": (
                    None
                    if len(accepted) >= required
                    else "Requires independently signed evidence bound to this revision."
                ),
            }
        )
    return sections


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
