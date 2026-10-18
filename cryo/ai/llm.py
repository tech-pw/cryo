"""LLM client abstraction for AI providers (Anthropic, OpenAI, Azure OpenAI)."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from cryo.config import get_settings
from cryo.security.outbound import validated_https_url

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

SUPPORTED_MODEL_MAP: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ),
    "openai": (
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ),
    "azure_openai": (
        "gpt4o",
        "gpt-4",
        "gpt-4-turbo",
        "gpt-35-turbo",
        "gpt-5.4-nano",
    ),
}


def _default_models() -> dict[str, str]:
    settings = get_settings()
    return {
        "anthropic": settings.llm_default_model_anthropic,
        "openai": settings.llm_default_model_openai,
        "azure_openai": settings.llm_default_model_azure_openai,
    }


def _resolve_model(
    provider_type: str,
    requested_model: str | None,
    *,
    allow_custom: bool,
) -> str:
    defaults = _default_models()
    fallback = defaults.get(provider_type, requested_model or "unknown-model")
    model = (requested_model or "").strip() or fallback
    supported = SUPPORTED_MODEL_MAP.get(provider_type, ())
    if supported and model not in supported and not allow_custom:
        logger.warning(
            "Model %s not supported for provider %s; falling back to %s",
            model,
            provider_type,
            fallback,
        )
        return fallback
    return model


# System prompt for Cryo Copilot
CRYO_SYSTEM_PROMPT = """You are Cryo Copilot, an AI assistant for Apache Iceberg table maintenance.

You help users understand:
- Table health metrics (grades A-F, scores 0-100)
- Compaction needs (small files, partition skew)
- Cost optimization (storage costs, projected savings)
- Maintenance operations (optimize, expire_snapshots, remove_orphan_files)

Be concise and actionable. Reference specific metrics when available.
If you don't have enough information, say so clearly."""


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    provider: str


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    supports_tools: bool = False

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        context: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Generate a completion for the given prompt."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        system: str,
        tools: list[ToolDef],
        max_tokens: int,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        """Default chat has no tool calls. Providers with tools override this."""
        transcript = "\n".join(f"{message.role}: {message.text or ''}" for message in messages)
        response = await self.complete(transcript, system=system, max_tokens=max_tokens)
        if on_text_delta and response.content:
            await on_text_delta(response.content)
        return ChatResult(
            text=response.content,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            model=response.model,
            provider=response.provider,
        )


class AnthropicClient(LLMClient):
    """Anthropic Claude client."""

    supports_tools = True

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or _default_models()["anthropic"]

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        context: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        system_prompt = system or CRYO_SYSTEM_PROMPT
        if context:
            system_prompt += f"\n\nContext:\n```json\n{json.dumps(context, indent=2)}\n```"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["content"][0]["text"],
            model=data["model"],
            input_tokens=data["usage"]["input_tokens"],
            output_tokens=data["usage"]["output_tokens"],
            provider="anthropic",
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        system: str,
        tools: list[ToolDef],
        max_tokens: int,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        from cryo.ai.llm_chat import anthropic_chat

        return await anthropic_chat(
            api_key=self.api_key,
            model=self.model,
            url=ANTHROPIC_API_URL,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            on_text_delta=on_text_delta,
        )


class OpenAIClient(LLMClient):
    """OpenAI GPT client."""

    supports_tools = True

    def __init__(self, api_key: str, model: str | None = None, *, api_base: str | None = None):
        self.api_key = api_key
        self.model = model or _default_models()["openai"]
        if api_base:
            base = api_base.rstrip("/")
            self.api_url = (
                base if base.endswith("/chat/completions") else f"{base}/chat/completions"
            )
        else:
            self.api_url = OPENAI_API_URL

    @property
    def provider_name(self) -> str:
        return "openai"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        context: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        system_prompt = system or CRYO_SYSTEM_PROMPT
        if context:
            system_prompt += f"\n\nContext:\n```json\n{json.dumps(context, indent=2)}\n```"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        base_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }

        api_url = validated_https_url(self.api_url)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                api_url,
                headers=headers,
                json={**base_payload, "max_tokens": max_tokens},
            )

            if response.status_code == 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", "")
                    if "max_completion_tokens" in error_msg:
                        response = await client.post(
                            api_url,
                            headers=headers,
                            json={**base_payload, "max_completion_tokens": max_tokens},
                        )
                except Exception:
                    pass

            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data["model"],
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            provider="openai",
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        system: str,
        tools: list[ToolDef],
        max_tokens: int,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        from cryo.ai.llm_chat import openai_compatible_chat

        return await openai_compatible_chat(
            url=self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            model=self.model,
            provider="openai",
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            on_text_delta=on_text_delta,
        )


