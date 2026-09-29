"""Server-owned Ask Cryo conversations. Access is checked on every read."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.security import Principal
from cryo.db.models import (
    AssistantConversation,
    AssistantEvidence,
    AssistantFeedback,
    AssistantMessage,
    AssistantToolCall,
)


def create_conversation(
    session: Session,
    principal: Principal,
    *,
    title: str | None,
    context: dict[str, Any],
) -> AssistantConversation:
    row = AssistantConversation(
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        title=title,
        context=context,
        status="active",
    )
    session.add(row)
    session.flush()
    return row


def get_owned(
    session: Session,
    principal: Principal,
    conversation_id: uuid.UUID,
    *,
    allow_share: bool = False,
) -> AssistantConversation | None:
    row = session.get(AssistantConversation, conversation_id)
    if row is None or row.deleted_at is not None:
        return None
    if row.owner_subject == principal.subject and _same_tenant(row, principal):
        return row
    if allow_share and row.share_token_hash:
        return row if _same_tenant(row, principal) else None
    return None


def list_owned(
    session: Session,
    principal: Principal,
    *,
    limit: int = 30,
    cursor: datetime | None = None,
) -> list[AssistantConversation]:
    stmt = (
        select(AssistantConversation)
        .where(
            AssistantConversation.owner_subject == principal.subject,
            AssistantConversation.deleted_at.is_(None),
        )
        .order_by(AssistantConversation.updated_at.desc())
        .limit(min(limit, 50))
    )
    if principal.tenant_id is not None:
        stmt = stmt.where(AssistantConversation.tenant_id == principal.tenant_id)
    if cursor is not None:
        stmt = stmt.where(AssistantConversation.updated_at < cursor)
    return list(session.scalars(stmt))


def append_user_message(
    session: Session,
    conversation: AssistantConversation,
    *,
    text: str,
    client_message_id: str,
) -> tuple[AssistantMessage, bool]:
    existing = session.scalar(
        select(AssistantMessage).where(
            AssistantMessage.conversation_id == conversation.id,
            AssistantMessage.client_message_id == client_message_id,
        )
    )
    if existing is not None:
        return existing, False
    seq = int(
        session.scalar(
            select(func.coalesce(func.max(AssistantMessage.seq), 0)).where(
                AssistantMessage.conversation_id == conversation.id
            )
        )
        or 0
    )
    row = AssistantMessage(
        conversation_id=conversation.id,
        seq=seq + 1,
        role="user",
        text=text,
        status="complete",
        client_message_id=client_message_id,
    )
    session.add(row)
    conversation.updated_at = datetime.now(UTC)
    session.flush()
    return row, True


def start_assistant_message(
    session: Session, conversation: AssistantConversation
) -> AssistantMessage:
    seq = int(
        session.scalar(
            select(func.coalesce(func.max(AssistantMessage.seq), 0)).where(
                AssistantMessage.conversation_id == conversation.id
            )
        )
        or 0
    )
    row = AssistantMessage(
        conversation_id=conversation.id,
        seq=seq + 1,
        role="assistant",
        status="streaming",
        grounding="n/a",
    )
    session.add(row)
    session.flush()
    return row


def finish_message(
    session: Session,
    message: AssistantMessage,
    *,
    text: str,
    blocks: list[dict[str, Any]],
    status: str,
    verified_handler: str | None,
    grounding: str,
) -> None:
    message.text = text
    message.blocks = blocks
    message.status = status
    message.verified_handler = verified_handler
    message.grounding = grounding
    message.completed_at = datetime.now(UTC)
    session.flush()


def record_tool_call(
    session: Session,
    message_id: uuid.UUID,
    *,
    step: int,
    tool_name: str,
    arguments: dict[str, Any],
    status: str,
    duration_ms: int,
    row_count: int,
    truncated: bool,
    error_category: str | None,
    evidence_id: uuid.UUID | None,
) -> AssistantToolCall:
    row = AssistantToolCall(
        message_id=message_id,
        step=step,
        tool_name=tool_name,
        arguments=arguments,
        status=status,
        duration_ms=duration_ms,
        row_count=row_count,
        truncated=truncated,
        error_category=error_category,
        evidence_id=evidence_id,
    )
    session.add(row)
    session.flush()
    return row


def record_evidence(
    session: Session,
    *,
    evidence_id: uuid.UUID,
    tool_call_id: uuid.UUID | None,
    envelope: dict[str, Any],
) -> AssistantEvidence:
    row = AssistantEvidence(
        id=evidence_id,
        tool_call_id=tool_call_id,
        schema_version=int(envelope.get("schema_version") or 1),
        scope=envelope.get("authorized_scope"),
        classification=str(envelope.get("classification") or "unknown"),
        coverage=envelope.get("coverage"),
        source_refs=envelope.get("source_record_ids"),
        result=envelope.get("result"),
        result_digest=hashlib.sha256(str(envelope.get("result")).encode("utf-8")).hexdigest(),
    )
    session.add(row)
    session.flush()
    return row


def recent_messages(
    session: Session, conversation_id: uuid.UUID, *, limit: int = 12
) -> list[AssistantMessage]:
    rows = session.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation_id)
        .order_by(AssistantMessage.seq.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def soft_delete(session: Session, conversation: AssistantConversation) -> None:
    conversation.deleted_at = datetime.now(UTC)
    conversation.status = "archived"
    session.flush()


def set_share_token(session: Session, conversation: AssistantConversation) -> str:
    token = secrets.token_urlsafe(24)
    conversation.share_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session.flush()
    return token


def clear_share_token(session: Session, conversation: AssistantConversation) -> None:
    conversation.share_token_hash = None
    session.flush()


def conversation_by_share_token(session: Session, token: str) -> AssistantConversation | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return session.scalar(
        select(AssistantConversation).where(
            AssistantConversation.share_token_hash == digest,
            AssistantConversation.deleted_at.is_(None),
        )
    )


def add_feedback(
    session: Session,
    *,
    message_id: uuid.UUID,
    principal: Principal,
    rating: int,
    reason: str | None,
) -> AssistantFeedback:
    row = AssistantFeedback(
        message_id=message_id,
        principal_subject=principal.subject,
        rating=rating,
        reason=reason,
    )
    session.add(row)
    session.flush()
    return row


def _same_tenant(row: AssistantConversation, principal: Principal) -> bool:
    if principal.tenant_id is None or row.tenant_id is None:
        return True
    return row.tenant_id == principal.tenant_id
