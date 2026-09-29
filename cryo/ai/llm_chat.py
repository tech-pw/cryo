"""Provider-specific tool-calling chat. Serialization stays in this module."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from cryo.ai.llm import ChatMessage, ChatResult, ToolCall, ToolDef


def _anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            rendered.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.text or "",
                        }
                    ],
                }
            )
            continue
        if message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.text:
                content.append({"type": "text", "text": message.text})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            rendered.append({"role": "assistant", "content": content})
            continue
        rendered.append({"role": message.role, "content": message.text or ""})
    return rendered


def _openai_messages(messages: list[ChatMessage], system: str) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if message.role == "tool":
            rendered.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.text or "",
                }
            )
            continue
        if message.role == "assistant" and message.tool_calls:
            rendered.append(
                {
                    "role": "assistant",
                    "content": message.text or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            )
            continue
        rendered.append({"role": message.role, "content": message.text or ""})
    return rendered


def _parse_anthropic(data: dict[str, Any], *, provider: str) -> ChatResult:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            raw = block.get("input") or {}
            calls.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    arguments=raw if isinstance(raw, dict) else {},
                )
            )
    usage = data.get("usage") or {}
    stop = "tool_use" if calls else str(data.get("stop_reason") or "end_turn")
    return ChatResult(
        text="".join(text_parts),
        tool_calls=calls,
        stop_reason=stop,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        model=str(data.get("model") or ""),
        provider=provider,
    )


def _parse_openai(data: dict[str, Any], *, provider: str, model: str) -> ChatResult:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments = function.get("arguments") or "{}"
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = arguments if isinstance(arguments, dict) else {}
        calls.append(
            ToolCall(
                id=str(raw.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=parsed,
            )
        )
    usage = data.get("usage") or {}
    return ChatResult(
        text=str(message.get("content") or ""),
        tool_calls=calls,
        stop_reason="tool_use" if calls else str(choice.get("finish_reason") or "stop"),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        model=str(data.get("model") or model),
        provider=provider,
    )


async def anthropic_chat(
    *,
    api_key: str,
    model: str,
    url: str,
    messages: list[ChatMessage],
    system: str,
    tools: list[ToolDef],
    max_tokens: int,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
) -> ChatResult:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": _anthropic_messages(messages),
    }
    if tools:
        payload["tools"] = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in tools
        ]
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    result = _parse_anthropic(data, provider="anthropic")
    if on_text_delta and result.text:
        await on_text_delta(result.text)
    return result


async def openai_compatible_chat(
    *,
    url: str,
    headers: dict[str, str],
    model: str,
    provider: str,
    messages: list[ChatMessage],
    system: str,
    tools: list[ToolDef],
    max_tokens: int,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
) -> ChatResult:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _openai_messages(messages, system),
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    result = _parse_openai(data, provider=provider, model=model)
    if on_text_delta and result.text:
        await on_text_delta(result.text)
    return result
