"""Background job to update AutoComp benefit scores.

Runs periodically after query log ingestion to refresh partition priorities.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from threading import Event

from cryo.config import get_settings
from cryo.db.session import SessionLocal
from cryo.planner.benefit_score import update_all_benefit_scores

logger = logging.getLogger(__name__)


def run_benefit_score_update() -> int:
    """Run one benefit score refresh pass and return partitions updated."""
    with SessionLocal() as session:
        return update_all_benefit_scores(session)


def benefit_score_updater_loop_sync(*, stop_event: Event | None = None) -> None:
    """Blocking loop suitable for CLI daemon threads."""
    settings = get_settings()
    interval_seconds = settings.benefit_score_update_interval_hours * 3600
    logger.info(
        "Starting benefit score updater (interval: %sh)",
        settings.benefit_score_update_interval_hours,
    )

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            logger.info("Updating AutoComp benefit scores for all tables...")
            start = datetime.now(UTC)
            partitions_updated = run_benefit_score_update()
            elapsed = (datetime.now(UTC) - start).total_seconds()
            logger.info(
                "Updated benefit scores for %s partitions in %.1fs",
                partitions_updated,
                elapsed,
            )
        except Exception as exc:
            logger.error("Error updating benefit scores: %s", exc, exc_info=True)

        if stop_event is None:
            time.sleep(interval_seconds)
            continue
        if stop_event.wait(interval_seconds):
            return


async def benefit_score_updater_loop() -> None:
    """Async background loop that updates benefit scores every N hours."""
    settings = get_settings()
    interval_seconds = settings.benefit_score_update_interval_hours * 3600
    logger.info(
        "Starting benefit score updater (interval: %sh)",
        settings.benefit_score_update_interval_hours,
    )

    while True:
        try:
            logger.info("Updating AutoComp benefit scores for all tables...")
            start = datetime.now(UTC)
            partitions_updated = run_benefit_score_update()
            elapsed = (datetime.now(UTC) - start).total_seconds()
            logger.info(
                "Updated benefit scores for %s partitions in %.1fs",
                partitions_updated,
                elapsed,
            )
        except Exception as exc:
            logger.error("Error updating benefit scores: %s", exc, exc_info=True)

        await asyncio.sleep(interval_seconds)


def start_benefit_score_updater() -> None:
    """Start the benefit score updater as a background asyncio task."""
    asyncio.create_task(benefit_score_updater_loop())
