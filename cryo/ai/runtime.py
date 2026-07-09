"""Shared LLM runtime: provider selection, budgets, and durable observations."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.llm import LLMResponse, create_llm_client
from cryo.config import get_settings
from cryo.db.models import AIProvider, LLMObservation
from cryo.observability.metrics import llm_budget_denials_total, llm_tokens_total
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMInvokeContext:
    feature: str
    prompt_name: str | None = None
    skillset_id: uuid.UUID | None = None
    skillset_name: str | None = None
    skillset_version: str | None = None
    run_id: uuid.UUID | None = None
    table_id: uuid.UUID | None = None
    outcome_id: uuid.UUID | None = None
    extra: dict[str, Any] | None = None


def get_active_provider(session: Session) -> AIProvider | None:
    provider = session.scalar(
        select(AIProvider).where(
            AIProvider.is_default.is_(True),
            AIProvider.enabled.is_(True),
        )
    )
    if provider is not None:
        return provider
    return session.scalar(select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1))


def _prompt_hash(prompt: str, *, system: str | None, context: dict[str, Any] | None) -> str:
    payload = {
        "prompt": prompt,
        "system": system or "",
        "context": context or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provider_budget(provider: AIProvider) -> dict[str, Any]:
    settings = get_settings()
    config = dict(provider.config or {})
    budget = dict(config.get("budget") or {})
    return {
        "per_call_max_tokens": int(
            budget.get("per_call_max_tokens", settings.llm_per_call_max_tokens_default)
        ),
        "daily_token_cap": int(
            budget.get("daily_token_cap", settings.llm_daily_token_cap_default)
        ),
        "daily_cost_cap_usd": float(
            budget.get("daily_cost_cap_usd", settings.llm_daily_cost_cap_usd_default)
        ),
        "mode": str(budget.get("over_budget_mode", settings.llm_over_budget_mode)).lower(),
    }


def _todays_usage(session: Session, provider_id: uuid.UUID) -> tuple[int, float]:
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tokens = session.scalar(
        select(func.coalesce(func.sum(LLMObservation.total_tokens), 0)).where(
            LLMObservation.provider_id == provider_id,
            LLMObservation.recorded_at >= day_start,
            LLMObservation.status == "success",
        )
    )
    cost = session.scalar(
        select(func.coalesce(func.sum(LLMObservation.estimated_cost_usd), 0.0)).where(
            LLMObservation.provider_id == provider_id,
            LLMObservation.recorded_at >= day_start,
            LLMObservation.status == "success",
        )
    )
    return int(tokens or 0), float(cost or 0.0)


def _estimate_cost_usd(
    provider: AIProvider,
    *,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    config = dict(provider.config or {})
    pricing = dict(config.get("pricing") or {})
    input_per_1k = pricing.get("input_per_1k_usd")
    output_per_1k = pricing.get("output_per_1k_usd")
    if input_per_1k is None or output_per_1k is None:
        return None
    cost = float(input_per_1k) * input_tokens + float(output_per_1k) * output_tokens
    return round(cost / 1000.0, 8)


def _write_observation(
    session: Session,
    *,
    provider: AIProvider | None,
    model_id: str,
    prompt_hash: str,
    context: LLMInvokeContext,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int | None = None,
    estimated_cost_usd: float | None = None,
    budget_denied: bool = False,
    error: str | None = None,
) -> LLMObservation:
    row = LLMObservation(
        provider_id=provider.id if provider is not None else None,
        provider_type=provider.provider_type if provider is not None else "none",
        model_id=model_id,
        feature=context.feature,
        prompt_name=context.prompt_name,
        prompt_hash=prompt_hash,
        skillset_id=context.skillset_id,
        skillset_name=context.skillset_name,
        skillset_version=context.skillset_version,
        run_id=context.run_id,
        table_id=context.table_id,
        outcome_id=context.outcome_id,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=max(0, input_tokens + output_tokens),
        estimated_cost_usd=estimated_cost_usd,
        status=status,
        budget_denied=budget_denied,
        error=error,
        extra=dict(context.extra or {}),
    )
    session.add(row)
    session.flush()
    return row


async def complete_with_budget_and_observation(
    session: Session,
    prompt: str,
    *,
    invoke: LLMInvokeContext,
    system: str | None = None,
    context: dict[str, Any] | None = None,
    max_tokens: int = 1024,
) -> tuple[LLMResponse | None, LLMObservation]:
    """Execute an LLM completion with provider budgets and durable logging.

    Returns ``(response, observation_row)`` where ``response`` is ``None`` when no provider is
    configured, when budget guardrails deny the request, or when the provider call fails.
    """

    provider = get_active_provider(session)
    hashed = _prompt_hash(prompt, system=system, context=context)
    if provider is None:
        row = _write_observation(
            session,
            provider=None,
            model_id="none",
            prompt_hash=hashed,
            context=invoke,
            status="no_provider",
        )
        session.commit()
        return None, row

    budget = _provider_budget(provider)
    used_tokens, used_cost = _todays_usage(session, provider.id)
    per_call_max = max(1, int(budget["per_call_max_tokens"]))
    if max_tokens > per_call_max:
        reason = (
            f"{budget['mode']}: requested_tokens({max_tokens}) exceeds "
            f"per_call_max_tokens({per_call_max})"
        )
        llm_budget_denials_total.labels(provider=provider.provider_type, reason="per_call").inc()
        row = _write_observation(
            session,
            provider=provider,
            model_id=provider.model,
            prompt_hash=hashed,
            context=invoke,
            status=f"budget_denied_{budget['mode']}",
            budget_denied=True,
            error=reason,
        )
        session.commit()
        return None, row

    daily_token_cap = max(1, int(budget["daily_token_cap"]))
    if used_tokens + max_tokens > daily_token_cap:
        reason = f"{budget['mode']}: daily token cap exceeded: {used_tokens}/{daily_token_cap}"
        llm_budget_denials_total.labels(
            provider=provider.provider_type, reason="daily_tokens"
        ).inc()
        row = _write_observation(
            session,
            provider=provider,
            model_id=provider.model,
            prompt_hash=hashed,
            context=invoke,
            status=f"budget_denied_{budget['mode']}",
            budget_denied=True,
            error=reason,
        )
        session.commit()
        return None, row

    api_key = ""
    if provider.api_key_encrypted:
        try:
            api_key = decrypt_secret(provider.api_key_encrypted) or ""
        except Exception:
            api_key = ""
    if not api_key:
        row = _write_observation(
            session,
            provider=provider,
            model_id=provider.model,
            prompt_hash=hashed,
            context=invoke,
            status="provider_unconfigured",
            error="provider has no decryptable API key",
        )
        session.commit()
        return None, row

    start = perf_counter()
    try:
        client = create_llm_client(
            provider.provider_type,
            api_key,
            provider.model,
            config=dict(provider.config or {}),
        )
        response = await client.complete(
            prompt,
            system=system,
            context=context,
            max_tokens=max_tokens,
        )
        elapsed_ms = int((perf_counter() - start) * 1000)
        estimated_cost = _estimate_cost_usd(
            provider,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        daily_cost_cap = float(budget["daily_cost_cap_usd"])
        projected_cost = used_cost + (estimated_cost or 0.0)
        if estimated_cost is not None and projected_cost > daily_cost_cap:
            llm_budget_denials_total.labels(
                provider=provider.provider_type, reason="daily_cost"
            ).inc()
            row = _write_observation(
                session,
                provider=provider,
                model_id=response.model,
                prompt_hash=hashed,
                context=invoke,
                status=f"budget_denied_{budget['mode']}",
                budget_denied=True,
                error=(
                    f"{budget['mode']}: daily cost cap exceeded: "
                    f"${projected_cost:.4f}/${daily_cost_cap:.4f}"
                ),
            )
            session.commit()
            return None, row

        row = _write_observation(
            session,
            provider=provider,
            model_id=response.model,
            prompt_hash=hashed,
            context=invoke,
            status="success",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=elapsed_ms,
            estimated_cost_usd=estimated_cost,
        )
        llm_tokens_total.labels(provider=provider.provider_type, feature=invoke.feature).inc(
            response.input_tokens + response.output_tokens
        )
        session.commit()
        return response, row
    except Exception as exc:
        row = _write_observation(
            session,
            provider=provider,
            model_id=provider.model,
            prompt_hash=hashed,
            context=invoke,
            status="error",
            error=str(exc),
        )
        session.commit()
        logger.warning("LLM invocation failed for feature=%s: %s", invoke.feature, exc)
        return None, row
