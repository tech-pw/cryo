from __future__ import annotations

import base64
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cryo.evidence.external import (
    EXTERNAL_EVIDENCE_SCHEMA,
    ExternalEvidence,
    external_evidence_payload,
    required_environment_for_gate,
    verify_external_evidence,
)
from cryo.evidence.release import (
    EXTERNAL_GATE_REQUIRED_COUNTS,
    EXTERNAL_GATES,
    RELEASE_DOSSIER_SCHEMA_VERSION,
    build_release_dossier,
)
from scripts.build_release_dossier import _load_external_evidence


def test_release_dossier_hashes_repository_evidence_and_fails_closed() -> None:
    root = Path(__file__).resolve().parents[1]

    dossier = build_release_dossier(root, version="v1.2.3", revision="abc123")

    assert dossier["schema_version"] == RELEASE_DOSSIER_SCHEMA_VERSION
    assert dossier["graduation"] == {
        "repository_complete": True,
        "external_complete": False,
        "ga_eligible": False,
        "blockers": list(EXTERNAL_GATES),
    }
    assert all(
        section["status"] == "repository_verified" for section in dossier["repository_evidence"]
    )
    assert all(
        len(artifact["sha256"]) == 64
        for section in dossier["repository_evidence"]
        for artifact in section["artifacts"]
    )
    assert len(dossier["integrity"]["sha256"]) == 64


def test_release_dossier_reports_missing_repository_artifacts(tmp_path: Path) -> None:
    dossier = build_release_dossier(tmp_path, version="dev", revision="dirty")

    assert dossier["graduation"]["repository_complete"] is False
    assert all(section["status"] == "incomplete" for section in dossier["repository_evidence"])
    assert all(section["missing"] for section in dossier["repository_evidence"])


def test_release_dossier_accepts_only_signed_revision_bound_external_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    key = Ed25519PrivateKey.generate()
    verified = []
    for gate in EXTERNAL_GATES:
        for index in range(EXTERNAL_GATE_REQUIRED_COUNTS[gate]):
            evidence = ExternalEvidence(
                schema_version=EXTERNAL_EVIDENCE_SCHEMA,
                evidence_id=f"{gate}-{index}",
                gate=gate,
                subject_revision="abc123",
                environment=required_environment_for_gate(gate),
                collected_at=datetime.now(UTC).isoformat(),
                verifier="independent-verifier",
                artifact_uri=f"s3://evidence/{gate}/{index}.json",
                artifact_sha256="a" * 64,
                result="passed",
                signature="",
            )
            signed = replace(
                evidence,
                signature=base64.b64encode(key.sign(external_evidence_payload(evidence))).decode(),
            )
            verified.append(
                verify_external_evidence(
                    signed,
                    public_key=key.public_key(),
                    allowed_gates=frozenset(EXTERNAL_GATES),
                )
            )

    dossier = build_release_dossier(
        root,
        version="v1.2.3",
        revision="abc123",
        external_evidence=tuple(verified),
    )

    assert dossier["graduation"] == {
        "repository_complete": True,
        "external_complete": True,
        "ga_eligible": True,
        "blockers": [],
    }


def test_external_evidence_rejects_tampering() -> None:
    key = Ed25519PrivateKey.generate()
    evidence = ExternalEvidence(
        schema_version=EXTERNAL_EVIDENCE_SCHEMA,
        evidence_id="restore-1",
        gate="two_measured_restore_drills",
        subject_revision="abc123",
        environment="production",
        collected_at=datetime.now(UTC).isoformat(),
        verifier="independent-verifier",
        artifact_uri="s3://evidence/restore.json",
        artifact_sha256="a" * 64,
        result="passed",
        signature="",
    )
    signed = replace(
        evidence,
        signature=base64.b64encode(key.sign(external_evidence_payload(evidence))).decode(),
        artifact_sha256="b" * 64,
    )

    try:
        verify_external_evidence(
            signed,
            public_key=key.public_key(),
            allowed_gates=frozenset(EXTERNAL_GATES),
        )
    except ValueError as error:
        assert str(error) == "external evidence signature is invalid"
    else:
        raise AssertionError("tampered external evidence must fail")


def test_release_builder_loads_only_evidence_with_matching_trusted_key(
    tmp_path: Path,
) -> None:
    evidence_dir = tmp_path / "evidence"
    keys_dir = tmp_path / "keys"
    evidence_dir.mkdir()
    keys_dir.mkdir()
    key = Ed25519PrivateKey.generate()
    evidence = ExternalEvidence(
        schema_version=EXTERNAL_EVIDENCE_SCHEMA,
        evidence_id="security-review-1",
        gate="external_security_review",
        subject_revision="abc123",
        environment="external",
        collected_at=datetime.now(UTC).isoformat(),
        verifier="security-reviewer",
        artifact_uri="https://evidence.example/security-review.json",
        artifact_sha256="a" * 64,
        result="passed",
        signature="",
    )
    signed = replace(
        evidence,
        signature=base64.b64encode(key.sign(external_evidence_payload(evidence))).decode(),
    )
    (evidence_dir / "security-review.json").write_text(json.dumps(asdict(signed)))
    (keys_dir / "security-reviewer.pem").write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    loaded = _load_external_evidence(evidence_dir, keys_dir)

    assert len(loaded) == 1
    assert loaded[0].gate == "external_security_review"
