from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

EXTERNAL_EVIDENCE_SCHEMA = "cryo.external-evidence/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
_EXTERNAL_CONTEXT_GATES = frozenset(
    {
        "six_design_partner_interviews",
        "wave_1_2_staffing_confirmed",
        "external_security_review",
        "external_iceberg_correctness_review",
        "three_independent_mdr_consumers",
        "three_repeat_external_contributors",
        "independent_category_adoption",
    }
)


@dataclass(frozen=True, slots=True)
class ExternalEvidence:
    schema_version: str
    evidence_id: str
    gate: str
    subject_revision: str
    environment: str
    collected_at: str
    verifier: str
    artifact_uri: str
    artifact_sha256: str
    result: str
    signature: str


@dataclass(frozen=True, slots=True)
class VerifiedExternalEvidence:
    evidence_id: str
    gate: str
    subject_revision: str
    environment: str
    collected_at: str
    verifier: str
    artifact_uri: str
    artifact_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def verify_external_evidence(
    evidence: ExternalEvidence,
    *,
    public_key: Ed25519PublicKey,
    allowed_gates: frozenset[str],
) -> VerifiedExternalEvidence:
    if evidence.schema_version != EXTERNAL_EVIDENCE_SCHEMA:
        raise ValueError("unsupported external evidence schema")
    if evidence.gate not in allowed_gates:
        raise ValueError("unknown external evidence gate")
    if evidence.result != "passed":
        raise ValueError("external evidence result is not passed")
    required_environment = required_environment_for_gate(evidence.gate)
    if evidence.environment != required_environment:
        raise ValueError(
            f"external evidence environment must be {required_environment} for this gate"
        )
    if not _EVIDENCE_ID.fullmatch(evidence.evidence_id):
        raise ValueError("evidence_id must be a safe stable identifier")
    if not evidence.subject_revision:
        raise ValueError("evidence identity, revision, and verifier are required")
    if not _VERIFIER.fullmatch(evidence.verifier):
        raise ValueError("verifier must be a safe stable identifier")
    artifact = urlparse(evidence.artifact_uri)
    if artifact.scheme not in {"https", "s3"} or not artifact.netloc:
        raise ValueError("artifact URI must use https:// or s3://")
    if artifact.username is not None or artifact.password is not None or artifact.fragment:
        raise ValueError("artifact URI must not contain credentials or fragments")
    if not _SHA256.fullmatch(evidence.artifact_sha256):
        raise ValueError("artifact_sha256 must be lowercase SHA-256")
    try:
        collected_at = datetime.fromisoformat(evidence.collected_at)
    except ValueError as exc:
        raise ValueError("collected_at must be ISO-8601") from exc
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise ValueError("collected_at must include a timezone")
    try:
        signature = base64.b64decode(evidence.signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("signature must be valid base64") from exc
    try:
        public_key.verify(signature, external_evidence_payload(evidence))
    except InvalidSignature as exc:
        raise ValueError("external evidence signature is invalid") from exc
    return VerifiedExternalEvidence(
        evidence_id=evidence.evidence_id,
        gate=evidence.gate,
        subject_revision=evidence.subject_revision,
        environment=evidence.environment,
        collected_at=evidence.collected_at,
        verifier=evidence.verifier,
        artifact_uri=evidence.artifact_uri,
        artifact_sha256=evidence.artifact_sha256,
    )


def external_evidence_payload(evidence: ExternalEvidence) -> bytes:
    unsigned = asdict(evidence)
    unsigned.pop("signature")
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def required_environment_for_gate(gate: str) -> str:
    return "external" if gate in _EXTERNAL_CONTEXT_GATES else "production"
