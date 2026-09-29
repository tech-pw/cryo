from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from cryo.config import Settings, get_settings
from cryo.db.models import AssistantApiKey
from cryo.mcp.http import MCP_PROTOCOL_VERSION, mount_mcp

_CALL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["content", "isError"],
    "properties": {
        "content": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "text"],
                "properties": {"type": {"type": "string"}, "text": {"type": "string"}},
            },
        },
        "isError": {"type": "boolean"},
    },
}


def _insert_key(
    session: Session,
    raw: str,
    scopes: list[str],
    *,
    revoked: bool = False,
) -> None:
    session.add(
        AssistantApiKey(
            id=uuid.uuid4(),
            name="conformance",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes=scopes,
            created_by="conformance",
            revoked_at=datetime.now(UTC) if revoked else None,
        )
    )
    session.commit()


def _mounted_app(enabled: bool = True) -> FastAPI:
    app = FastAPI()
    mount_mcp(
        app,
        get_settings().model_copy(update={"assistant_mcp_enabled": enabled, "environment": "test"}),
    )
    return app


def _client(
    monkeypatch: Any,
    session_factory: sessionmaker[Session],
    *,
    enabled: bool = True,
) -> TestClient:
    monkeypatch.setattr("cryo.mcp.http._open_session", lambda: session_factory())
    return TestClient(_mounted_app(enabled))


def _post(
    client: TestClient,
    method: str,
    *,
    request_id: int = 1,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    protocol: str | None = MCP_PROTOCOL_VERSION,
) -> Any:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if protocol is not None:
        headers["mcp-protocol-version"] = protocol
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post("/mcp", headers=headers, json=payload)


def _assert_object_schema(schema: object) -> None:
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)


def _assert_call_output(result: object) -> None:
    assert isinstance(result, dict)
    for key in _CALL_OUTPUT_SCHEMA["required"]:
        assert key in result
    assert isinstance(result["isError"], bool)
    content = result["content"]
    assert isinstance(content, list) and content
    block = content[0]
    assert isinstance(block, dict)
    assert block.get("type") == "text"
    assert isinstance(block.get("text"), str)


def test_protocol_version_constant_is_pinned() -> None:
    assert MCP_PROTOCOL_VERSION == "2026-07-28"
    assert Settings.model_fields["assistant_mcp_enabled"].default is False


def test_route_not_registered_when_flag_is_false() -> None:
    app = FastAPI()
    mount_mcp(app, get_settings().model_copy(update={"assistant_mcp_enabled": False}))
    assert "/mcp" not in app.openapi()["paths"]
    response = TestClient(app).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    assert response.status_code == 404


