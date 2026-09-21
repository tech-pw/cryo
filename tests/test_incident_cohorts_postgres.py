"""Real-Postgres tests for cryo/db/repositories/incident_cohorts.py -- same rationale as
tests/test_analysis_tasks_postgres.py (partial/unique constraints need the real backend).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from cryo.db.models import MaintenanceRun, Source, Table
from cryo.db.repositories.incident_cohorts import (
    add_member,
    create_cohort,
    get_cohort_by_slug,
    list_members,
    record_membership_status,
    record_reconciliation_outcome,
)
from cryo.db.session import SessionLocal


def _postgres_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="requires a reachable local Postgres with migrations applied"
)


@pytest.fixture(autouse=True)
def _clean_up():
    created: dict[str, list] = {"cohorts": [], "runs": [], "tables": [], "sources": []}
    yield created
    with SessionLocal() as session:
        for cohort_id in created["cohorts"]:
            session.execute(
                text("DELETE FROM cryo.incident_cohort_members WHERE cohort_id = :c"),
                {"c": cohort_id},
            )
            session.execute(text("DELETE FROM cryo.incident_cohorts WHERE id = :c"), {"c": cohort_id})
        for run_id in created["runs"]:
            session.execute(text("DELETE FROM cryo.runs WHERE id = :r"), {"r": run_id})
        for table_id in created["tables"]:
            session.execute(text("DELETE FROM cryo.tables WHERE id = :t"), {"t": table_id})
        for source_id in created["sources"]:
            session.execute(text("DELETE FROM cryo.sources WHERE id = :s"), {"s": source_id})
        session.commit()


def _seed_run(session, created) -> uuid.UUID:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:8]}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True
    )
    run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="running", trigger="cron")
    session.add_all([source, table, run])
    session.commit()
    created["sources"].append(source.id)
    created["tables"].append(table.id)
    created["runs"].append(run.id)
    return run.id


def test_create_cohort_is_idempotent_by_slug(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        first = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(first.id)
        second = create_cohort(session, slug=slug, description="different", evidence_query="SELECT 2")
        assert first.id == second.id
        # Idempotent means "return existing", not "overwrite" -- description must be unchanged.
        assert second.description == "d"


def test_add_member_is_idempotent_and_never_duplicates(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)

        first = add_member(session, cohort.id, run_id)
        second = add_member(session, cohort.id, run_id)
        assert first.id == second.id

        members = list_members(session, cohort.id)
        assert len(members) == 1


def test_record_reconciliation_outcome_persists_status_and_notes(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)
        member = add_member(session, cohort.id, run_id)

        record_reconciliation_outcome(
            session, member.id, status="run_scope_closed", notes="finalized cleanly via grace fix"
        )

        members = list_members(session, cohort.id)
        assert members[0].reconciliation_status == "run_scope_closed"
        assert members[0].notes == "finalized cleanly via grace fix"
        assert members[0].reconciled_at is not None


def test_record_reconciliation_outcome_rejects_unknown_status(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)
        member = add_member(session, cohort.id, run_id)

        with pytest.raises(ValueError):
            record_reconciliation_outcome(session, member.id, status="reconciled_as_success")


def test_get_cohort_by_slug_returns_none_when_missing(_clean_up):
    with SessionLocal() as session:
        assert get_cohort_by_slug(session, f"nonexistent-{uuid.uuid4().hex}") is None


def test_add_member_defaults_to_candidate_membership_status(_clean_up):
    """Review finding F11: a newly-added member matched only by the window query must start as
    'candidate' -- NOT implicitly treated as a confirmed incident-affected run."""
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)

        member = add_member(session, cohort.id, run_id)
        assert member.membership_status == "candidate"


def test_record_membership_status_promotes_candidate_to_confirmed(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)
        member = add_member(session, cohort.id, run_id)

        record_membership_status(session, member.id, status="confirmed")

        members = list_members(session, cohort.id)
        assert members[0].membership_status == "confirmed"


def test_record_membership_status_rejects_unknown_status(_clean_up):
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)
        run_id = _seed_run(session, _clean_up)
        member = add_member(session, cohort.id, run_id)

        with pytest.raises(ValueError):
            record_membership_status(session, member.id, status="definitely_affected")


def test_list_members_filters_by_membership_status(_clean_up):
    """Review finding F11: a caller must be able to get only 'confirmed' members -- the whole
    point of the candidate/confirmed distinction is that presenting the unfiltered candidate
    list as 'affected by the incident' would overstate confirmed impact."""
    slug = f"test-cohort-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        cohort = create_cohort(session, slug=slug, description="d", evidence_query="SELECT 1")
        _clean_up["cohorts"].append(cohort.id)

        run_a = _seed_run(session, _clean_up)
        run_b = _seed_run(session, _clean_up)
        member_a = add_member(session, cohort.id, run_a)
        add_member(session, cohort.id, run_b)  # stays "candidate"

        record_membership_status(session, member_a.id, status="confirmed")

        all_members = list_members(session, cohort.id)
        assert len(all_members) == 2

        confirmed_only = list_members(session, cohort.id, membership_status="confirmed")
        assert len(confirmed_only) == 1
        assert confirmed_only[0].id == member_a.id

        candidates_only = list_members(session, cohort.id, membership_status="candidate")
        assert len(candidates_only) == 1
        assert candidates_only[0].run_id == run_b