class AzureOpenAIClient(LLMClient):
    """Azure OpenAI client.

    Azure OpenAI uses a different URL structure and authentication:
    - Base URL: https://<resource>.azure-api.net or custom APIM endpoint
    - API Key via api-key header (not Bearer token)
    - Deployment name in URL path instead of model in body
    """

    supports_tools = True

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        api_base: str | None = None,
        api_version: str = "2024-03-01-preview",
    ):
        self.api_key = api_key
        self.model = model or _default_models()["azure_openai"]
        if not api_base:
            raise ValueError("Azure OpenAI requires an administrator-configured api_base")
        self.api_base = api_base
        self.api_version = api_version

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    def _build_url(self) -> str:
        """Build the Azure OpenAI API URL."""
        base = self.api_base.rstrip("/")
        # Handle both direct Azure OpenAI and APIM load-balanced endpoints
        if "/openai" in base:
            # APIM style: base already includes /openai or /openai-load-balancing
            return (
                f"{base}/deployments/{self.model}/chat/completions?api-version={self.api_version}"
            )
        else:
            # Direct Azure OpenAI style
            return (
                f"{base}/openai/deployments/{self.model}/chat/completions"
                f"?api-version={self.api_version}"
            )

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        context: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        system_prompt = system or CRYO_SYSTEM_PROMPT
        if context:
            system_prompt += f"\n\nContext:\n```json\n{json.dumps(context, indent=2)}\n```"

        url = validated_https_url(self._build_url())

        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }

        base_payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Try with max_tokens first (standard for most models)
            response = await client.post(
                url,
                headers=headers,
                json={**base_payload, "max_tokens": max_tokens},
            )

            # 400 on max_tokens → retry with max_completion_tokens (o1, nano, etc.)
            if response.status_code == 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", "")
                    if "max_completion_tokens" in error_msg:
                        response = await client.post(
                            url,
                            headers=headers,
                            json={**base_payload, "max_completion_tokens": max_tokens},
                        )
                except Exception:
                    pass

            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", self.model),
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            provider="azure_openai",
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        system: str,
        tools: list[ToolDef],
        max_tokens: int,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        from cryo.ai.llm_chat import openai_compatible_chat

        return await openai_compatible_chat(
            url=self._build_url(),
            headers={"api-key": self.api_key, "Content-Type": "application/json"},
            model=self.model,
            provider="azure_openai",
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            on_text_delta=on_text_delta,
        )


