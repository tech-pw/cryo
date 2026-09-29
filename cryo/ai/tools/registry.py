"""Typed tool registry. Visibility is enforced here, not in the prompt."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.api.security import Principal, Role, role_allows
from cryo.config import Settings

ToolHandler = Callable[["ToolContext", BaseModel], EvidenceEnvelope]


@dataclass
class ToolContext:
    session: Session
    principal: Principal
    settings: Settings
    now: datetime
    timezone: str
    conversation_id: uuid.UUID | None = None
    send_row_values: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    family: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    tier: int
    min_role: Role
    handler: ToolHandler
    capability: str | None = None
    timeout_s: float = 10.0
    max_rows: int = 200


@dataclass
class ToolRegistry:
    specs: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def visible(
        self,
        principal: Principal,
        *,
        capabilities: set[str] | None = None,
        max_tier: int | None = None,
        enabled_tiers: set[int] | None = None,
        writes_enabled: bool = True,
    ) -> list[ToolSpec]:
        available = capabilities if capabilities is not None else set()
        shown: list[ToolSpec] = []
        for spec in self.specs.values():
            if not _role_ok(principal, spec.min_role):
                continue
            if spec.capability and spec.capability not in available and capabilities is not None:
                continue
            if max_tier is not None and spec.tier > max_tier:
                continue
            if enabled_tiers is not None and spec.tier not in enabled_tiers:
                continue
            if spec.tier >= 1 and not writes_enabled:
                continue
            if spec.tier >= 3:
                continue
            shown.append(spec)
        return shown

    def call(self, name: str, ctx: ToolContext, arguments: dict[str, Any]) -> EvidenceEnvelope:
        spec = self.specs.get(name)
        if spec is None or spec.tier >= 3:
            return EvidenceEnvelope(
                tool_name=name,
                classification="unknown",
                error_category="unknown_tool",
                warnings=[f"Tool {name} is not registered."],
                result={"error": "unknown_tool"},
            )
        if not _role_ok(ctx.principal, spec.min_role):
            return EvidenceEnvelope(
                tool_name=name,
                classification="unknown",
                error_category="forbidden",
                warnings=["Role does not allow this tool."],
                result={"error": "forbidden"},
            )
        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            return EvidenceEnvelope(
                tool_name=name,
                classification="unknown",
                error_category="invalid_arguments",
                warnings=[exc.errors()[0]["msg"] if exc.errors() else "invalid arguments"],
                result={"error": "invalid_arguments"},
            )
        try:
            envelope = spec.handler(ctx, parsed)
        except Exception as exc:  # noqa: BLE001 - tool failures stay data
            return EvidenceEnvelope(
                tool_name=name,
                classification="unknown",
                error_category="tool_error",
                warnings=[type(exc).__name__],
                result={"error": "tool_error"},
            )
        _trim_result(envelope, spec.max_rows)
        return envelope


_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    from cryo.ai.assistant.tool_scope import install_tenant_guard

    install_tenant_guard()
    return _REGISTRY


def _role_ok(principal: Principal, required: Role) -> bool:
    return any(role_allows(role, required) for role in principal.roles)


def _trim_result(envelope: EvidenceEnvelope, max_rows: int) -> None:
    result = dict(envelope.result)
    trimmed = False
    for key, value in list(result.items()):
        if isinstance(value, list) and len(value) > max_rows:
            result[key] = value[:max_rows]
            trimmed = True
    if trimmed or envelope.row_count > max_rows:
        envelope.truncated = True
        envelope.warnings.append(f"truncated to {max_rows} rows")
        envelope.result = result
