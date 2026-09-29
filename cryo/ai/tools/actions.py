"""Prepare-only actions. Tier-3 operations are not tools and nothing is executed."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from cryo.ai.actions.gateway import ActionGateway
from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import make_envelope
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role

_TIER3 = {"purge", "force", "deactivate", "restore", "force-admit"}


class PrepareInput(BaseModel):
    action: str
    table_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    params: dict[str, Any] = Field(default_factory=dict)


def _normalize(action: str) -> str:
    return action.strip().lower().replace("_", "-")


def _ui_link(action: str, spec: PrepareInput) -> str:
    if action in {"force", "force-admit"} and spec.run_id is not None:
        return f"/runs/{spec.run_id}"
    if spec.table_id is not None:
        return f"/tables/{spec.table_id}"
    if spec.run_id is not None:
        return f"/runs/{spec.run_id}"
    return "/tables"


def prepare_action(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = PrepareInput.model_validate(parsed.model_dump())
    action = _normalize(spec.action)
    if action in _TIER3:
        link = _ui_link(action, spec)
        return make_envelope(
            "prepare_action",
            {
                "prepared": False,
                "executed": False,
                "action": action,
                "ui_link": link,
                "error": "not_a_tool",
            },
            ctx=ctx,
            classification="unknown",
            error_category="not_a_tool",
            warnings=[f"{action} is a tier-3 UI action, not a tool. Open {link}."],
            source_links=[{"rel": "ui", "href": link}],
        )
    gateway_cls = ActionGateway
    if getattr(gateway_cls, "prepare", None) is None:
        return make_envelope(
            "prepare_action",
            {"prepared": False, "executed": False, "action": action},
            ctx=ctx,
            classification="unknown",
            error_category="unsupported",
            warnings=["ActionGateway.prepare is not available. Nothing was executed."],
        )
    args = dict(spec.params)
    if spec.table_id is not None:
        args["table_id"] = str(spec.table_id)
    if spec.run_id is not None:
        args["run_id"] = str(spec.run_id)
    if spec.job_id is not None:
        args["job_id"] = str(spec.job_id)
    try:
        proposal = gateway_cls(ctx.session).prepare(
            ctx.principal,
            action.replace("-", "_"),
            args,
            ctx.conversation_id,
            None,
        )
    except ValueError as exc:
        return make_envelope(
            "prepare_action",
            {"prepared": False, "executed": False, "action": action, "error": str(exc)},
            ctx=ctx,
            classification="unknown",
            error_category="unsupported",
            warnings=[str(exc)],
        )
    prepared = {
        "prepared": True,
        "executed": False,
        "proposal": {
            "id": str(proposal.id),
            "action_type": proposal.action_type,
            "tier": proposal.tier,
            "status": proposal.status,
            "preview": proposal.preview,
            "expires_at": proposal.expires_at.isoformat() if proposal.expires_at else None,
        },
    }
    prepared.setdefault("action", action)
    return make_envelope(
        "prepare_action",
        prepared,
        ctx=ctx,
        classification="inferred",
        warnings=["This is a proposal only. Nothing was executed."],
        measurement_method="action_gateway",
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="prepare_action",
        family="actions",
        description="Prepare a proposal. Does not execute. Tier-3 actions return a UI link.",
        input_model=PrepareInput,
        output_model=EvidenceEnvelope,
        tier=1,
        min_role=Role.OPERATOR,
        handler=prepare_action,
    ),
)