def test_route_registered_without_an_api_key(
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("cryo.mcp.http._open_session", lambda: session_factory())
    app = _mounted_app(True)
    assert "/mcp" in app.openapi()["paths"]
    response = TestClient(app).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_initialize(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    raw = "conformance-initialize"
    _insert_key(session, raw, ["read"])
    response = _post(
        _client(monkeypatch, session_factory),
        "initialize",
        token=raw,
        params={
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "conformance", "version": "0"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "error" not in body
    assert body["result"] == {
        "protocolVersion": "2026-07-28",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "cryo", "version": "ask-cryo"},
    }


def test_tools_list_input_and_output_schemas(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    raw = "conformance-list"
    _insert_key(session, raw, ["read"])
    client = _client(monkeypatch, session_factory)
    listed = _post(client, "tools/list", token=raw)
    assert listed.status_code == 200
    body = listed.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    tools = body["result"]["tools"]
    assert isinstance(tools, list) and tools
    by_name = {}
    for tool in tools:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str)
        _assert_object_schema(tool["inputSchema"])
        output_schema = tool.get("outputSchema", _CALL_OUTPUT_SCHEMA)
        assert isinstance(output_schema, dict)
        assert output_schema.get("type") in {"object", "array"}
        if "outputSchema" in tool:
            _assert_object_schema(tool["outputSchema"])
        by_name[tool["name"]] = tool
    catalog = by_name["describe_catalog"]["inputSchema"]
    assert catalog["properties"] == {}
    assert catalog["additionalProperties"] is False
    protect = by_name["protect_snapshot"]["inputSchema"]
    assert protect["properties"]["snapshot_id"]["type"] == "string"
    assert protect["properties"]["table_id"]["type"] == "string"
    called = _post(
        client,
        "tools/call",
        request_id=2,
        token=raw,
        params={"name": "describe_catalog", "arguments": {}},
    )
    result = called.json()["result"]
    _assert_call_output(result)
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == [tool["name"] for tool in tools]


def test_tools_call(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    raw = "conformance-call"
    _insert_key(session, raw, ["read", "maintenance:propose"])
    response = _post(
        _client(monkeypatch, session_factory),
        "tools/call",
        token=raw,
        params={"name": "save_view", "arguments": {"name": "hot", "filters": {}}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "error" not in body
    result = body["result"]
    _assert_call_output(result)
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["approval_url"].startswith("/assistant/approve/")
    assert payload["status"] == "awaiting_authorization"
    assert "run_id" not in payload


def test_structured_errors(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    raw = "conformance-errors"
    _insert_key(session, raw, ["read", "maintenance:propose"])
    client = _client(monkeypatch, session_factory)
    parse_error = client.post(
        "/mcp",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    assert parse_error.status_code == 200
    parsed = parse_error.json()
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] is None
    assert parsed["error"] == {"code": -32700, "message": "parse error"}
    assert "result" not in parsed
    invalid = client.post(
        "/mcp",
        content=b"[]",
        headers={"content-type": "application/json"},
    )
    assert invalid.json()["jsonrpc"] == "2.0"
    assert invalid.json()["error"] == {"code": -32600, "message": "invalid request"}
    assert "result" not in invalid.json()
    missing = _post(client, "resources/list", request_id=7, token=raw)
    assert missing.status_code == 200
    missing_body = missing.json()
    assert missing_body["id"] == 7
    assert missing_body["error"] == {"code": -32601, "message": "method not found"}
    assert "result" not in missing_body
    unknown = _post(
        client,
        "tools/call",
        request_id=8,
        token=raw,
        params={"name": "not_a_tool", "arguments": {}},
    )
    unknown_body = unknown.json()
    assert "error" not in unknown_body
    _assert_call_output(unknown_body["result"])
    assert unknown_body["result"]["isError"] is True
    assert unknown_body["result"]["content"][0]["text"] == "unknown tool not_a_tool"
    confirmed = _post(
        client,
        "tools/call",
        request_id=9,
        token=raw,
        params={"name": "save_view", "arguments": {"confirm": True}},
    )
    confirmed_result = confirmed.json()["result"]
    assert confirmed_result["isError"] is True
    assert confirmed_result["content"][0]["text"] == "confirm is not authorization"


def test_auth_failure_bad_or_revoked_key(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    revoked = "conformance-revoked-key"
    _insert_key(session, revoked, ["read"], revoked=True)
    client = _client(monkeypatch, session_factory)
    bad = _post(client, "initialize", token="not-a-real-key")
    assert bad.status_code == 401
    assert bad.json()["detail"] == "invalid api key"
    gone = _post(client, "initialize", token=revoked)
    assert gone.status_code == 401
    assert gone.json()["detail"] == "invalid api key"


def test_read_tools_are_listed_and_called(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch: Any,
) -> None:
    raw = "conformance-read"
    _insert_key(session, raw, ["read"])
    client = _client(monkeypatch, session_factory)
    listed = _post(client, "tools/list", token=raw)
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "get_lake_health" in names
    assert "list_snapshots" in names
    called = _post(
        client,
        "tools/call",
        request_id=2,
        token=raw,
        params={"name": "get_lake_health", "arguments": {}},
    )
    result = called.json()["result"]
    _assert_call_output(result)
    assert result["isError"] is False
    envelope = json.loads(result["content"][0]["text"])
    assert envelope["tool_name"] == "get_lake_health"
    foreign = _post(
        client,
        "tools/call",
        request_id=3,
        token=raw,
        params={
            "name": "list_snapshots",
            "arguments": {"table_id": "22222222-2222-2222-2222-222222222222"},
        },
    )
    foreign_body = json.loads(foreign.json()["result"]["content"][0]["text"])
    assert foreign_body.get("error_category") in {"forbidden", "not_found", "unknown"}
