from __future__ import annotations

from cryo.evidence.upgrade import (
    UPGRADE_EVIDENCE_SCHEMA,
    build_upgrade_rollback_evidence,
)


def test_upgrade_rollback_evidence_fingerprints_both_transactional_directions() -> None:
    evidence = build_upgrade_rollback_evidence(
        revision="abc123",
        upgrade_sql=b"BEGIN;\nCREATE TABLE example (id int);\nCOMMIT;\n",
        rollback_sql=b"BEGIN;\nDROP TABLE example;\nCOMMIT;\n",
    )

    assert evidence.schema_version == UPGRADE_EVIDENCE_SCHEMA
    assert evidence.status == "repository_render_verified"
    assert evidence.production_qualified is False
    assert evidence.upgrade.transaction_wrapped is True
    assert evidence.rollback.transaction_wrapped is True
    assert len(evidence.upgrade.sha256) == 64
    assert len(evidence.rollback.sha256) == 64
    assert evidence.blockers


def test_upgrade_rollback_evidence_fails_closed_for_unwrapped_or_empty_sql() -> None:
    evidence = build_upgrade_rollback_evidence(
        revision="abc123",
        upgrade_sql=b"CREATE TABLE example (id int);",
        rollback_sql=b"",
    )

    assert evidence.status == "incomplete"
    assert evidence.production_qualified is False


def test_upgrade_rollback_evidence_rejects_missing_revision() -> None:
    try:
        build_upgrade_rollback_evidence(
            revision=" ",
            upgrade_sql=b"BEGIN;\nCOMMIT;",
            rollback_sql=b"BEGIN;\nCOMMIT;",
        )
    except ValueError as error:
        assert str(error) == "revision must not be empty"
    else:
        raise AssertionError("empty revision must fail")


def test_full_migration_graph_renders_offline() -> None:
    """The release evidence job runs `alembic upgrade head --sql` and the reverse. A migration
    that reads rows unconditionally breaks offline rendering (20261022_1200 did, which failed
    every release-supply-chain run from 2026-10-22), so render both directions here."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    alembic = shutil.which("alembic") or str(Path(sys.executable).with_name("alembic"))
    if not Path(alembic).exists():
        pytest.skip("alembic CLI not installed")
    root = Path(__file__).resolve().parents[1]
    for args in (("upgrade", "head"), ("downgrade", "head:base")):
        result = subprocess.run(
            (alembic, *args, "--sql"), cwd=root, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert "COMMIT" in result.stdout
    upgrade = subprocess.run(
        (alembic, "upgrade", "head", "--sql"), cwd=root, capture_output=True, text=True, check=True
    ).stdout
    assert "duplicate active-job backfill runs only online" in upgrade
