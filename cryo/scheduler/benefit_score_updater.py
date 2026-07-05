"""Background job to update AutoComp benefit scores.

Runs periodically after query log ingestion to refresh partition priorities.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.session import get_session_maker
from cryo.planner.benefit_score import update_all_benefit_scores

logger = logging.getLogger(__name__)


async def benefit_score_updater_loop():
    """Background loop that updates benefit scores every N hours."""
    settings = get_settings()
    interval_hours = settings.benefit_score_update_interval_hours
    interval_seconds = interval_hours * 3600
    
    SessionMaker = get_session_maker()
    
    logger.info(f"Starting benefit score updater (interval: {interval_hours}h)")
    
    while True:
        try:
            with SessionMaker() as session:
                logger.info("Updating AutoComp benefit scores for all tables...")
                start = datetime.now(UTC)
                
                partitions_updated = update_all_benefit_scores(session)
                
                elapsed = (datetime.now(UTC) - start).total_seconds()
                logger.info(
                    f"Updated benefit scores for {partitions_updated} partitions "
                    f"in {elapsed:.1f}s"
                )
        
        except Exception as e:
            logger.error(f"Error updating benefit scores: {e}", exc_info=True)
        
        # Sleep until next update
        await asyncio.sleep(interval_seconds)


def start_benefit_score_updater():
    """Start the benefit score updater as a background task."""
    asyncio.create_task(benefit_score_updater_loop())
