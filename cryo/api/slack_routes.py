"""Slack interactive requests. Signature is checked. Maintenance is never executed."""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["integrations"])


@router.post("/api/v1/integrations/slack/interactions")
async def slack_interactions(request: Request) -> dict[str, str]:
    from cryo.config import get_settings
    from cryo.mcp.slack_verify import verify_slack_signature

    body = await request.body()
    secret = get_settings().slack_signing_secret or ""
    if not verify_slack_signature(
        body,
        request.headers.get("x-slack-request-timestamp", ""),
        request.headers.get("x-slack-signature", ""),
        secret,
    ):
        raise HTTPException(status_code=401, detail="invalid slack signature")
    return slack_interaction_response(_payload(body))


def slack_interaction_response(payload: dict[str, Any]) -> dict[str, str]:
    run_path = _diagnose_run_path(payload)
    if run_path is not None:
        return {
            "response_type": "ephemeral",
            "text": f"Open this run in Cryo: {run_path}",
        }
    return {
        "response_type": "ephemeral",
        "text": "Cryo does not run maintenance from Slack. Approve the action in Cryo.",
    }


def _payload(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8")
    if text.startswith("{"):
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    form = parse_qs(text, keep_blank_values=True)
    raw = form.get("payload", [""])[0]
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _diagnose_run_path(payload: dict[str, Any]) -> str | None:
    candidates: list[object] = []
    actions = payload.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("action_id") or "")
            if action_id == "diagnose" or action_id.startswith("diagnose_"):
                candidates.append(action.get("value") or action.get("run_id"))
    if payload.get("type") == "diagnose" or payload.get("callback_id") == "diagnose":
        candidates.append(payload.get("run_id"))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            run_id = uuid.UUID(candidate)
        except ValueError:
            continue
        return f"/runs/{run_id}"
    return None
