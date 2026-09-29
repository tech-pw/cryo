"""One Ask Cryo turn. The model may call tools; it cannot authorize or execute."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from cryo.ai.assistant.context import resolve
from cryo.ai.assistant.conversations import (
    finish_message,
    recent_messages,
    record_evidence,
    record_tool_call,
    start_assistant_message,
)
from cryo.ai.assistant.events import AssistantEvent
from cryo.ai.assistant.replay import acquire_turn, is_stopped, mark_stopped, publish, release_turn
from cryo.ai.assistant.settings import AssistantSettings
from cryo.ai.assistant.tool_scope import forbidden_envelope, install_tenant_guard
from cryo.ai.llm import ChatMessage, LLMClient, ToolDef
from cryo.ai.tools.bootstrap import register_builtin_tools
from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.registry import ToolContext, get_registry
from cryo.api.security import Principal, Role
from cryo.config import Settings
from cryo.db.models import LLMObservation
from cryo.observability.metrics import (
    assistant_grounding_total,
    assistant_tool_calls_total,
    assistant_turns_total,
)

install_tenant_guard()


def request_stop(message_id: str) -> None:
    mark_stopped(str(message_id))


def active_turns(subject: str) -> int:
    del subject
    return 0


async def run_turn(
    session: Session,
    principal: Principal,
    *,
    conversation_id: uuid.UUID,
    user_text: str,
    page_context: dict[str, Any] | None,
    mentions: list[dict[str, Any]] | None,
    settings: AssistantSettings,
    install: Settings,
    client: LLMClient | None = None,
) -> AsyncIterator[AssistantEvent]:
    if not settings.enabled:
        yield _error("forbidden", "Ask Cryo is disabled for this installation.")
        return
    if not principal.can(Role(settings.allowed_min_role)):
        yield _error("forbidden", "Your role cannot use Ask Cryo.")
        return
    if not acquire_turn(principal.subject, install.assistant_max_active_turns_per_user):
        yield _error("budget_denied", "Too many active Ask Cryo turns.")
        return
    started = time.monotonic()
    message = start_assistant_message(session, _conversation(session, conversation_id))
    seq = 0
    blocks: list[dict[str, Any]] = []
    evidence_payloads: list[dict[str, Any]] = []
    verified_handler: str | None = None
    grounding = "n/a"
    answer_text = ""
    input_tokens = 0
    output_tokens = 0
    try:
        seq, event = _emit(
            message.id,
            seq,
            "message.accepted",
            {"conversation_id": str(conversation_id)},
        )
        yield event

        resolved = _resolve(session, principal, user_text, page_context or {}, mentions or [])
        seq, event = _emit(
            message.id,
            seq,
            "context.resolved",
            {
                "entities": resolved.get("entities") or [],
                "window": resolved.get("window"),
                "timezone": resolved.get("timezone") or "Asia/Kolkata",
            },
        )
        yield event
        if resolved.get("ambiguous"):
            seq, event = _emit(
                message.id,
                seq,
                "clarification.needed",
                {
                    "question": resolved.get("question") or "Which table did you mean?",
                    "candidates": resolved.get("candidates") or [],
                },
            )
            yield event
            answer_text = str(resolved.get("question") or "Which table did you mean?")
            blocks.append({"type": "answer", "text": answer_text, "evidence_ids": []})
            grounding = "n/a"
            finish_message(
                session,
                message,
                text=answer_text,
                blocks=blocks,
                status="complete",
                verified_handler=None,
                grounding=grounding,
            )
            seq, event = _emit(message.id, seq, "message.completed", {"grounding": grounding})
            yield event
            assistant_turns_total.labels(outcome="clarification").inc()
            return

        verified = _match_verified(session, user_text, resolved)
        if verified is not None:
            verified_handler = str(verified["handler_id"])
            payload = verified["result"]
            evidence_payloads.append(payload)
            blocks.append(
                {
                    "type": "verified",
                    "handler": verified_handler,
                    "parameters": verified.get("parameters") or {},
                    "evidence_ids": [],
                }
            )
            answer_text = str(payload.get("answer") or "")
            blocks.extend(_blocks_from_payload(payload))
            for block in blocks:
                seq, event = _emit(message.id, seq, "answer.block", {"block": block})
                yield event
            grounding = "passed"
        elif client is None or not client.supports_tools:
            answer_text = (
                "Free-form questions need an AI provider. "
                "Pick a starter question for a verified answer."
            )
            blocks.append(
                {"type": "gap", "missing_input": "ai_provider", "setup_url": "/settings/ai"}
            )
            blocks.append({"type": "answer", "text": answer_text, "evidence_ids": []})
            for block in blocks:
                seq, event = _emit(message.id, seq, "answer.block", {"block": block})
                yield event
            grounding = "n/a"

        if client is not None and client.supports_tools and verified is None:
            async for event in _stream_tool_loop(
                session,
                principal,
                message_id=message.id,
                conversation_id=conversation_id,
                user_text=user_text,
                resolved=resolved,
                settings=settings,
                install=install,
                client=client,
                started=started,
                seq=seq,
            ):
                if event.type == "message.completed":
                    answer_text = str(event.payload.get("text") or answer_text)
                    blocks = list(event.payload.get("blocks") or blocks)
                    grounding = str(event.payload.get("grounding") or grounding)
                    input_tokens = int(event.payload.get("input_tokens") or 0)
                    output_tokens = int(event.payload.get("output_tokens") or 0)
                yield event
                seq = event.seq
            finish_message(
                session,
                message,
                text=answer_text,
                blocks=blocks,
                status="stopped" if is_stopped(str(message.id)) else "complete",
                verified_handler=None,
                grounding=grounding,
            )
            _observe(
                session,
                principal,
                conversation_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=client.provider_name,
                model=getattr(client, "model", "unknown"),
            )
            assistant_turns_total.labels(outcome="tools").inc()
            return

        finish_message(
            session,
            message,
            text=answer_text,
            blocks=blocks,
            status="complete",
            verified_handler=verified_handler,
            grounding=grounding,
        )
        seq, event = _emit(
            message.id,
            seq,
            "message.completed",
            {
                "verified_handler": verified_handler,
                "grounding": grounding,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": None},
            },
        )
        yield event
        assistant_turns_total.labels(outcome="verified" if verified_handler else "fallback").inc()
    except Exception:
        assistant_turns_total.labels(outcome="error").inc()
        finish_message(
            session,
            message,
            text=answer_text or "Ask Cryo could not finish this turn.",
            blocks=blocks,
            status="error",
            verified_handler=verified_handler,
            grounding="fallback",
        )
        seq, event = _emit(
            message.id, seq, "error", {"code": "internal", "message": "Turn failed."}
        )
        yield event
    finally:
        release_turn(principal.subject)


async def _stream_tool_loop(
    session: Session,
    principal: Principal,
    *,
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_text: str,
    resolved: dict[str, Any],
    settings: AssistantSettings,
    install: Settings,
    client: LLMClient,
    started: float,
    seq: int,
) -> AsyncIterator[AssistantEvent]:
    register_builtin_tools()
    registry = get_registry()
    specs = registry.visible(
        principal,
        max_tier=2,
        enabled_tiers=set(settings.enabled_tiers),
        writes_enabled=install.assistant_writes_enabled,
    )
    tool_defs = [
        ToolDef(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )
        for spec in specs
    ]
    history = _history(session, conversation_id, message_id, user_text)
    evidence_payloads: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    calls = 0
    input_tokens = 0
    output_tokens = 0
    system = _system_prompt(session, resolved, settings)
    ctx = ToolContext(
        session=session,
        principal=principal,
        settings=install,
        now=datetime.now(UTC),
        timezone=str(resolved.get("timezone") or "Asia/Kolkata"),
        conversation_id=conversation_id,
        send_row_values=settings.send_row_values,
    )
    while calls < install.assistant_max_tool_calls:
        if is_stopped(str(message_id)):
            break
        if time.monotonic() - started > install.assistant_turn_timeout_s:
            seq, event = _emit(
                message_id, seq, "error", {"code": "timeout", "message": "Turn time limit reached."}
            )
            yield event
            break
        result = await client.chat(
            history,
            system=system,
            tools=tool_defs,
            max_tokens=install.llm_per_call_max_tokens_default,
        )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        if not result.tool_calls:
            answer, grounding, extra = await _grounded_answer(
                client,
                history,
                system,
                tool_defs,
                result.text,
                evidence_payloads,
                install,
            )
            input_tokens += extra[0]
            output_tokens += extra[1]
            if grounding == "fallback":
                from cryo.ai.assistant.grounding import evidence_fallback_table

                answer = "Could not summarise this reliably."
                blocks.append(evidence_fallback_table(evidence_payloads))
            else:
                blocks.append(
                    {
                        "type": "answer",
                        "text": answer,
                        "as_of": None,
                        "scope": {},
                        "evidence_ids": [],
                    }
                )
            for block in blocks:
                seq, event = _emit(message_id, seq, "answer.block", {"block": block})
                yield event
            seq, event = _emit(
                message_id,
                seq,
                "message.completed",
                {
                    "text": answer,
                    "blocks": blocks,
                    "grounding": grounding,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "verified_handler": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": None,
                    },
                },
            )
            yield event
            return
        history.append(
            ChatMessage(role="assistant", text=result.text or None, tool_calls=result.tool_calls)
        )
        batch = []
        for call in result.tool_calls:
            if calls >= install.assistant_max_tool_calls:
                break
            calls += 1
            batch.append((calls, call))
            label = call.name.replace("_", " ")
            seq, event = _emit(
                message_id, seq, "step.started", {"step": calls, "label": label, "tool": call.name}
            )
            yield event
        remaining = install.assistant_turn_timeout_s - (time.monotonic() - started)
        outcomes = await _run_tool_batch(
            session, registry, ctx, batch, remaining, install.assistant_max_parallel_reads
        )
        for step, call, envelope, duration_ms in outcomes:
            status = "error" if envelope.error_category else "ok"
            assistant_tool_calls_total.labels(tool=call.name, status=status).inc()
            evidence_id = uuid.UUID(envelope.evidence_id)
            tool_row = record_tool_call(
                session,
                message_id,
                step=step,
                tool_name=call.name,
                arguments=call.arguments,
                status=status,
                duration_ms=duration_ms,
                row_count=envelope.row_count,
                truncated=envelope.truncated,
                error_category=envelope.error_category,
                evidence_id=evidence_id,
            )
            record_evidence(
                session,
                evidence_id=evidence_id,
                tool_call_id=tool_row.id,
                envelope=envelope.model_dump(mode="json"),
            )
            evidence_payloads.append(envelope.model_dump(mode="json"))
            if envelope.error_category:
                seq, event = _emit(
                    message_id,
                    seq,
                    "step.failed",
                    {
                        "step": step,
                        "error_category": envelope.error_category,
                        "message": "; ".join(envelope.warnings) or envelope.error_category,
                    },
                )
            else:
                seq, event = _emit(
                    message_id,
                    seq,
                    "step.completed",
                    {
                        "step": step,
                        "evidence_id": envelope.evidence_id,
                        "row_count": envelope.row_count,
                        "truncated": envelope.truncated,
                        "duration_ms": duration_ms,
                    },
                )
            yield event
            proposal = (envelope.result or {}).get("proposal")
            if isinstance(proposal, dict) and proposal.get("id"):
                seq, event = _emit(message_id, seq, "proposal.created", {"proposal": proposal})
                yield event
                blocks.append(
                    {
                        "type": "action_review",
                        "proposal_id": proposal["id"],
                        "evidence_ids": [envelope.evidence_id],
                    }
                )
            history.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.id,
                    text=_tool_payload(envelope),
                )
            )
    grounding = _ground("", evidence_payloads)
    seq, event = _emit(
        message_id,
        seq,
        "message.completed",
        {
            "text": "Stopped before a complete answer.",
            "blocks": blocks,
            "grounding": grounding,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    yield event


async def _grounded_answer(
    client: LLMClient,
    history: list[ChatMessage],
    system: str,
    tools: list[ToolDef],
    text: str,
    evidence_payloads: list[dict[str, Any]],
    install: Settings,
) -> tuple[str, str, tuple[int, int]]:
    grounding = _ground(text, evidence_payloads)
    extra_in = 0
    extra_out = 0
    if grounding == "fallback":
        history.append(ChatMessage(role="assistant", text=text))
        history.append(
            ChatMessage(
                role="user",
                text=(
                    "That answer included numbers that are not in the tool evidence. "
                    "Answer again using only those fields."
                ),
            )
        )
        retry = await client.chat(
            history,
            system=system,
            tools=tools,
            max_tokens=install.llm_per_call_max_tokens_default,
        )
        extra_in = retry.input_tokens
        extra_out = retry.output_tokens
        text = retry.text
        grounding = _ground(text, evidence_payloads)
    return text, grounding, (extra_in, extra_out)


def _tier(registry: Any, name: str) -> int:
    spec = registry.get(name)
    return spec.tier if spec else 0


async def _run_tool_batch(
    session: Session,
    registry: Any,
    ctx: ToolContext,
    batch: list[tuple[int, Any]],
    remaining: float,
    parallel: int,
) -> list[tuple[int, Any, EvidenceEnvelope, int]]:
    if remaining <= 0:
        return [
            (
                step,
                call,
                EvidenceEnvelope(
                    tool_name=call.name,
                    classification="unknown",
                    error_category="timeout",
                    warnings=["Turn time limit reached."],
                ),
                0,
            )
            for step, call in batch
        ]
    read_sem = asyncio.Semaphore(max(1, parallel))
    write_sem = asyncio.Semaphore(1)
    reads = [item for item in batch if _tier(registry, item[1].name) < 1]
    writes = [item for item in batch if _tier(registry, item[1].name) >= 1]

    async def _one(
        step: int,
        call: Any,
        sem: asyncio.Semaphore,
    ) -> tuple[int, Any, EvidenceEnvelope, int]:
        spec = registry.get(call.name)
        timeout = min(spec.timeout_s if spec else 10.0, remaining)
        async with sem:
            began = time.monotonic()
            envelope = await _invoke_tool(
                session, registry, ctx, call.name, call.arguments, timeout
            )
            return step, call, envelope, int((time.monotonic() - began) * 1000)

    read_out = (
        list(await asyncio.gather(*(_one(step, call, read_sem) for step, call in reads)))
        if reads
        else []
    )
    write_out = (
        list(await asyncio.gather(*(_one(step, call, write_sem) for step, call in writes)))
        if writes
        else []
    )
    return read_out + write_out


async def _invoke_tool(
    session: Session,
    registry: Any,
    ctx: ToolContext,
    name: str,
    arguments: dict[str, Any],
    timeout: float,
) -> EvidenceEnvelope:
    from sqlalchemy.orm import sessionmaker
    from starlette.concurrency import run_in_threadpool

    spec = registry.get(name)
    tier = spec.tier if spec else 0

    def _read() -> EvidenceEnvelope:
        maker = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
        with maker() as tool_session:
            tool_session.info.update(session.info)
            called = registry.call(name, replace(ctx, session=tool_session), arguments)
            if not isinstance(called, EvidenceEnvelope):
                raise TypeError("tool did not return evidence")
            return called

    def _write() -> EvidenceEnvelope:
        nested = session.begin_nested()
        try:
            called = registry.call(name, ctx, arguments)
            if not isinstance(called, EvidenceEnvelope):
                raise TypeError("tool did not return evidence")
            envelope = called
            if envelope.error_category:
                nested.rollback()
            else:
                nested.commit()
            return envelope
        except Exception:
            nested.rollback()
            return EvidenceEnvelope(
                tool_name=name,
                classification="unknown",
                error_category="tool_error",
                warnings=["tool_error"],
            )

    try:
        runner = _write if tier >= 1 else _read
        return await asyncio.wait_for(run_in_threadpool(runner), timeout=max(timeout, 0.05))
    except TimeoutError:
        return EvidenceEnvelope(
            tool_name=name,
            classification="unknown",
            error_category="timeout",
            warnings=["Tool timed out."],
        )
    except HTTPException:
        return forbidden_envelope(name)


def _history(
    session: Session, conversation_id: uuid.UUID, message_id: uuid.UUID, user_text: str
) -> list[ChatMessage]:
    history: list[ChatMessage] = []
    for message in recent_messages(session, conversation_id):
        if message.id == message_id or not message.text:
            continue
        if message.role == "user" or message.role == "assistant":
            role: Literal["user", "assistant"] = "user" if message.role == "user" else "assistant"
            history.append(ChatMessage(role=role, text=message.text))
    if not history or history[-1].text != user_text:
        history.append(ChatMessage(role="user", text=user_text))
    return history


def _conversation(session: Session, conversation_id: uuid.UUID) -> Any:
    from cryo.db.models import AssistantConversation

    row = session.get(AssistantConversation, conversation_id)
    if row is None:
        raise KeyError("conversation not found")
    return row


def _resolve(
    session: Session,
    principal: Principal,
    text: str,
    page_context: dict[str, Any],
    mentions: list[dict[str, Any]],
) -> dict[str, Any]:
    return resolve(session, principal, text, page_context, mentions)


def _match_verified(
    session: Session, question: str, resolved: dict[str, Any]
) -> dict[str, Any] | None:
    from cryo.ai.tools.verified import VERIFIED_HANDLERS, match_verified

    matched = match_verified(question, resolved)
    if not matched:
        return None
    handler_id = str(matched["handler"])
    entry = VERIFIED_HANDLERS[handler_id]
    context = {**resolved, "question": question}
    result = entry["handler"](context, session)
    return {
        "handler_id": handler_id,
        "parameters": matched.get("parameters") or {},
        "result": result,
    }


def _system_prompt(session: Session, resolved: dict[str, Any], settings: AssistantSettings) -> str:
    from cryo.ai.assistant.prompts import ensure_ask_cryo_skillset, system_prompt

    text = system_prompt(
        datetime.now(UTC),
        str(resolved.get("timezone") or "Asia/Kolkata"),
        settings.team_instructions_skillset,
    )
    ensure_ask_cryo_skillset(session, text)
    return text


def _ground(text: str, evidence_payloads: list[dict[str, Any]]) -> str:
    from cryo.ai.assistant.grounding import check_numbers

    result = check_numbers(text, evidence_payloads)
    status = str(result.get("status") or "passed")
    assistant_grounding_total.labels(result=status).inc()
    return status


def _blocks_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "answer",
            "text": str(payload.get("answer") or ""),
            "as_of": payload.get("as_of"),
            "scope": payload.get("scope") or {},
            "evidence_ids": [],
        }
    ]
    tables = payload.get("tables")
    if isinstance(tables, list):
        blocks.append(
            {
                "type": "ranked_table",
                "columns": [{"key": "table", "label": "Table"}],
                "rows": tables,
                "total": _as_int(payload.get("total"), len(tables)),
                "truncated": bool(payload.get("truncated")),
                "evidence_ids": [],
            }
        )
    return blocks


def _tool_payload(envelope: Any) -> str:
    import json

    data = envelope.for_model(send_row_values=False)
    return json.dumps({"evidence_id": envelope.evidence_id, "data": data})


def _observe(
    session: Session,
    principal: Principal,
    conversation_id: uuid.UUID,
    *,
    input_tokens: int,
    output_tokens: int,
    provider: str,
    model: str,
) -> None:
    row = LLMObservation(
        provider_type=provider,
        model_id=model,
        feature="ask_cryo",
        prompt_name="ask_cryo",
        prompt_hash="ask_cryo",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=None,
        status="success",
        principal_subject=principal.subject,
        conversation_id=conversation_id,
    )
    session.add(row)
    session.flush()


def _emit(
    message_id: uuid.UUID, seq: int, event_type: str, payload: dict[str, Any]
) -> tuple[int, AssistantEvent]:
    seq += 1
    event = AssistantEvent(message_id=str(message_id), seq=seq, type=event_type, payload=payload)
    publish(str(message_id), event.model_dump(mode="json"))
    return seq, event


def _as_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _error(code: str, message: str) -> AssistantEvent:
    return AssistantEvent(
        message_id="0",
        seq=1,
        type="error",
        payload={"code": code, "message": message},
    )
