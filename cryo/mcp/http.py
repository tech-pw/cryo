"""Minimal streamable-HTTP MCP adapter. No official MCP SDK."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.ai.actions.catalog import CATALOG, is_tier3
from cryo.api.security import Principal, Role
from cryo.config import Settings, get_settings
from cryo.db.models import AssistantApiKey

MCP_PROTOCOL_VERSION = "2026-07-28"
router = APIRouter(tags=["mcp"])


def mount_mcp(app: FastAPI, settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    if not resolved.assistant_mcp_enabled:
        return
    app.state.cryo_mcp_settings = resolved
    app.include_router(router)


def _read_tool_definitions() -> list[dict[str, Any]]:
    from cryo.ai.tools.bootstrap import register_builtin_tools
    from cryo.ai.tools.registry import get_registry

    register_builtin_tools()
    tools: list[dict[str, Any]] = []
    for spec in get_registry().specs.values():
        if spec.tier != 0:
            continue
        schema = spec.input_model.model_json_schema()
        schema["type"] = "object"
        schema.setdefault("properties", {})
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": schema,
            }
        )
    return tools


def mcp_tool_definitions() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "describe_catalog",
            "description": "List Ask Cryo action types that can be proposed. Does not run them.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        *_read_tool_definitions(),
    ]
    for spec in CATALOG.values():
        if is_tier3(spec.action_type) or _forbidden_name(spec.action_type):
            continue
        tools.append(
            {
                "name": spec.action_type,
                "description": (
                    f"Propose {spec.action_type}. Returns an approval URL. "
                    "confirm=true does not run the action."
                ),
                "inputSchema": _input_schema(spec.action_type),
            }
        )
    return tools


@router.post("/mcp")
async def mcp_http(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "cryo_mcp_settings", None) or get_settings()
    if not settings.assistant_mcp_enabled:
        raise HTTPException(status_code=404, detail="not found")
    version = request.headers.get("mcp-protocol-version")
    if version and version != MCP_PROTOCOL_VERSION:
        raise HTTPException(status_code=400, detail="unsupported MCP protocol version")
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JSONResponse(_rpc_error(None, -32700, "parse error"))
    if not isinstance(payload, dict):
        return JSONResponse(_rpc_error(None, -32600, "invalid request"))
    session = _open_session()
    try:
        principal, scopes = _principal_from_bearer(session, request.headers.get("authorization"))
        body = _handle(session, principal, scopes, payload)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return JSONResponse(body)


def _handle(
    session: Session,
    principal: Principal,
    scopes: set[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    method = str(payload.get("method") or "")
    request_id = payload.get("id")
    raw_params = payload.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    if method in {"initialize", "ping"}:
        result = (
            {"ok": True}
            if method == "ping"
            else {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cryo", "version": "ask-cryo"},
            }
        )
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": mcp_tool_definitions()}}
    if method == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _call_tool(session, principal, scopes, params),
        }
    return _rpc_error(request_id, -32601, "method not found")


def _call_read_tool(
    session: Session,
    principal: Principal,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    from cryo.ai.tools.registry import ToolContext, get_registry

    envelope = get_registry().call(
        name,
        ToolContext(
            session=session,
            principal=principal,
            settings=get_settings(),
            now=datetime.now(UTC),
            timezone="UTC",
        ),
        arguments,
    )
    return _tool_text(envelope.model_dump_json())


def _call_tool(
    session: Session,
    principal: Principal,
    scopes: set[str],
    params: dict[str, Any],
) -> dict[str, Any]:
    name = str(params.get("name") or "")
    raw_arguments = params.get("arguments")
    arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
    known = {tool["name"] for tool in mcp_tool_definitions()}
    if _forbidden_name(name) or is_tier3(name) or name not in known:
        return _tool_error(f"unknown tool {name}")
    if name == "describe_catalog":
        return _tool_text(json.dumps([tool["name"] for tool in mcp_tool_definitions()]))
    read_names = {tool["name"] for tool in _read_tool_definitions()}
    if name in read_names:
        if "read" not in scopes:
            return _tool_error("read scope required")
        return _call_read_tool(session, principal, name, arguments)
    if "maintenance:propose" not in scopes:
        return _tool_error("maintenance:propose scope required")
    if _confirm_set(arguments):
        return _tool_error("confirm is not authorization")
    from cryo.ai.actions.gateway import ActionGateway

    proposal = ActionGateway(session, origin="mcp").prepare(principal, name, arguments, None, None)
    body = {
        "proposal_id": str(proposal.id),
        "approval_url": f"/assistant/approve/{proposal.id}",
        "status": proposal.status,
    }
    return _tool_text(json.dumps(body))


def _principal_from_bearer(session: Session, header: str | None) -> tuple[Principal, set[str]]:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.split(" ", 1)[1].strip()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    key = session.scalar(select(AssistantApiKey).where(AssistantApiKey.key_hash == digest))
    if key is None or key.revoked_at is not None:
        raise HTTPException(status_code=401, detail="invalid api key")
    scopes = {str(item) for item in (key.scopes or [])}
    if "read" not in scopes:
        raise HTTPException(status_code=403, detail="read scope required")
    key.last_used_at = datetime.now(UTC)
    principal = Principal(
        subject=key.created_by,
        roles=frozenset({Role.VIEWER}),
        tenant_id=key.tenant_id,
    )
    return principal, scopes


def _open_session() -> Session:
    from cryo.db.session import SessionLocal

    return SessionLocal()


def _input_schema(action_type: str) -> dict[str, Any]:
    properties: dict[str, Any] = {"note": {"type": "string"}, "reason": {"type": "string"}}
    if action_type in {"protect_snapshot", "unprotect_snapshot"}:
        properties["snapshot_id"] = {"type": "string"}
        properties["table_id"] = {"type": "string"}
    return {"type": "object", "properties": properties, "additionalProperties": True}


def _forbidden_name(name: str) -> bool:
    normalized = name.strip().lower()
    return "purge" in normalized or "delete" in normalized or normalized.startswith("drop")


def _confirm_set(arguments: dict[str, Any]) -> bool:
    value = arguments.get("confirm")
    return value is True or value in {"true", "True", "1", 1}


def _tool_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _rpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
