"""Ask Cryo HTTP API. Execution stays in the action gateway, not in these handlers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.ai.actions.gateway import ActionGateway
from cryo.ai.assistant.conversations import (
    add_feedback,
    append_user_message,
    clear_share_token,
    conversation_by_share_token,
    create_conversation,
    get_owned,
    list_owned,
    set_share_token,
    soft_delete,
)
from cryo.ai.assistant.coordinator import request_stop, run_turn
from cryo.ai.assistant.events import AssistantEvent, format_sse
from cryo.ai.assistant.replay import replay, turn_slots_left
from cryo.ai.assistant.settings import load_assistant_settings, save_assistant_settings
from cryo.ai.assistant.starters import starters_for
from cryo.ai.llm import get_llm_client
from cryo.api.deps import CurrentPrincipal, SessionDep, require_admin, require_viewer
from cryo.api.security import Principal, Role
from cryo.config import get_settings
from cryo.db.models import (
    AssistantApiKey,
    AssistantConversation,
    AssistantEvidence,
    AssistantMessage,
)

router = APIRouter(prefix="/api/v1", tags=["assistant"])


class ConversationCreate(BaseModel):
    title: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ConversationPatch(BaseModel):
    title: str | None = None
    status: str | None = None


class MessageCreate(BaseModel):
    text: str
    context: dict[str, Any] = Field(default_factory=dict)
    mentions: list[dict[str, Any]] = Field(default_factory=list)
    client_message_id: str


class FeedbackCreate(BaseModel):
    rating: int
    reason: str | None = None


class AuthorizeBody(BaseModel):
    note: str | None = None
    idempotency_key: str
    channel: str = "ui_card"


class SettingsBody(BaseModel):
    enabled: bool
    allowed_min_role: str = "viewer"
    enabled_tiers: list[int] = Field(default_factory=lambda: [0])
    send_row_values: bool = False
    retention_days: int = 90
    team_instructions_skillset: str | None = None


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])


def _conversation_read(row: AssistantConversation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "context": row.context,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _allowed(principal: Principal, session: Session) -> None:
    install = get_settings()
    settings = load_assistant_settings(session, principal.tenant_id, install=install)
    if not settings.enabled:
        raise HTTPException(status_code=403, detail="Ask Cryo is disabled")
    if not principal.can(Role(settings.allowed_min_role)):
        raise HTTPException(status_code=403, detail="Role cannot use Ask Cryo")


@router.get("/assistant/capabilities", dependencies=[Depends(require_viewer)])
def capabilities(session: SessionDep, principal: CurrentPrincipal) -> dict[str, Any]:
    install = get_settings()
    settings = load_assistant_settings(session, principal.tenant_id, install=install)
    client = get_llm_client(session)
    reasons: list[str] = []
    if not install.assistant_enabled:
        reasons.append("assistant_disabled")
    if not install.assistant_writes_enabled:
        reasons.append("assistant_writes_disabled")
    if not install.maintenance_writes_enabled:
        reasons.append("maintenance_writes_disabled")
    return {
        "enabled": settings.enabled,
        "provider_configured": client is not None,
        "supports_tools": bool(client and client.supports_tools),
        "tiers_enabled": list(settings.enabled_tiers),
        "writes_enabled": install.assistant_writes_enabled and install.maintenance_writes_enabled,
        "role": settings.allowed_min_role,
        "reasons": reasons,
    }


@router.get("/assistant/starters", dependencies=[Depends(require_viewer)])
def starters(
    session: SessionDep,
    route: str | None = None,
    table_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, str]]:
    return starters_for(route, table_id, run_id)


@router.post("/assistant/conversations", dependencies=[Depends(require_viewer)])
def post_conversation(
    payload: ConversationCreate, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    _allowed(principal, session)
    row = create_conversation(session, principal, title=payload.title, context=payload.context)
    session.commit()
    return _conversation_read(row)


@router.get("/assistant/conversations", dependencies=[Depends(require_viewer)])
def get_conversations(
    session: SessionDep,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    parsed = datetime.fromisoformat(cursor) if cursor else None
    rows = list_owned(session, principal, limit=limit, cursor=parsed)
    return {"items": [_conversation_read(row) for row in rows]}


@router.get("/assistant/conversations/{conversation_id}", dependencies=[Depends(require_viewer)])
def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    row = get_owned(session, principal, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = session.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == row.id)
        .order_by(AssistantMessage.seq.desc())
        .limit(50)
    ).all()
    return {
        **_conversation_read(row),
        "messages": [
            {
                "id": str(message.id),
                "seq": message.seq,
                "role": message.role,
                "text": message.text,
                "blocks": message.blocks,
                "status": message.status,
                "verified_handler": message.verified_handler,
                "grounding": message.grounding,
            }
            for message in reversed(messages)
        ],
    }


@router.patch("/assistant/conversations/{conversation_id}", dependencies=[Depends(require_viewer)])
def patch_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationPatch,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    row = get_owned(session, principal, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.title is not None:
        row.title = payload.title
    if payload.status is not None:
        row.status = payload.status
    session.commit()
    return _conversation_read(row)


@router.delete(
    "/assistant/conversations/{conversation_id}",
    status_code=204,
    dependencies=[Depends(require_viewer)],
)
def delete_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> None:
    row = get_owned(session, principal, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    soft_delete(session, row)
    session.commit()


@router.post("/assistant/conversations/{conversation_id}/messages")
async def post_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> StreamingResponse:
    _allowed(principal, session)
    install = get_settings()
    if turn_slots_left(principal.subject, install.assistant_max_active_turns_per_user) <= 0:
        raise HTTPException(
            status_code=429,
            detail="Too many active Ask Cryo turns.",
            headers={"Retry-After": "30"},
        )
    conversation = get_owned(session, principal, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _user, created = append_user_message(
        session,
        conversation,
        text=payload.text,
        client_message_id=payload.client_message_id,
    )
    install = get_settings()
    settings = load_assistant_settings(session, principal.tenant_id, install=install)
    client = get_llm_client(session)

    async def generate() -> Any:
        if not created:
            prior = session.scalar(
                select(AssistantMessage)
                .where(
                    AssistantMessage.conversation_id == conversation.id,
                    AssistantMessage.role == "assistant",
                    AssistantMessage.status.in_(("complete", "stopped", "error")),
                )
                .order_by(AssistantMessage.seq.desc())
            )
            if prior is not None and prior.blocks:
                event = AssistantEvent(
                    message_id=str(prior.id),
                    seq=1,
                    type="message.completed",
                    payload={"blocks": prior.blocks, "grounding": prior.grounding},
                )
                yield format_sse(event)
                return
        try:
            async for event in run_turn(
                session,
                principal,
                conversation_id=conversation.id,
                user_text=payload.text,
                page_context=payload.context,
                mentions=payload.mentions,
                settings=settings,
                install=install,
                client=client,
            ):
                yield format_sse(event)
            session.commit()
        except Exception:
            session.rollback()
            failed = AssistantEvent(
                message_id="0",
                seq=1,
                type="error",
                payload={"code": "internal", "message": "Turn failed."},
            )
            yield format_sse(failed)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/assistant/messages/{message_id}/events", dependencies=[Depends(require_viewer)])
def message_events(
    message_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
    after_seq: int = 0,
) -> StreamingResponse:
    message = session.get(AssistantMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    conversation = get_owned(session, principal, message.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Message not found")

    def generate() -> Any:
        if message.status == "complete" and message.blocks:
            event = AssistantEvent(
                message_id=str(message.id),
                seq=after_seq + 1,
                type="message.completed",
                payload={"blocks": message.blocks, "grounding": message.grounding},
            )
            yield format_sse(event)
            return
        for item in replay(str(message_id), after_seq=after_seq):
            yield format_sse(AssistantEvent.model_validate(item))

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post(
    "/assistant/messages/{message_id}/stop",
    status_code=202,
    dependencies=[Depends(require_viewer)],
)
def stop_message(
    message_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, str]:
    message = session.get(AssistantMessage, message_id)
    if message is None or get_owned(session, principal, message.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    request_stop(str(message_id))
    return {"status": "cancellation_requested"}


@router.post("/assistant/messages/{message_id}/feedback", dependencies=[Depends(require_viewer)])
def post_feedback(
    message_id: uuid.UUID,
    payload: FeedbackCreate,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> dict[str, str]:
    message = session.get(AssistantMessage, message_id)
    if message is None or get_owned(session, principal, message.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if payload.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    add_feedback(
        session,
        message_id=message_id,
        principal=principal,
        rating=payload.rating,
        reason=payload.reason,
    )
    session.commit()
    return {"status": "recorded"}


@router.get("/assistant/evidence/{evidence_id}", dependencies=[Depends(require_viewer)])
def get_evidence(
    evidence_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    row = session.get(AssistantEvidence, evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return {
        "evidence_id": str(row.id),
        "classification": row.classification,
        "coverage": row.coverage,
        "result": row.result,
        "scope": row.scope,
    }


@router.post(
    "/assistant/conversations/{conversation_id}/share",
    dependencies=[Depends(require_viewer)],
)
def share_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, str]:
    row = get_owned(session, principal, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    token = set_share_token(session, row)
    session.commit()
    return {"url": f"/assistant/shared/{token}"}


@router.delete(
    "/assistant/conversations/{conversation_id}/share",
    status_code=204,
    dependencies=[Depends(require_viewer)],
)
def unshare_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> None:
    row = get_owned(session, principal, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    clear_share_token(session, row)
    session.commit()


@router.get("/assistant/shared/{token}", dependencies=[Depends(require_viewer)])
def get_shared(token: str, session: SessionDep, principal: CurrentPrincipal) -> dict[str, Any]:
    row = conversation_by_share_token(session, token)
    if row is None:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    if principal.tenant_id and row.tenant_id and principal.tenant_id != row.tenant_id:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    return {**_conversation_read(row), "read_only": True}


@router.get("/assistant/settings", dependencies=[Depends(require_admin)])
def get_settings_route(session: SessionDep, principal: CurrentPrincipal) -> dict[str, Any]:
    settings = load_assistant_settings(session, principal.tenant_id)
    return {
        "enabled": settings.enabled,
        "allowed_min_role": settings.allowed_min_role,
        "enabled_tiers": list(settings.enabled_tiers),
        "send_row_values": settings.send_row_values,
        "retention_days": settings.retention_days,
        "team_instructions_skillset": settings.team_instructions_skillset,
    }


@router.put("/assistant/settings", dependencies=[Depends(require_admin)])
def put_settings(
    payload: SettingsBody, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, str]:
    if payload.allowed_min_role not in {"viewer", "operator", "admin"}:
        raise HTTPException(status_code=400, detail="Unknown role")
    save_assistant_settings(
        session,
        principal.tenant_id,
        enabled=payload.enabled,
        allowed_min_role=payload.allowed_min_role,
        enabled_tiers=payload.enabled_tiers,
        send_row_values=payload.send_row_values,
        retention_days=payload.retention_days,
        team_instructions_skillset=payload.team_instructions_skillset,
        updated_by=principal.subject,
    )
    session.commit()
    return {"status": "saved"}


@router.get("/assistant/usage", dependencies=[Depends(require_admin)])
def usage(session: SessionDep, group_by: str = "day") -> dict[str, Any]:
    from cryo.db.models import LLMObservation

    rows = session.scalars(
        select(LLMObservation)
        .where(LLMObservation.feature == "ask_cryo")
        .order_by(LLMObservation.recorded_at.desc())
        .limit(500)
    ).all()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.principal_subject or "unknown"
        if group_by == "day" and row.recorded_at is not None:
            key = row.recorded_at.date().isoformat()
        bucket = grouped.setdefault(
            key, {"key": key, "turns": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None}
        )
        bucket["turns"] += 1
        bucket["input_tokens"] += row.input_tokens
        bucket["output_tokens"] += row.output_tokens
        if row.estimated_cost_usd is None:
            bucket["cost_usd"] = None
        elif bucket["cost_usd"] is not None:
            bucket["cost_usd"] += row.estimated_cost_usd
    return {"group_by": group_by, "items": list(grouped.values())}


@router.get("/assistant/api-keys", dependencies=[Depends(require_admin)])
def list_api_keys(session: SessionDep) -> dict[str, Any]:
    rows = session.scalars(
        select(AssistantApiKey).where(AssistantApiKey.revoked_at.is_(None))
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "name": row.name,
                "scopes": row.scopes,
                "created_at": row.created_at,
                "last_used_at": row.last_used_at,
            }
            for row in rows
        ]
    }


@router.post("/assistant/api-keys", dependencies=[Depends(require_admin)])
def create_api_key(
    payload: ApiKeyCreate, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    allowed = {"read", "maintenance:propose"}
    if any(scope not in allowed for scope in payload.scopes):
        raise HTTPException(status_code=400, detail="Unknown API key scope")
    raw = secrets.token_urlsafe(32)
    row = AssistantApiKey(
        tenant_id=principal.tenant_id,
        name=payload.name,
        key_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        scopes=payload.scopes,
        created_by=principal.subject,
    )
    session.add(row)
    session.commit()
    return {"id": str(row.id), "key": raw, "scopes": payload.scopes}


@router.delete(
    "/assistant/api-keys/{key_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
def revoke_api_key(key_id: uuid.UUID, session: SessionDep) -> None:
    row = session.get(AssistantApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    row.revoked_at = datetime.now(UTC)
    session.commit()


@router.get("/assistant/schema/events", dependencies=[Depends(require_viewer)])
def event_schema() -> dict[str, Any]:
    return AssistantEvent.model_json_schema()


@router.get("/assistant/proposals/{proposal_id}", dependencies=[Depends(require_viewer)])
def get_proposal(
    proposal_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    return _proposal_call(session, principal, proposal_id, "status")


@router.post("/assistant/proposals/{proposal_id}/authorize", dependencies=[Depends(require_viewer)])
def authorize_proposal(
    proposal_id: uuid.UUID,
    payload: AuthorizeBody,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    if payload.channel not in {"ui_card", "approval_link"}:
        raise HTTPException(status_code=400, detail="Typed chat is not an approval channel")
    try:
        body = _proposal_call(
            session,
            principal,
            proposal_id,
            "authorize",
            channel=payload.channel,
            note=payload.note,
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _raise_gateway(exc)
    session.commit()
    return body


@router.post("/assistant/proposals/{proposal_id}/reject", dependencies=[Depends(require_viewer)])
def reject_proposal(
    proposal_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> dict[str, Any]:
    body = _proposal_call(session, principal, proposal_id, "reject")
    session.commit()
    return body


def _proposal_call(
    session: Session,
    principal: Principal,
    proposal_id: uuid.UUID,
    method: str,
    **kwargs: Any,
) -> dict[str, Any]:
    gateway = ActionGateway(session)
    fn = getattr(gateway, method)
    if method == "status":
        proposal = gateway.status(principal, proposal_id)
    else:
        proposal = fn(principal, proposal_id, **kwargs)
    return _proposal_read(proposal)


def _proposal_read(proposal: Any) -> dict[str, Any]:
    return {
        "id": str(proposal.id),
        "action_type": proposal.action_type,
        "tier": proposal.tier,
        "status": proposal.status,
        "preview": proposal.preview,
        "plan_hash": proposal.plan_hash,
        "target_digest": proposal.target_digest,
        "expires_at": proposal.expires_at,
        "run_id": str(proposal.run_id) if proposal.run_id else None,
        "job_ids": proposal.job_ids,
        "superseded_by": str(proposal.superseded_by) if proposal.superseded_by else None,
        "last_error": proposal.last_error,
        "required_role": proposal.required_role,
    }


def _raise_gateway(exc: Exception) -> None:
    name = type(exc).__name__
    if name == "Conflict":
        revised = getattr(exc, "revised_proposal_id", None)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "plan_changed",
                "revised_proposal_id": str(revised) if revised else None,
            },
        ) from exc
    if name in {"PermissionError", "Forbidden"}:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc
