"""Ask Cryo conversation retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.ai.assistant.conversations import create_conversation
from cryo.ai.assistant.retention import purge_expired_conversations
from cryo.api.security import Principal, Role
from cryo.db.models import AssistantConversation


def test_purge_archives_conversations_past_retention(session) -> None:
    principal = Principal(subject="viewer-1", roles=frozenset({Role.VIEWER}))
    old = create_conversation(session, principal, title="old", context={})
    recent = create_conversation(session, principal, title="recent", context={})
    old.updated_at = datetime.now(UTC) - timedelta(days=120)
    session.flush()
    removed = purge_expired_conversations(session, retention_days=90)
    assert removed == 1
    assert session.get(AssistantConversation, old.id).deleted_at is not None
    assert session.get(AssistantConversation, recent.id).deleted_at is None