class MockLLMClient(LLMClient):
    """Mock client for testing (no API calls)."""

    supports_tools = True

    def __init__(self, model: str = "mock-model", script: list[ChatResult] | None = None):
        self.model = model
        self.script = list(script or [])
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        context: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        return LLMResponse(
            content=f"[Mock response to: {prompt[:50]}...]",
            model=self.model,
            input_tokens=len(prompt) // 4,
            output_tokens=50,
            provider="mock",
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        system: str,
        tools: list[ToolDef],
        max_tokens: int,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        if self.script:
            result = self.script[min(self.calls, len(self.script) - 1)]
            self.calls += 1
        else:
            result = ChatResult(
                text="[mock]",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=1,
                output_tokens=1,
                model=self.model,
                provider="mock",
            )
        if on_text_delta and result.text:
            await on_text_delta(result.text)
        return result


def _decrypt_api_key(stored: str | None) -> str | None:
    if not stored:
        return None
    try:
        from cryo.security.crypto import decrypt_secret
    except Exception:
        return None
    try:
        return decrypt_secret(stored)
    except Exception:
        return None


def get_llm_client(session: object | None = None) -> LLMClient | None:
    """Return the active LLM client, or ``None`` when no provider is configured.

    Prefers the default ``AIProvider`` row (a real, network-backed LLM); if none
    is enabled we deliberately return ``None`` rather than falling back to
    ``MockLLMClient`` so callers can detect "no LLM available" and choose a
    deterministic fallback instead of silently mocking.
    """
    from sqlalchemy import select

    from cryo.db.models import AIProvider

    if session is None:
        return None

    provider = None
    try:
        provider = session.execute(  # type: ignore[attr-defined]
            select(AIProvider).where(
                AIProvider.is_default.is_(True),
                AIProvider.enabled.is_(True),
            )
        ).scalar_one_or_none()
    except Exception:
        provider = None

    if provider is None:
        try:
            provider = session.execute(  # type: ignore[attr-defined]
                select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1)
            ).scalar_one_or_none()
        except Exception:
            provider = None

    if provider is None:
        return None

    api_key = _decrypt_api_key(getattr(provider, "api_key_encrypted", None)) or ""
    provider_type = getattr(provider, "provider_type", "mock")
    model = getattr(provider, "model", None)
    config = getattr(provider, "config", None) or {}
    if provider_type == "mock":
        return None
    try:
        return create_llm_client(provider_type, api_key, model, config=config)
    except Exception:
        return None


def create_llm_client(
    provider_type: str,
    api_key: str | None,
    model: str | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> LLMClient:
    """Factory function to create an LLM client.

    Args:
        provider_type: One of 'anthropic', 'openai', 'azure_openai', 'mock'
        api_key: API key for the provider
        model: Model/deployment name
        config: Additional config (e.g., api_base, api_version for Azure)
    """
    config = config or {}

    allow_custom_model = bool(config.get("allow_custom_model", False))
    model = _resolve_model(provider_type, model, allow_custom=allow_custom_model)

    if provider_type == "anthropic":
        if not api_key:
            raise ValueError("API key required for Anthropic")
        return AnthropicClient(api_key, model)
    elif provider_type == "openai":
        if not api_key:
            raise ValueError("API key required for OpenAI")
        settings = get_settings()
        api_base = config.get("api_base") or settings.llm_openai_base_url
        return OpenAIClient(api_key, model, api_base=api_base)
    elif provider_type == "azure_openai":
        if not api_key:
            raise ValueError("API key required for Azure OpenAI")
        return AzureOpenAIClient(
            api_key,
            model,
            api_base=config.get("api_base"),
            api_version=config.get("api_version", "2024-03-01-preview"),
        )
    elif provider_type == "mock":
        return MockLLMClient(model or "mock-model")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


# Supported providers for UI
SUPPORTED_PROVIDERS = [
    {
        "type": "anthropic",
        "name": "Anthropic",
        "models": [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        "default_model": "claude-sonnet-4-20250514",
        "config_fields": [],
    },
    {
        "type": "openai",
        "name": "OpenAI",
        "models": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ],
        "default_model": "gpt-4o",
        "config_fields": [
            {
                "name": "api_base",
                "label": "OpenAI-compatible API Base",
                "placeholder": "https://api.openai.com/v1",
                "required": False,
            },
            {
                "name": "allow_custom_model",
                "label": "Allow custom model/deployment name",
                "placeholder": "false",
                "required": False,
                "default": False,
            },
        ],
    },
    {
        "type": "azure_openai",
        "name": "Azure OpenAI",
        "models": [
            "gpt4o",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-35-turbo",
            "gpt-5.4-nano",
        ],
        "default_model": "gpt4o",
        "config_fields": [
            {
                "name": "api_base",
                "label": "API Base URL",
                "placeholder": "https://your-resource.openai.azure.com",
                "required": True,
            },
            {
                "name": "api_version",
                "label": "API Version",
                "placeholder": "2024-03-01-preview",
                "required": False,
                "default": "2024-03-01-preview",
            },
        ],
    },
]
