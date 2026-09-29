"""Action types Ask Cryo may propose. Tier 3 names are absent on purpose."""

from __future__ import annotations

from dataclasses import dataclass

from cryo.api.security import Role

TIER3_ACTION_TYPES = frozenset(
    {
        "purge",
        "purge_table",
        "purge_snapshots",
        "force",
        "force_expire",
        "force_admit",
        "force-admit",
        "deactivate",
        "deactivate_table",
        "restore",
        "restore_table",
        "delete",
        "delete_table",
        "drop_table",
    }
)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_type: str
    tier: int
    min_role: Role
    requires_note: bool = False
    requires_reason: bool = False


def _spec(
    action_type: str,
    tier: int,
    role: Role,
    *,
    note: bool = False,
    reason: bool = False,
) -> ActionSpec:
    if tier >= 3:
        raise ValueError("tier 3 actions are not registered")
    return ActionSpec(
        action_type=action_type,
        tier=tier,
        min_role=role,
        requires_note=note,
        requires_reason=reason,
    )


CATALOG: dict[str, ActionSpec] = {
    spec.action_type: spec
    for spec in (
        _spec("scan_table", 1, Role.OPERATOR),
        _spec("save_view", 1, Role.VIEWER),
        _spec("subscribe_alerts", 1, Role.VIEWER),
        _spec("protect_snapshot", 1, Role.OPERATOR),
        _spec("unprotect_snapshot", 1, Role.OPERATOR),
        _spec("dismiss_unused_table", 1, Role.OPERATOR, reason=True),
        _spec("run_maintenance", 2, Role.OPERATOR),
        _spec("act_on_recommendation", 2, Role.OPERATOR),
        _spec("act_on_approval", 2, Role.OPERATOR),
        _spec("pause_maintenance", 2, Role.OPERATOR, note=True),
        _spec("resume_maintenance", 2, Role.OPERATOR, note=True),
        _spec("cancel_run", 2, Role.OPERATOR, note=True),
        _spec("set_table_override", 2, Role.OPERATOR, note=True),
        _spec("update_preset", 2, Role.OPERATOR),
    )
}


def normalize_action_type(action_type: str) -> str:
    return action_type.strip().lower().replace(" ", "_").replace("-", "_")


def is_tier3(action_type: str) -> bool:
    normalized = normalize_action_type(action_type)
    denied = {normalize_action_type(name) for name in TIER3_ACTION_TYPES}
    if normalized in denied:
        return True
    return "purge" in normalized or normalized.startswith(("delete", "drop"))


def get_action(action_type: str) -> ActionSpec:
    if is_tier3(action_type):
        raise ValueError(f"action {action_type} is not available")
    spec = CATALOG.get(normalize_action_type(action_type))
    if spec is None or spec.tier >= 3:
        raise ValueError(f"unknown action {action_type}")
    return spec
