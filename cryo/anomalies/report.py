"""Build, persist, and optionally Slack-notify shared-S3 anomaly snapshots."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.anomalies.harvest import HarvestResult, harvest_all_sources
from cryo.anomalies.paths import OverlapDetection, detect_shared_s3_groups
from cryo.anomalies.slack import (
    build_shared_s3_slack_blocks,
    shared_s3_fallback_text,
)
from cryo.db.models import SharedS3AnomalyReport, SlackConfig
from cryo.reporting.slack import SlackClient
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

SHARED_S3_REPORT_CRON = "0 10 * * *"
SHARED_S3_REPORT_TZ = ZoneInfo("Asia/Kolkata")


def refresh_shared_s3_report(session: Session) -> SharedS3AnomalyReport:
    """Harvest catalogs, group overlaps, persist a snapshot, and return it."""
    generated_at = datetime.now(UTC)
    harvest = harvest_all_sources(session)
    detection = detect_shared_s3_groups(harvest.location_rows())
    report = SharedS3AnomalyReport(
        generated_at=generated_at,
        table_count=detection.usable_count,
        group_count=len(detection.groups),
        overlapping_table_count=sum(group.table_count for group in detection.groups),
        skipped_count=sum(source.skipped_count for source in harvest.sources),
        sources_scanned=_sources_payload(harvest),
        source_errors=_source_errors(harvest),
        groups=_groups_payload(detection),
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def latest_shared_s3_report(session: Session) -> SharedS3AnomalyReport | None:
    return session.scalar(
        select(SharedS3AnomalyReport).order_by(SharedS3AnomalyReport.generated_at.desc()).limit(1)
    )


def empty_report_payload() -> dict[str, Any]:
    return {
        "id": None,
        "generated_at": None,
        "sources_harvested": 0,
        "table_count": 0,
        "overlapping_group_count": 0,
        "overlapping_table_count": 0,
        "skipped_count": 0,
        "sources": [],
        "groups": [],
    }


def report_to_payload(report: SharedS3AnomalyReport) -> dict[str, Any]:
    sources = list(report.sources_scanned or [])
    return {
        "id": str(report.id),
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "sources_harvested": len(sources),
        "table_count": report.table_count,
        "overlapping_group_count": report.group_count,
        "overlapping_table_count": report.overlapping_table_count,
        "skipped_count": report.skipped_count,
        "sources": sources,
        "groups": list(report.groups or []),
    }


async def notify_shared_s3_report(
    session: Session,
    report: SharedS3AnomalyReport | None = None,
) -> dict[str, Any]:
    """Send Slack only when overlapping groups exist. Harvest if no snapshot given."""
    snapshot = report or latest_shared_s3_report(session)
    if snapshot is None:
        snapshot = refresh_shared_s3_report(session)

    payload = report_to_payload(snapshot)
    if snapshot.group_count == 0:
        payload["slack_sent"] = False
        payload["slack_skipped_reason"] = "no_anomalies"
        payload["slack_targets"] = 0
        logger.info("Shared S3 report has no overlapping groups; skipping Slack")
        return payload

    configs = session.scalars(
        select(SlackConfig).where(
            SlackConfig.enabled.is_(True),
            SlackConfig.webhook_url_encrypted.is_not(None),
        )
    ).all()
    if not configs:
        payload["slack_sent"] = False
        payload["slack_skipped_reason"] = "no_slack_config"
        payload["slack_targets"] = 0
        logger.warning("Shared S3 anomalies found but no enabled Slack webhook is configured")
        return payload

    detection = _detection_from_report(snapshot)
    generated_at = snapshot.generated_at
    blocks = build_shared_s3_slack_blocks(
        detection,
        generated_at=generated_at,
        source_count=len(snapshot.sources_scanned or []),
    )
    fallback = shared_s3_fallback_text(detection, generated_at)
    sent = 0
    for config in configs:
        webhook = decrypt_secret(config.webhook_url_encrypted)
        if not webhook:
            continue
        client = SlackClient(webhook, config.channel)
        if await client.send_blocks(blocks, fallback_text=fallback):
            sent += 1
            logger.info("Shared S3 anomaly report sent to Slack config '%s'", config.name)
        else:
            logger.warning("Shared S3 Slack send failed for config '%s'", config.name)

    payload["slack_sent"] = sent > 0
    payload["slack_skipped_reason"] = None if sent > 0 else "send_failed"
    payload["slack_targets"] = sent
    return payload


def run_shared_s3_report_now(*, send_slack: bool = False) -> dict[str, Any]:
    """CLI/Makefile entry: harvest, persist, optionally notify."""
    import asyncio

    from cryo.db.session import SessionLocal

    session = SessionLocal()
    try:
        snapshot = refresh_shared_s3_report(session)
        payload = report_to_payload(snapshot)
        if send_slack:
            payload = asyncio.run(notify_shared_s3_report(session, snapshot))
        return payload
    finally:
        session.close()


def _sources_payload(harvest: HarvestResult) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(source.source_id),
            "source_name": source.source_name,
            "source_type": source.source_type,
            "mode": source.mode,
            "table_count": source.table_count,
            "skipped_count": source.skipped_count,
            "error": source.error,
        }
        for source in harvest.sources
    ]


def _source_errors(harvest: HarvestResult) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(source.source_id),
            "source_name": source.source_name,
            "error": source.error,
        }
        for source in harvest.sources
        if source.error
    ]


def _groups_payload(detection: OverlapDetection) -> list[dict[str, Any]]:
    return [
        {
            "shared_path": group.shared_path,
            "kind": group.kind,
            "table_count": group.table_count,
            "tables": [
                {
                    "source_id": str(member.source_id),
                    "source_name": member.source_name,
                    "source_type": member.source_type,
                    "schema_name": member.schema_name,
                    "table_name": member.table_name,
                    "location": member.location,
                    "table_id": str(member.table_id) if member.table_id else None,
                    "qualified_name": member.qualified_name,
                }
                for member in group.tables
            ],
        }
        for group in detection.groups
    ]


def _detection_from_report(report: SharedS3AnomalyReport) -> OverlapDetection:
    from cryo.anomalies.paths import LocationRow

    rows: list[LocationRow] = []
    for group in report.groups or []:
        for table in group.get("tables", []):
            rows.append(
                LocationRow(
                    source_id=uuid.UUID(str(table["source_id"])),
                    source_name=str(table["source_name"]),
                    source_type=str(table["source_type"]),
                    schema_name=str(table["schema_name"]),
                    table_name=str(table["table_name"]),
                    location=str(table["location"]),
                    table_id=uuid.UUID(str(table["table_id"])) if table.get("table_id") else None,
                )
            )
    return detect_shared_s3_groups(rows)
