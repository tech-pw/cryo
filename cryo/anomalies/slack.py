"""Slack Block Kit payload for shared-S3 warehouse anomalies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cryo.anomalies.paths import OverlapDetection, OverlapGroup
from cryo.config import get_settings
from cryo.reporting.slack import (
    SlackBlock,
    context_block,
    divider_block,
    header_block,
    section_block,
    table_block,
)

MAX_SLACK_GROUPS = 12
MAX_NAMES_PER_GROUP = 4
BOTTOM_LINE = (
    "One warehouse path must map to one catalog name. DROP / expire / orphan-remove "
    "on an alias deletes the sibling table's files."
)


def build_shared_s3_slack_blocks(
    detection: OverlapDetection,
    *,
    generated_at: datetime,
    source_count: int,
) -> list[SlackBlock]:
    date_str = generated_at.strftime("%B %d, %Y")
    overlapping_tables = sum(group.table_count for group in detection.groups)
    blocks: list[SlackBlock] = [
        header_block(f"Shared S3 path anomalies — {date_str}"),
        section_block(
            f":red_circle: *{len(detection.groups)} group"
            f"{'' if len(detection.groups) == 1 else 's'}*, "
            f"{overlapping_tables} table"
            f"{'' if overlapping_tables == 1 else 's'} share a warehouse"
        ),
        section_block(BOTTOM_LINE),
        divider_block(),
        table_block(
            ["Shared path", "Kind", "Tables"],
            [
                [
                    _truncate(group.shared_path, 80),
                    group.kind,
                    _format_members(group),
                ]
                for group in detection.groups[:MAX_SLACK_GROUPS]
            ],
        ),
    ]
    leftover = len(detection.groups) - MAX_SLACK_GROUPS
    if leftover > 0:
        blocks.append(section_block(f"_…and {leftover} more group{'s' if leftover != 1 else ''}._"))
    blocks.append(divider_block())
    footer = (
        f"Harvested {source_count} catalog{'s' if source_count != 1 else ''} · "
        f"{detection.usable_count} tables with an S3 location"
    )
    anomalies_url = _anomalies_url()
    if anomalies_url:
        footer = f"{footer} · <{anomalies_url}|Open in Cryo>"
    blocks.append(context_block([footer]))
    return blocks


def shared_s3_fallback_text(detection: OverlapDetection, generated_at: datetime) -> str:
    date_str = generated_at.strftime("%Y-%m-%d")
    return (
        f"Cryo shared S3 path anomalies {date_str}: "
        f"{len(detection.groups)} groups, "
        f"{sum(group.table_count for group in detection.groups)} tables"
    )


def _format_members(group: OverlapGroup) -> str:
    names = [member.qualified_name for member in group.tables[:MAX_NAMES_PER_GROUP]]
    leftover = group.table_count - len(names)
    if leftover > 0:
        names.append(f"+{leftover} more")
    return ", ".join(names)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"…{value[-(limit - 1) :]}"


def _anomalies_url() -> str | None:
    base = get_settings().web_app_base_url
    if not base:
        return None
    return f"{base.rstrip('/')}/anomalies"


def blocks_as_dicts(blocks: list[SlackBlock]) -> list[dict[str, Any]]:
    return [block.to_dict() for block in blocks]
