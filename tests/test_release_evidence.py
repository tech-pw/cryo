from __future__ import annotations

from pathlib import Path

from cryo.evidence.release import (
    EXTERNAL_GATES,
    RELEASE_DOSSIER_SCHEMA_VERSION,
    build_release_dossier,
)


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
