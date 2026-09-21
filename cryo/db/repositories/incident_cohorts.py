"""Persist-only incident cohort tracking (recovery plan §4/§5, 2026-09-20).

Deliberately does not fail, cancel, or relabel any run/job -- see
cryo/db/models.py::IncidentCohort's docstring. Actual remediation of a member still goes through
cryo.worker.orphan_recovery.recover_orphaned_jobs / cryo.queue.reconcile.reconcile_stuck_admitted_jobs,
which already check external execution state before touching anything; this module only records
membership and the reconciliation *outcome* observed by re-querying those existing systems.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cryo.db.models import IncidentCohort, IncidentCohortMember

_VALID_RECONCILIATION_STATUSES = frozenset(
    {"pending", "attempt_ended", "run_scope_closed", "unresolved"}
)
# Review finding F11: "candidate" (matched the incident window query, not yet individually
# verified) vs "confirmed" (independently checked and found to actually exhibit the incident's
# symptom) vs "excluded" (checked and found NOT actually affected). See
# cryo/db/models.py::IncidentCohortMember.membership_status's docstring.
_VALID_MEMBERSHIP_STATUSES = frozenset({"candidate", "confirmed", "excluded"})


def create_cohort(
    session: Session, *, slug: str, description: str, evidence_query: str
) -> IncidentCohort:
    """Idempotent by slug: re-running the same investigation script twice returns the existing
    cohort rather than creating a duplicate."""
    existing = session.scalar(select(IncidentCohort).where(IncidentCohort.slug == slug))
    if existing is not None:
        return existing
    cohort = IncidentCohort(slug=slug, description=description, evidence_query=evidence_query)
    session.add(cohort)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(IncidentCohort).where(IncidentCohort.slug == slug))
        if existing is None:
            raise
        return existing
    session.refresh(cohort)
    return cohort


def add_member(
    session: Session, cohort_id: uuid.UUID, run_id: uuid.UUID
) -> IncidentCohortMember:
    """Idempotent by (cohort_id, run_id) -- see the partial-index-free unique index on the
    model; re-adding an already-tracked run returns the existing membership row unchanged."""
    existing = session.scalar(
        select(IncidentCohortMember).where(
            IncidentCohortMember.cohort_id == cohort_id, IncidentCohortMember.run_id == run_id
        )
    )
    if existing is not None:
        return existing
    member = IncidentCohortMember(cohort_id=cohort_id, run_id=run_id)
    session.add(member)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(IncidentCohortMember).where(
                IncidentCohortMember.cohort_id == cohort_id,
                IncidentCohortMember.run_id == run_id,
            )
        )
        if existing is None:
            raise
        return existing
    session.refresh(member)
    return member


def record_reconciliation_outcome(
    session: Session,
    member_id: uuid.UUID,
    *,
    status: str,
    notes: str | None = None,
    now: datetime | None = None,
) -> None:
    """Record what an external reconciliation pass (orphan_recovery, reconcile_stuck_admitted_
    jobs, or a human's manual investigation) found for this member -- never mutates the
    underlying run/job itself; this table is a record of outcomes, not a trigger for them."""
    if status not in _VALID_RECONCILIATION_STATUSES:
        raise ValueError(
            f"reconciliation_status must be one of {sorted(_VALID_RECONCILIATION_STATUSES)}, "
            f"got {status!r}"
        )
    member = session.get(IncidentCohortMember, member_id)
    if member is None:
        raise ValueError(f"incident_cohort_member {member_id} not found")
    member.reconciliation_status = status
    member.notes = notes
    member.reconciled_at = now or datetime.now(UTC)
    session.commit()


def record_membership_status(
    session: Session, member_id: uuid.UUID, *, status: str
) -> None:
    """Record the outcome of independently verifying whether a candidate member was actually
    affected by the incident (review finding F11) -- separate from reconciliation_status, which
    tracks what happened to the run's own recovery, not whether it belongs in this cohort at
    all. Only ever narrows the "candidate" default toward "confirmed" or "excluded"; never
    silently re-widens an already-decided member back to "candidate" (callers that need to
    reopen a decision should do so explicitly, this function does not guard against it beyond
    validating the target value itself)."""
    if status not in _VALID_MEMBERSHIP_STATUSES:
        raise ValueError(
            f"membership_status must be one of {sorted(_VALID_MEMBERSHIP_STATUSES)}, got {status!r}"
        )
    member = session.get(IncidentCohortMember, member_id)
    if member is None:
        raise ValueError(f"incident_cohort_member {member_id} not found")
    member.membership_status = status
    session.commit()


def get_cohort_by_slug(session: Session, slug: str) -> IncidentCohort | None:
    return session.scalar(select(IncidentCohort).where(IncidentCohort.slug == slug))


def list_members(
    session: Session, cohort_id: uuid.UUID, *, membership_status: str | None = None
) -> list[IncidentCohortMember]:
    """Pass membership_status="confirmed" to get only independently-verified members (review
    finding F11) -- omit it (the default) to get every candidate, matching the original
    window-query scope. Callers presenting cohort membership as "affected by the incident"
    (rather than "matched the investigation's initial window query") should filter to
    "confirmed" explicitly rather than treating the full candidate list as already verified."""
    query = select(IncidentCohortMember).where(IncidentCohortMember.cohort_id == cohort_id)
    if membership_status is not None:
        query = query.where(IncidentCohortMember.membership_status == membership_status)
    return list(session.scalars(query).all())
