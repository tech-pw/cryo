from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cryo.evidence.external import (
    ExternalEvidence,
    VerifiedExternalEvidence,
    verify_external_evidence,
)
from cryo.evidence.release import EXTERNAL_GATES, write_release_dossier


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, fail-closed Cryo release evidence dossier."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--external-evidence-dir", type=Path)
    parser.add_argument("--trusted-keys-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    revision = args.revision or _git_revision(args.root)
    external_evidence = _load_external_evidence(
        args.external_evidence_dir,
        args.trusted_keys_dir,
    )
    dossier = write_release_dossier(
        args.root,
        args.output,
        version=args.version,
        revision=revision,
        external_evidence=external_evidence,
    )
    return 0 if dossier["graduation"]["repository_complete"] else 1


def _git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_external_evidence(
    evidence_dir: Path | None,
    keys_dir: Path | None,
) -> tuple[VerifiedExternalEvidence, ...]:
    if evidence_dir is None and keys_dir is None:
        return ()
    if evidence_dir is None or keys_dir is None:
        raise ValueError("--external-evidence-dir and --trusted-keys-dir must be provided together")
    verified = []
    for path in sorted(evidence_dir.glob("*.json")):
        evidence = ExternalEvidence(**json.loads(path.read_text()))
        key_value = serialization.load_pem_public_key(
            (keys_dir / f"{evidence.verifier}.pem").read_bytes()
        )
        if not isinstance(key_value, Ed25519PublicKey):
            raise ValueError(f"trusted key is not Ed25519: {evidence.verifier}")
        verified.append(
            verify_external_evidence(
                evidence,
                public_key=key_value,
                allowed_gates=frozenset(EXTERNAL_GATES),
            )
        )
    return tuple(verified)


if __name__ == "__main__":
    raise SystemExit(main())
