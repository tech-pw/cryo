"""Ask Cryo turn loop without a provider."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from cryo.ai.assistant.conversations import create_conversation
from cryo.ai.assistant.coordinator import run_turn
from cryo.ai.assistant.settings import AssistantSettings
from cryo.ai.llm import ChatResult, MockLLMClient, ToolCall
from cryo.api.security import Principal, Role
from cryo.config import get_settings
from cryo.db.models import AssistantMessage


@pytest.fixture()
def principal() -> Principal:
    return Principal(subject="operator-1", roles=frozenset({Role.OPERATOR}))


def _events(session, principal, conversation_id, text, settings) -> list:
    async def _collect():
        return [
            event
            async for event in run_turn(
                session,
                principal,
                conversation_id=conversation_id,
                user_text=text,
                page_context={},
                mentions=[],
                settings=settings,
                install=get_settings(),
                client=None,
            )
        ]

    return asyncio.run(_collect())


def test_turn_without_provider_abstains(session, principal: Principal) -> None:
    conversation = create_conversation(session, principal, title="health", context={})
    settings = AssistantSettings(
        enabled=True,
        allowed_min_role="viewer",
        enabled_tiers=(0,),
        send_row_values=False,
        retention_days=90,
        team_instructions_skillset=None,
    )
    events = _events(session, principal, conversation.id, "How healthy is the lake?", settings)
    types = [event.type for event in events]
    assert "message.accepted" in types
    assert "message.completed" in types
    assert "error" not in types
    completed = session.get(AssistantMessage, uuid.UUID(events[0].message_id))
    assert completed is not None
    assert completed.status == "complete"
    assert any(block.get("type") == "gap" for block in (completed.blocks or []))


def test_disabled_assistant_errors(session, principal: Principal) -> None:
    conversation = create_conversation(session, principal, title="off", context={})
    settings = AssistantSettings(
        enabled=False,
        allowed_min_role="viewer",
        enabled_tiers=(0,),
        send_row_values=False,
        retention_days=90,
        team_instructions_skillset=None,
    )
    events = _events(session, principal, conversation.id, "hello", settings)
    assert events[0].type == "error"
    assert events[0].payload["code"] == "forbidden"


def test_tool_loop_uses_registry_and_does_not_execute(session, principal: Principal) -> None:
    from pydantic import BaseModel

    from cryo.ai.tools.evidence import EvidenceEnvelope
    from cryo.ai.tools.registry import ToolContext, ToolSpec, get_registry
    from cryo.api.security import Role

    class EmptyIn(BaseModel):
        pass

    class EmptyOut(BaseModel):
        pass

    def handler(ctx: ToolContext, parsed: EmptyIn) -> EvidenceEnvelope:
        return EvidenceEnvelope(
            tool_name="get_lake_health",
            result={"grade": "C", "score": 61},
            row_count=1,
            classification="observed",
        )

    registry = get_registry()
    original_spec = registry.specs.get("get_lake_health")
    registry.register(
        ToolSpec(
            name="get_lake_health",
            family="health",
            description="Fleet health",
            input_model=EmptyIn,
            output_model=EmptyOut,
            tier=0,
            min_role=Role.VIEWER,
            handler=handler,
        )
    )
    try:
        client = MockLLMClient(
            script=[
                ChatResult(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="get_lake_health", arguments={})],
                    stop_reason="tool_use",
                    input_tokens=3,
                    output_tokens=2,
                    model="mock",
                    provider="mock",
                ),
                ChatResult(
                    text="The lake grade is C.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    input_tokens=4,
                    output_tokens=5,
                    model="mock",
                    provider="mock",
                ),
            ]
        )
        conversation = create_conversation(session, principal, title="tools", context={})
        settings = AssistantSettings(
            enabled=True,
            allowed_min_role="viewer",
            enabled_tiers=(0,),
            send_row_values=False,
            retention_days=90,
            team_instructions_skillset=None,
        )

        async def _collect():
            return [
                event
                async for event in run_turn(
                    session,
                    principal,
                    conversation_id=conversation.id,
                    user_text="How healthy is the lake?",
                    page_context={},
                    mentions=[],
                    settings=settings,
                    install=get_settings(),
                    client=client,
                )
            ]

        events = asyncio.run(_collect())
        types = [event.type for event in events]
        assert "step.completed" in types
        assert "proposal.created" not in types
        completed = next(event for event in events if event.type == "message.completed")
        assert completed.payload["text"] == "The lake grade is C."
        assert not any(event.type == "answer.delta" for event in events)
    finally:
        # This mutates the module-level _REGISTRY singleton, which is shared across the
        # whole pytest process -- confirmed live 2026-09-29 that leaving the fake spec in
        # place broke test_assistant_tenant_scope.py, run later in the suite, with a
        # KeyError on the real get_lake_health's "active_tables" field. Restore (or remove,
        # if this test somehow ran before the real tool was ever registered) so later tests
        # see the real handler.
        if original_spec is not None:
            registry.register(original_spec)
        else:
            registry.specs.pop("get_lake_health", None)


def test_invented_number_is_not_streamed(session, principal: Principal) -> None:
    from pydantic import BaseModel

    from cryo.ai.tools.evidence import EvidenceEnvelope
    from cryo.ai.tools.registry import ToolContext, ToolSpec, get_registry
    from cryo.api.security import Role

    class EmptyIn(BaseModel):
        pass

    class EmptyOut(BaseModel):
        pass

    def handler(ctx: ToolContext, parsed: EmptyIn) -> EvidenceEnvelope:
        return EvidenceEnvelope(
            tool_name="get_lake_health",
            result={"grade": "C"},
            row_count=1,
            classification="observed",
        )

    registry = get_registry()
    original_spec = registry.specs.get("get_lake_health")
    registry.register(
        ToolSpec(
            name="get_lake_health",
            family="health",
            description="Fleet health",
            input_model=EmptyIn,
            output_model=EmptyOut,
            tier=0,
            min_role=Role.VIEWER,
            handler=handler,
        )
    )
    try:
        client = MockLLMClient(
            script=[
                ChatResult(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="get_lake_health", arguments={})],
                    stop_reason="tool_use",
                    input_tokens=1,
                    output_tokens=1,
                    model="mock",
                    provider="mock",
                ),
                ChatResult(
                    text="The score is 999.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    input_tokens=1,
                    output_tokens=1,
                    model="mock",
                    provider="mock",
                ),
                ChatResult(
                    text="The score is 999 again.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    input_tokens=1,
                    output_tokens=1,
                    model="mock",
                    provider="mock",
                ),
            ]
        )
        conversation = create_conversation(session, principal, title="ground", context={})
        settings = AssistantSettings(
            enabled=True,
            allowed_min_role="viewer",
            enabled_tiers=(0,),
            send_row_values=False,
            retention_days=90,
            team_instructions_skillset=None,
        )

        async def _collect():
            return [
                event
                async for event in run_turn(
                    session,
                    principal,
                    conversation_id=conversation.id,
                    user_text="Summarise the stored grade.",
                    page_context={},
                    mentions=[],
                    settings=settings,
                    install=get_settings(),
                    client=client,
                )
            ]

        events = asyncio.run(_collect())
        assert not any(
            "999" in str(event.payload) and event.type == "answer.delta" for event in events
        )
        completed = next(event for event in events if event.type == "message.completed")
        assert completed.payload["grounding"] == "fallback"
        assert completed.payload["text"] == "Could not summarise this reliably."
        blocks = completed.payload["blocks"]
        assert any(block.get("type") == "ranked_table" for block in blocks)
    finally:
        # Same registry-leak fix as test_tool_loop_uses_registry_and_does_not_execute above --
        # see that finally block's comment for the incident this prevents.
        if original_spec is not None:
            registry.register(original_spec)
        else:
            registry.specs.pop("get_lake_health", None)
