from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.assistant.conversations import create_conversation
from cryo.ai.assistant.coordinator import run_turn
from cryo.ai.assistant.settings import AssistantSettings
from cryo.ai.llm import ChatResult, MockLLMClient, ToolCall
from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.registry import ToolContext, ToolSpec, get_registry
from cryo.api.security import Principal, Role
from cryo.config import get_settings
from cryo.db.models import AssistantActionProposal


def test_prepare_calls_in_one_turn_run_sequentially(session: Session) -> None:
    principal = Principal(subject="operator-1", roles=frozenset({Role.OPERATOR}))
    conversation = create_conversation(session, principal, title="actions", context={})
    windows: list[dict[str, object]] = []
    names = ("prepare_scan_stub", "prepare_save_stub")

    def _handler(tool_name: str):
        def handler(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
            started = time.monotonic()
            thread_id = threading.get_ident()
            try:
                time.sleep(0.2)
                proposal_id = uuid.uuid4()
                now = datetime.now(UTC)
                ctx.session.add(
                    AssistantActionProposal(
                        id=proposal_id,
                        conversation_id=ctx.conversation_id,
                        tenant_id=ctx.principal.tenant_id,
                        owner_subject=ctx.principal.subject,
                        action_type=tool_name.removeprefix("prepare_"),
                        tier=1,
                        arguments={"tool": tool_name},
                        required_role=Role.OPERATOR.value,
                        status="awaiting_authorization",
                        expires_at=now + timedelta(minutes=10),
                        idempotency_key=str(proposal_id),
                        created_at=now,
                        updated_at=now,
                    )
                )
                ctx.session.flush()
                return EvidenceEnvelope(
                    tool_name=tool_name,
                    result={"proposal": {"id": str(proposal_id)}},
                    classification="inferred",
                    row_count=1,
                )
            finally:
                windows.append(
                    {
                        "tool": tool_name,
                        "thread": thread_id,
                        "start": started,
                        "end": time.monotonic(),
                    }
                )

        return handler

    class EmptyIn(BaseModel):
        pass

    registry = get_registry()
    for name in names:
        registry.register(
            ToolSpec(
                name=name,
                family="actions",
                description="stub prepare",
                input_model=EmptyIn,
                output_model=EvidenceEnvelope,
                tier=1,
                min_role=Role.OPERATOR,
                handler=_handler(name),
            )
        )
    client = MockLLMClient(
        script=[
            ChatResult(
                text="",
                tool_calls=[
                    ToolCall(id="p1", name=names[0], arguments={}),
                    ToolCall(id="p2", name=names[1], arguments={}),
                ],
                stop_reason="tool_use",
                input_tokens=1,
                output_tokens=1,
                model="mock",
                provider="mock",
            ),
            ChatResult(
                text="Both proposals are ready.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=1,
                output_tokens=1,
                model="mock",
                provider="mock",
            ),
        ]
    )
    settings = AssistantSettings(
        enabled=True,
        allowed_min_role="viewer",
        enabled_tiers=(0, 1),
        send_row_values=False,
        retention_days=90,
        team_instructions_skillset=None,
    )

    async def _collect() -> list[object]:
        return [
            event
            async for event in run_turn(
                session,
                principal,
                conversation_id=conversation.id,
                user_text="zz-proposal-batch",
                page_context={},
                mentions=[],
                settings=settings,
                install=get_settings(),
                client=client,
            )
        ]

    try:
        events = asyncio.run(_collect())
    finally:
        for name in names:
            registry.specs.pop(name, None)
    assert [item["tool"] for item in windows] == list(names)
    ordered = sorted(windows, key=lambda item: float(item["start"]))
    assert float(ordered[0]["end"]) <= float(ordered[1]["start"])
    assert len({item["thread"] for item in windows}) >= 1
    stored = session.scalar(
        select(func.count())
        .select_from(AssistantActionProposal)
        .where(AssistantActionProposal.conversation_id == conversation.id)
    )
    assert stored == 2
    assert any(getattr(event, "type", None) == "proposal.created" for event in events)
