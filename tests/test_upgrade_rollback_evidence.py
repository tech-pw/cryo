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
