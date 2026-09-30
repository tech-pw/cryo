from __future__ import annotations

import asyncio
from typing import Any

from cryo.ai.llm import ChatMessage
from cryo.ai.llm_chat import openai_compatible_chat


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)

    def json(self) -> dict[str, Any]:
        return self._body


class _Client:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
        del url, headers
        self.payloads.append(json)
        if "max_tokens" in json:
            return _Response(
                400,
                {"error": {"message": "Use 'max_completion_tokens' instead."}},
            )
        return _Response(
            200,
            {
                "model": "gpt-5.4-nano",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )


def test_chat_retries_with_max_completion_tokens(monkeypatch) -> None:
    client = _Client()
    monkeypatch.setattr("cryo.ai.llm_chat.httpx.AsyncClient", lambda timeout: client)
    result = asyncio.run(
        openai_compatible_chat(
            url="https://example.test/chat",
            headers={},
            model="gpt-5.4-nano",
            provider="azure_openai",
            messages=[ChatMessage(role="user", text="hi")],
            system="You are Ask Cryo.",
            tools=[],
            max_tokens=64,
        )
    )
    assert result.text == "hello"
    assert "max_completion_tokens" in client.payloads[1]
    assert "max_tokens" not in client.payloads[1]
