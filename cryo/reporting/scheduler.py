"""Scheduler for daily reports."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from cryo.db.models import SlackConfig
from cryo.db.session import SessionLocal
from cryo.reporting import SlackClient, generate_daily_report
from cryo.scheduler.cron import cron_is_due
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _slack_webhook_url(stored: str | None) -> str | None:
    return decrypt_secret(stored)


class ReportScheduler:
    """Scheduler for periodic reports (e.g., daily anomaly report)."""

    name = "report-scheduler"

    def __init__(self) -> None:
        self._last_runs: dict[str, datetime] = {}  # config_id -> last_run
        self._last_shared_s3_run: datetime | None = None
        self._last_query_cost_run: datetime | None = None
        self._last_health_check_run: datetime | None = None
        self._running = False

    async def start(self, poll_interval_sec: int = 60) -> None:
        """Start the report scheduler loop."""
        logger.info("Starting report scheduler")
        self._running = True

        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Report scheduler tick failed")

            await asyncio.sleep(poll_interval_sec)

    def run(self) -> None:
        """Sync entry point matching DaemonRunner's ``.run()`` shape (cryo/cli.py), so this
        drops into the same Thread(target=runner.run)/runner.stop() wiring as the other
        background daemons without special-casing its async start() there."""
        asyncio.run(self.start())

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False

    async def _tick(self) -> None:
        """Check if any reports are due and generate them."""
        now = datetime.now(UTC)
        session = SessionLocal()

        try:
            # Get all enabled Slack configs with daily reports enabled
            configs = session.scalars(
                select(SlackConfig).where(
                    SlackConfig.enabled.is_(True),
                    SlackConfig.daily_report_enabled.is_(True),
                )
            ).all()

            for config in configs:
                if not config.webhook_url_encrypted:
                    continue

                last_run = self._last_runs.get(str(config.id))
                if cron_is_due(config.daily_report_cron, now=now, last_run_at=last_run):
                    logger.info(f"Daily report is due for Slack config '{config.name}'")
                    await self._send_daily_report(config)
                    self._last_runs[str(config.id)] = now

            await self._maybe_send_shared_s3_report(now)
            await self._maybe_send_query_cost_report(now)
            await self._maybe_send_production_health_check(now)
        finally:
            session.close()

    async def _maybe_send_shared_s3_report(self, now: datetime) -> None:
        from cryo.anomalies.report import (
            SHARED_S3_REPORT_CRON,
            SHARED_S3_REPORT_TZ,
            notify_shared_s3_report,
            refresh_shared_s3_report,
        )

        if not cron_is_due(
            SHARED_S3_REPORT_CRON,
            now=now,
            last_run_at=self._last_shared_s3_run,
            tz=SHARED_S3_REPORT_TZ,
        ):
            return

        logger.info("Shared S3 anomaly report is due (10:00 IST)")
        session = SessionLocal()
        try:
            snapshot = refresh_shared_s3_report(session)
            result = await notify_shared_s3_report(session, snapshot)
            if result.get("slack_skipped_reason") == "no_anomalies":
                logger.info("Shared S3 harvest found no overlapping paths; Slack skipped")
            elif result.get("slack_sent"):
                logger.info("Shared S3 anomaly report sent to Slack")
            else:
                logger.warning(
                    "Shared S3 anomalies found but Slack was not sent (%s)",
                    result.get("slack_skipped_reason"),
                )
            self._last_shared_s3_run = now
        except Exception:
            logger.exception("Failed to run shared S3 anomaly report")
        finally:
            session.close()

    async def _maybe_send_query_cost_report(self, now: datetime) -> None:
        from cryo.reporting.query_cost_report import (
            QUERY_COST_REPORT_CRON,
            QUERY_COST_REPORT_TZ,
            notify_query_cost_report,
        )

        if not cron_is_due(
            QUERY_COST_REPORT_CRON,
            now=now,
            last_run_at=self._last_query_cost_run,
            tz=QUERY_COST_REPORT_TZ,
        ):
            return

        logger.info("Trino query-cost report is due (10:15 IST)")
        session = SessionLocal()
        try:
            result = await notify_query_cost_report(session, now=now)
            if result.get("slack_sent"):
                logger.info("Trino query-cost report sent to Slack")
            else:
                logger.warning(
                    "Trino query-cost report ready but Slack was not sent (%s)",
                    result.get("slack_skipped_reason"),
                )
            self._last_query_cost_run = now
        except Exception:
            logger.exception("Failed to run Trino query-cost report")
        finally:
            session.close()

    async def _maybe_send_production_health_check(self, now: datetime) -> None:
        from cryo.reporting.health_check_report import (
            PRODUCTION_HEALTH_CHECK_CRON,
            PRODUCTION_HEALTH_CHECK_TZ,
            notify_production_health_check,
        )

        if not cron_is_due(
            PRODUCTION_HEALTH_CHECK_CRON,
            now=now,
            last_run_at=self._last_health_check_run,
            tz=PRODUCTION_HEALTH_CHECK_TZ,
        ):
            return

        logger.info("Production health check is due (09:45 IST)")
        session = SessionLocal()
        try:
            result = await notify_production_health_check(session, now=now)
            if result.get("slack_sent"):
                logger.info(
                    "Production health check sent to Slack (%s)", result.get("overall_status")
                )
            else:
                logger.warning(
                    "Production health check ready but Slack was not sent (%s)",
                    result.get("slack_skipped_reason"),
                )
            self._last_health_check_run = now
        except Exception:
            logger.exception("Failed to run production health check")
        finally:
            session.close()

    async def _send_daily_report(self, config: SlackConfig) -> None:
        """Generate and send the daily report."""
        if config.webhook_url_encrypted is None:
            logger.warning("Skipping Slack config '%s' without a webhook URL", config.name)
            return

        session = SessionLocal()
        try:
            slack_client = SlackClient(
                _slack_webhook_url(config.webhook_url_encrypted) or "",
                config.channel,
            )
            res = await generate_daily_report(
                session,
                slack_client=slack_client,
                use_ai_summary=config.daily_report_use_ai,
            )
            if res.get("slack_sent"):
                logger.info(f"Daily report sent successfully to '{config.name}'")
            else:
                logger.warning(
                    "Daily report generated but Slack notification failed to send for '%s'",
                    config.name,
                )
        except Exception:
            logger.exception(f"Failed to send daily report to '{config.name}'")
        finally:
            session.close()


async def run_daily_report_now() -> dict:
    """Run the daily report immediately (for manual trigger)."""
    session = SessionLocal()

    try:
        # Get the default Slack config
        slack_config = session.scalar(
            select(SlackConfig).where(
                SlackConfig.is_default.is_(True),
                SlackConfig.enabled.is_(True),
            )
        )
        if slack_config is None:
            slack_config = session.scalar(
                select(SlackConfig).where(SlackConfig.enabled.is_(True)).limit(1)
            )

        slack_client = None
        use_ai = True
        if slack_config and slack_config.webhook_url_encrypted:
            slack_client = SlackClient(
                _slack_webhook_url(slack_config.webhook_url_encrypted) or "",
                slack_config.channel,
            )
            use_ai = slack_config.daily_report_use_ai

        result = await generate_daily_report(
            session,
            slack_client=slack_client,
            use_ai_summary=use_ai,
        )
        return result
    finally:
        session.close()


def main() -> None:
    """Entry point for running the report scheduler."""
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    scheduler = ReportScheduler()

    try:
        asyncio.run(scheduler.start())
    except KeyboardInterrupt:
        scheduler.stop()


if __name__ == "__main__":
    main()
