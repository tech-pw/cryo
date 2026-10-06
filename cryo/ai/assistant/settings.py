"""Tenant assistant settings. Missing row means the install defaults, writes off."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.db.models import AssistantSettingsRow

_ZERO = uuid.UUID(int=0)


@dataclass(frozen=True)
class AssistantSettings:
    enabled: bool
    allowed_min_role: str
    enabled_tiers: tuple[int, ...]
    send_row_values: bool
    retention_days: int
    team_instructions_skillset: str | None


def load_assistant_settings(
    session: Session,
    tenant_id: uuid.UUID | None,
    *,
    install: Settings | None = None,
) -> AssistantSettings:
    install = install or get_settings()
    key = tenant_id or _ZERO
    row = session.get(AssistantSettingsRow, key)
    if row is None:
        return AssistantSettings(
            enabled=install.assistant_enabled,
            allowed_min_role="viewer",
            enabled_tiers=(0,),
            send_row_values=False,
            retention_days=install.assistant_retention_days,
            team_instructions_skillset=None,
        )
    stored_tiers = row.enabled_tiers
    tiers = tuple(int(item) for item in stored_tiers) if stored_tiers is not None else (0,)
    return AssistantSettings(
        enabled=bool(row.enabled and install.assistant_enabled),
        allowed_min_role=row.allowed_min_role,
        enabled_tiers=tiers,
        send_row_values=bool(row.send_row_values),
        retention_days=int(row.retention_days),
        team_instructions_skillset=row.team_instructions_skillset,
    )


def save_assistant_settings(
    session: Session,
    tenant_id: uuid.UUID | None,
    *,
    enabled: bool,
    allowed_min_role: str,
    enabled_tiers: list[int],
    send_row_values: bool,
    retention_days: int,
    team_instructions_skillset: str | None,
    updated_by: str,
) -> AssistantSettingsRow:
    key = tenant_id or _ZERO
    row = session.get(AssistantSettingsRow, key)
    if row is None:
        row = AssistantSettingsRow(tenant_id=key)
        session.add(row)
    row.enabled = enabled
    row.allowed_min_role = allowed_min_role
    row.enabled_tiers = [int(item) for item in enabled_tiers if int(item) < 3]
    row.send_row_values = send_row_values
    row.retention_days = retention_days
    row.team_instructions_skillset = team_instructions_skillset
    row.updated_by = updated_by
    row.updated_at = datetime.now(UTC)
    session.flush()
    return row
