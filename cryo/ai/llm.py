"""LLM client abstraction for AI providers (Anthropic, OpenAI, Azure OpenAI)."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# Default models
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "azure_openai": "gpt4o",
}

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


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

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


class AnthropicClient(LLMClient):
    """Anthropic Claude client."""

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODELS["anthropic"]

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


class OpenAIClient(LLMClient):
    """OpenAI GPT client."""

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODELS["openai"]

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

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENAI_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data["model"],
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            provider="openai",
        )


class AzureOpenAIClient(LLMClient):
    """Azure OpenAI client.

    Azure OpenAI uses a different URL structure and authentication:
    - Base URL: https://<resource>.azure-api.net or custom APIM endpoint
    - API Key via api-key header (not Bearer token)
    - Deployment name in URL path instead of model in body
    """

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        api_base: str | None = None,
        api_version: str = "2024-03-01-preview",
    ):
        self.api_key = api_key
        self.model = model or DEFAULT_MODELS["azure_openai"]
        self.api_base = api_base or "https://apim-aiguru-gpt4.azure-api.net"
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

        url = self._build_url()

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers={
                    "api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", self.model),
            input_tokens=data["usage"]["prompt_tokens"],
            output_tokens=data["usage"]["completion_tokens"],
            provider="azure_openai",
        )


class MockLLMClient(LLMClient):
    """Mock client for testing (no API calls)."""

    def __init__(self, model: str = "mock-model"):
        self.model = model

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

    if provider_type == "anthropic":
        if not api_key:
            raise ValueError("API key required for Anthropic")
        return AnthropicClient(api_key, model)
    elif provider_type == "openai":
        if not api_key:
            raise ValueError("API key required for OpenAI")
        return OpenAIClient(api_key, model)
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
        "config_fields": [],
    },
    {
        "type": "azure_openai",
        "name": "Azure OpenAI",
        "models": [
            "gpt4o",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-35-turbo",
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
