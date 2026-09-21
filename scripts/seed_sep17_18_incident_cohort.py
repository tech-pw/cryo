"""One-off seed script: persist CANDIDATE membership for the Sep 17-18, 2026 outage-window run
cohort (recovery plan §4/§5). Idempotent -- safe to re-run; create_cohort/add_member are both
idempotent by their natural keys. Does not touch any run/job status.

Review finding F11, 2026-09-20: a run created in this time window is a CANDIDATE, not a
confirmed incident-affected run -- window membership alone doesn't establish that a given run
actually exhibited the incident's symptom (most runs from this window finalized normally). Every
row this script inserts starts at membership_status="candidate" (the model's own default); use
cryo.db.repositories.incident_cohorts.record_membership_status to promote a specific run to
"confirmed" or "excluded" only after checking its own evidence (job statuses, orphaned_deferred
failures, etc.), not from this script alone.

Run via: kubectl exec -n cryo deploy/cryo-governor -- python3 scripts/seed_sep17_18_incident_cohort.py
"""

from __future__ import annotations

from sqlalchemy import text

from cryo.db.repositories.incident_cohorts import add_member, create_cohort
from cryo.db.session import SessionLocal

# Review finding F11: explicit UTC bounds -- a bare date literal ('2026-09-17') is interpreted
# using the connection's session timezone, not necessarily UTC, which could silently shift the
# window by several hours depending on the deployment's configured Postgres timezone setting.
_EVIDENCE_QUERY = """
    SELECT id, status, created_at, finished_at
    FROM cryo.runs
    WHERE created_at >= '2026-09-17T00:00:00+00:00' AND created_at < '2026-09-19T00:00:00+00:00'
    ORDER BY created_at
"""


def main() -> None:
    with SessionLocal() as session:
        cohort = create_cohort(
            session,
            slug="sep-17-18-2026-zombie-runs",
            description=(
                "CANDIDATE runs created during the 2026-09-17/18 metastore-congestion + "
                "scheduler-OOM outage window (UTC bounds) -- window membership alone, NOT yet "
                "individually confirmed against each run's own evidence. Investigated as the "
                "source of the ~2,058/hour orphaned_deferred failure spike (jobs deferred for a "
                "genuinely fleet-wide capacity reason, finalized out from under them before "
                "reconcile_deferred_jobs got a chance to un-defer them -- see "
                "run_finalizer.py's _CAPACITY_DEFERRED_FINALIZE_GRACE). Most candidates have "
                "since finalized cleanly via the capacity-grace fix; see each member's "
                "membership_status/reconciliation_status/notes for the current per-run finding, "
                "and only membership_status='confirmed' members should be presented as actually "
                "incident-affected."
            ),
            evidence_query=_EVIDENCE_QUERY,
        )

        rows = session.execute(text(_EVIDENCE_QUERY)).all()
        for row in rows:
            add_member(session, cohort.id, row.id)

        print(
            f"Cohort {cohort.slug} ({cohort.id}): {len(rows)} candidate run(s) recorded "
            f"(membership_status='candidate' -- not yet individually confirmed)."
        )


if __name__ == "__main__":
    main()
