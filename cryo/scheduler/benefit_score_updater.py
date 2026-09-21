"""Background job to update AutoComp benefit scores.

Runs periodically after query log ingestion to refresh partition priorities.

Review finding F01, 2026-09-20: this module used to also own the daemon-loop wrapper
(benefit_score_updater_loop_sync/benefit_score_updater_loop/start_benefit_score_updater) --
removed because cryo.cli.BenefitScoreUpdaterRunner (a DaemonRunner subclass, same pattern as
every other scheduler background thread) is now the single loop implementation both CLI
entrypoints (`cryo scheduler` and `cryo serve`) share, so the benefit_score_sweep_via_k8s_jobs
flag check in its _tick method can no longer be bypassed by one entrypoint using a different,
flag-unaware loop.
"""

from __future__ import annotations

from cryo.db.session import SessionLocal
from cryo.planner.benefit_score import update_all_benefit_scores


def run_benefit_score_update() -> int:
    """Run one benefit score refresh pass and return partitions updated."""
    with SessionLocal() as session:
        # The realized-value dataset joins 90 days of audit, stats, cost, and query history --
        # running that per table across a 12k-table fleet every sweep is a real memory/IO storm
        # (this used refresh_realized_value=False, unconditionally, as an emergency 2026-09-15/16
        # OOM stopgap -- but that stops per-table learning forever, not just during the
        # incident, which is CR-04's exact regression). update_all_benefit_scores now bounds this
        # internally (max_realized_value_refreshes, default 500/sweep, always spent on the
        # stalest tables first) instead of an all-or-nothing switch -- see its own docstring.
        return update_all_benefit_scores(session, refresh_realized_value=True)
