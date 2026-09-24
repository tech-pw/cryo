"""One-shot K8s Job entrypoint for heavy fleet analysis (recovery plan §2/§3, 2026-09-20).

Real incident this replaces: the benefit-score sweep ran as an in-process scheduler thread
(cryo/scheduler/benefit_score_updater.py) with no persisted progress. A crash/OOM/redeploy threw
the whole sweep away and restarted at table 1, and the scheduler's own memory budget was shared
with every other daemon thread it runs -- so this one sweep's growth could (and did) evict
unrelated work. This module is a short-lived process: it claims one AnalysisTask via
cryo/db/repositories/analysis_tasks.py's CAS contract, does one bounded batch of work, commits
progress, and exits -- either to be re-invoked by the coordinator for the next batch (still the
same task, cursor resumed) or because the whole task finished.

Deliberately NOT a long-running loop over the entire fleet in one process: that would just move
the same unbounded-duration problem into a Job instead of a Thread. The coordinator
(cryo/scheduler/benefit_score_updater.py) is responsible for re-invoking this entrypoint
batch-by-batch until the task reaches a terminal status.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

from cryo.db.repositories.analysis_tasks import (
    LeaseConflict,
    claim_due_task,
    commit_progress,
    finish_task,
    heartbeat,
    yield_task,
)
from cryo.db.session import SessionLocal
from cryo.observability.logging import quiet_third_party_loggers
from cryo.planner.benefit_score import (
    _DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP,
    update_all_benefit_scores_batch,
)

logger = logging.getLogger(__name__)

# Registry of task_type -> batch runner. Recovery plan's own file-naming implies a fuller
# cryo/analysis/registry.py if/when a second analysis task type is added; a single-entry dict
# inline here avoids building that abstraction before a second consumer exists.
_BENEFIT_SCORE_SWEEP = "benefit_score_sweep"

# Bounds how many batches (each _DEFAULT_TABLES_PER_BATCH tables) a single Job invocation
# processes before exiting -- lets the coordinator's Job activeDeadlineSeconds and this
# process's own wall-clock budget stay bounded and predictable, rather than one Job attempting
# the entire remaining fleet in a single pod lifetime.
_MAX_BATCHES_PER_INVOCATION = 10

# Default lease length claim_due_task grants. A 200-table batch (_DEFAULT_TABLES_PER_BATCH in
# benefit_score.py) can plausibly exceed 300s -- review finding F05: the pre-fix runner never
# heartbeated during a batch, so a slow batch could let its own lease expire mid-computation,
# and its eventual commit_progress call would then correctly raise LeaseConflict but only AFTER
# the expensive scoring work had already run and committed inside update_all_benefit_scores_batch
# (that function commits its own output per table, independent of this runner's lease state --
# see its own docstring). Heartbeating before each batch (not just after) means a lost lease is
# detected and the runner exits BEFORE starting the next batch's expensive work, not after.
_LEASE_SECONDS = 300


def _owner_id() -> str:
    """Best-effort identity for lease_owner -- the K8s pod name when running as a Job, else
    hostname+pid for local/manual runs. Used only for observability (who holds a lease), never
    for correctness -- correctness comes entirely from lease_generation."""
    pod_name = os.environ.get("HOSTNAME")
    if pod_name:
        return pod_name
    return f"local-{os.getpid()}"


def run_benefit_score_sweep_task() -> int:
    """Claim (or resume) the benefit_score_sweep task and process up to
    _MAX_BATCHES_PER_INVOCATION batches. Returns the process exit code.

    A cursor of None means "start of fleet" (fresh generation or never-started task); the task's
    `cursor` field stores the last-processed Table.id as a string between batches, same pattern
    as TableStats.partition_coverage_cursor for per-table pagination.

    Known remaining gap (review finding F05, not fully closed here): update_all_benefit_scores_
    batch commits its own scoring output (per-table, inside the function) independent of this
    runner's lease -- a stale child whose lease was reclaimed *during* a batch's computation can
    still have that batch's writes land, even though the runner's own subsequent commit_progress
    call will correctly raise LeaseConflict and stop further batches. The heartbeat-before-each-
    batch below closes the common case (detect a lost lease BEFORE starting the next batch's
    work) but does not make an in-flight batch's own commits atomic with the lease check --
    that requires either moving scoring's commit inside the same transaction as the cursor
    update, or a two-phase (compute-then-single-commit) restructuring of update_all_benefit_
    scores_batch itself, which is a larger, separate change to that function's transaction
    boundaries.
    """
    owner = _owner_id()
    with SessionLocal() as session:
        claimed = claim_due_task(
            session, task_type=_BENEFIT_SCORE_SWEEP, owner=owner, lease_seconds=_LEASE_SECONDS
        )
        if claimed is None:
            logger.info("No benefit_score_sweep task currently claimable; exiting cleanly.")
            return 0

        logger.info(
            "Claimed benefit_score_sweep task %s (attempt=%d, lease_generation=%d, cursor=%s)",
            claimed.id,
            claimed.attempt,
            claimed.lease_generation,
            claimed.cursor,
        )

        lease_generation = claimed.lease_generation
        cursor = uuid.UUID(claimed.cursor) if claimed.cursor else None
        total_partitions = 0

        # Review finding F08: the realized-value refresh budget must be spent once per fleet
        # GENERATION, not reset to full on every batch. Persisted in AnalysisTask.task_state
        # (opaque, task-type-defined -- see that column's docstring) so it survives both
        # across-batch (within one invocation) and across-invocation (crash/continuation)
        # boundaries, same as `cursor` does for table position.
        refresh_budget_remaining = (
            claimed.task_state.get("refresh_budget_remaining")
            if claimed.task_state
            else None
        )
        if refresh_budget_remaining is None:
            refresh_budget_remaining = _DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP

        try:
            for batch_index in range(_MAX_BATCHES_PER_INVOCATION):
                # Heartbeat BEFORE the batch's expensive work, not just after -- detects a lost
                # lease (reclaimed by another owner after this one's lease expired) before
                # spending the batch's own compute/commit cost, per F05's docstring above.
                refreshed_generation = heartbeat(
                    session, claimed.id, lease_generation=lease_generation, lease_seconds=_LEASE_SECONDS
                )
                if refreshed_generation is None:
                    logger.warning(
                        "Lost lease on benefit_score_sweep task %s before batch %d "
                        "(lease_generation=%d) -- another owner has superseded this attempt; "
                        "exiting without starting further work.",
                        claimed.id,
                        batch_index,
                        lease_generation,
                    )
                    return 1
                lease_generation = refreshed_generation

                start = datetime.now(UTC)
                result = update_all_benefit_scores_batch(
                    session, cursor=cursor, refresh_budget_remaining=refresh_budget_remaining
                )
                elapsed = (datetime.now(UTC) - start).total_seconds()
                total_partitions += result.partitions_updated
                refresh_budget_remaining = max(
                    0, refresh_budget_remaining - result.realized_value_refreshes_spent
                )
                logger.info(
                    "Batch %d: %d tables, %d partitions scored in %.1fs (cursor -> %s, done=%s, "
                    "realized_value_refreshes_spent=%d, budget_remaining=%d)",
                    batch_index,
                    result.tables_processed,
                    result.partitions_updated,
                    elapsed,
                    result.next_cursor,
                    result.done,
                    result.realized_value_refreshes_spent,
                    refresh_budget_remaining,
                )

                lease_generation = commit_progress(
                    session,
                    claimed.id,
                    lease_generation=lease_generation,
                    cursor=str(result.next_cursor) if result.next_cursor else None,
                    processed_delta=result.tables_processed,
                    task_state={"refresh_budget_remaining": refresh_budget_remaining},
                    lease_seconds=_LEASE_SECONDS,
                )

                if result.done:
                    finish_task(
                        session, claimed.id, lease_generation=lease_generation, status="succeeded"
                    )
                    logger.info(
                        "benefit_score_sweep task %s completed the full fleet "
                        "(%d partitions scored across this and prior invocations' batches).",
                        claimed.id,
                        total_partitions,
                    )
                    return 0

                cursor = result.next_cursor
        except LeaseConflict:
            # Another owner already claimed a newer generation or reclaimed an expired lease --
            # per commit_progress/finish_task's own contract, this is fatal: stop immediately
            # without attempting finish_task (which would itself raise the same conflict).
            logger.warning(
                "Lease conflict on benefit_score_sweep task %s -- another owner has superseded "
                "this attempt; exiting without further writes.",
                claimed.id,
            )
            return 1
        except Exception:
            logger.exception("Unhandled error during benefit_score_sweep batch processing")
            try:
                finish_task(
                    session,
                    claimed.id,
                    lease_generation=lease_generation,
                    status="failed",
                    failure_reason="unhandled_exception",
                )
            except LeaseConflict:
                pass
            return 1

        # Exhausted _MAX_BATCHES_PER_INVOCATION without finishing the fleet -- this is expected
        # steady-state for a large fleet, not an error. Review finding F03: the old version just
        # returned here, leaving the row at status='running' with a live lease -- unclaimable by
        # claim_due_task's WHERE clause (only matches 'pending'/'leased') until that lease
        # naturally expired, even though this is a clean, immediate-re-dispatch-safe exit, not a
        # crash. yield_task releases the row back to 'pending' (preserving cursor/
        # processed_count) so the coordinator's very next tick can dispatch a continuation Job
        # instead of waiting out a lease window that no longer serves any purpose.
        logger.info(
            "Reached max batches (%d) for this invocation; %d partitions scored so far. "
            "Yielding task back to pending at cursor=%s for the next invocation.",
            _MAX_BATCHES_PER_INVOCATION,
            total_partitions,
            cursor,
        )
        if not yield_task(session, claimed.id, lease_generation=lease_generation):
            # Already lost the lease between the last commit_progress and here -- another owner
            # will (or already did) pick up the continuation; nothing further for this process
            # to do, and forcing a yield here would be a no-op at best, a conflict at worst.
            logger.warning(
                "Could not yield benefit_score_sweep task %s (lease already superseded) -- "
                "another owner will continue it.",
                claimed.id,
            )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cryo-analysis-runner",
        description="Bounded one-shot analysis task runner (K8s Job entrypoint)",
    )
    parser.add_argument(
        "task_type",
        choices=[_BENEFIT_SCORE_SWEEP],
        help="Which analysis task type to claim and process",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    quiet_third_party_loggers()

    if args.task_type == _BENEFIT_SCORE_SWEEP:
        return run_benefit_score_sweep_task()
    return 1  # unreachable given argparse choices, kept for exhaustiveness


if __name__ == "__main__":
    sys.exit(main())
