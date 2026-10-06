from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from cryo.config import get_settings
from cryo.db.models import AssistantApiKey
from cryo.mcp.http import MCP_PROTOCOL_VERSION, mcp_tool_definitions, mount_mcp


def test_protocol_version_is_pinned() -> None:
    assert MCP_PROTOCOL_VERSION == "2026-07-28"


def test_tools_list_excludes_purge_and_delete() -> None:
    names = [tool["name"] for tool in mcp_tool_definitions()]
    assert names
    assert not any("purge" in name or "delete" in name or name.startswith("drop") for name in names)
    assert "run_maintenance" in names
    assert "describe_catalog" in names


def test_mcp_disabled_is_404() -> None:
    app = FastAPI()
    mount_mcp(app, get_settings().model_copy(update={"assistant_mcp_enabled": False}))
    response = TestClient(app).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 404


def test_mcp_tools_list_and_confirm_does_not_execute(
    session: Session,
    session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    raw = "cryo-test-key"
    session.add(
        AssistantApiKey(
            id=uuid.uuid4(),
            name="mcp",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes=["read", "maintenance:propose"],
            created_by="mcp-user",
        )
    )
    session.commit()
    monkeypatch.setattr("cryo.mcp.http._open_session", lambda: session_factory())
    app = FastAPI()
    mount_mcp(
        app,
        get_settings().model_copy(update={"assistant_mcp_enabled": True, "environment": "test"}),
    )
    client = TestClient(app)
    listed = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed.status_code == 200
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert "purge" not in names
    assert "delete" not in names
    called = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "scan_table",
                "arguments": {"table_id": str(uuid.uuid4()), "confirm": True},
            },
        },
    )
    body = called.json()["result"]
    assert body["isError"] is True
    assert "run_id" not in json.dumps(body)
    proposed = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "save_view", "arguments": {"name": "hot", "filters": {}}},
        },
    )
    text = proposed.json()["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["approval_url"].startswith("/assistant/approve/")
    assert "run_id" not in payload
