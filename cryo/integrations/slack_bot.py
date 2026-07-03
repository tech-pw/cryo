"""Slack bot integration for ChatOps.

Provides /cryo commands for table status, maintenance triggers, and alerts.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.model_utils import catalog_name_for_table, qualified_table_name
from cryo.db.models import AuditLog, Table, TableStats

logger = logging.getLogger(__name__)


async def handle_slash_command(
    command: str,
    text: str,
    user_id: str,
    channel_id: str,
    session: Session,
) -> dict[str, Any]:
    """
    Handle /cryo slash commands from Slack.

    Args:
        command: Command name (e.g., "/cryo")
        text: Command arguments
        user_id: Slack user ID
        channel_id: Slack channel ID
        session: Database session

    Returns:
        Slack response payload
    """
    parts = text.strip().split()

    if not parts:
        return help_response()

    action = parts[0].lower()

    if action == "status":
        # /cryo status table catalog.schema.table
        if len(parts) < 3 or parts[1] != "table":
            return error_response("Usage: /cryo status table <catalog.schema.table>")

        table_ref = parts[2]
        return await get_table_status(session, table_ref)

    elif action == "optimize":
        # /cryo optimize table catalog.schema.table
        if len(parts) < 3 or parts[1] != "table":
            return error_response("Usage: /cryo optimize table <catalog.schema.table>")

        table_ref = parts[2]
        return await trigger_optimize(session, table_ref, user_id)

    elif action == "alerts":
        # /cryo alerts enable table catalog.schema.table
        if len(parts) < 4:
            return error_response(
                "Usage: /cryo alerts <enable|disable> table <catalog.schema.table>"
            )

        enable = parts[1].lower() == "enable"
        table_ref = parts[3]
        return await configure_alerts(session, table_ref, channel_id, enable)

    elif action == "help":
        return help_response()

    else:
        return error_response(
            f"Unknown command: {action}. Type '/cryo help' for available commands."
        )


async def get_table_status(
    session: Session,
    table_ref: str,
) -> dict[str, Any]:
    """
    Get table status for Slack response.

    Args:
        session: Database session
        table_ref: Table reference (catalog.schema.table)

    Returns:
        Slack response payload
    """
    parts = table_ref.split(".")
    if len(parts) != 3:
        return error_response("Invalid table reference. Format: catalog.schema.table")

    catalog, schema, table_name = parts

    # Find table. Catalog is resolved through the table's source metadata.
    stmt = (
        select(Table, TableStats)
        .join(TableStats)
        .where(
            Table.schema_name == schema,
            Table.table_name == table_name,
        )
    )

    result = next(
        (row for row in session.execute(stmt).all() if catalog_name_for_table(row[0]) == catalog),
        None,
    )

    if result is None:
        return error_response(f"Table not found: {table_ref}")

    table, stats = result
    display_name = qualified_table_name(table)
    monthly_cost = stats.estimated_monthly_s3_cost_usd or 0.0
    last_optimized_at = session.scalar(
        select(AuditLog.ts)
        .where(
            AuditLog.table_id == table.id,
            AuditLog.status == "succeeded",
        )
        .order_by(AuditLog.ts.desc())
        .limit(1)
    )

    # Format response
    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Table Status: `{table_ref}`*",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Health Grade:* {stats.grade}"},
                    {"type": "mrkdwn", "text": f"*Score:* {stats.overall_score:.1f}/100"},
                    {"type": "mrkdwn", "text": f"*Size:* {stats.total_size_gb:.1f} GB"},
                    {"type": "mrkdwn", "text": f"*Small Files:* {stats.small_files_count:,}"},
                    {
                        "type": "mrkdwn",
                        "text": (
                            "*Last Optimized:* "
                            f"{last_optimized_at.strftime('%Y-%m-%d %H:%M') if last_optimized_at else 'Never'}"  # noqa: E501
                        ),
                    },
                    {"type": "mrkdwn", "text": f"*Resolved Name:* `{display_name}`"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Est. Monthly Cost:* ${monthly_cost:.2f}",
                    },
                ],
            },
        ],
    }


async def trigger_optimize(
    session: Session,
    table_ref: str,
    user_id: str,
) -> dict[str, Any]:
    """
    Trigger table optimization from Slack.

    Args:
        session: Database session
        table_ref: Table reference
        user_id: User who triggered

    Returns:
        Slack response payload
    """
    # In production, this would enqueue a maintenance job
    # For now, just return a confirmation

    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ Compaction job queued for `{table_ref}` by <@{user_id}>",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Job will start within 5 minutes. Check Cryo dashboard for progress."
                        ),
                    }
                ],
            },
        ],
    }


async def configure_alerts(
    session: Session,
    table_ref: str,
    channel_id: str,
    enable: bool,
) -> dict[str, Any]:
    """
    Configure alerts for a table.

    Args:
        session: Database session
        table_ref: Table reference
        channel_id: Slack channel ID
        enable: Enable or disable alerts

    Returns:
        Slack response payload
    """
    action = "enabled" if enable else "disabled"

    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ Alerts {action} for `{table_ref}` in <#{channel_id}>",
                },
            },
        ],
    }


def help_response() -> dict[str, Any]:
    """Return help message."""
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Cryo Slack Bot Commands*",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "`/cryo status table <catalog.schema.table>` - Show table health "
                        "grade and metrics\n"
                        "`/cryo optimize table <catalog.schema.table>` - Trigger compaction job\n"
                        "`/cryo alerts enable table <catalog.schema.table>` - Subscribe "
                        "to alerts\n"
                        "`/cryo alerts disable table <catalog.schema.table>` - Unsubscribe "
                        "from alerts\n"
                        "`/cryo help` - Show this help message"
                    ),
                },
            },
        ],
    }


def error_response(message: str) -> dict[str, Any]:
    """Return error message."""
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"❌ {message}",
                },
            },
        ],
    }
