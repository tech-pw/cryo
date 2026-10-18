"""Evidence envelope shared by every Ask Cryo tool."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Classification = Literal["observed", "modeled", "inferred", "unknown"]

_SECRET_KEYS = {
    "api_key",
    "password",
    "secret",
    "token",
    "webhook_url",
    "authorization",
    "api_key_encrypted",
    "secret_ref",
    "signing_secret",
    "bot_token",
}
_TOKEN_URL = re.compile(
    r"(https?://[^\s]*?(?:token|sig|signature|key|password)=)[^&\s]+",
    re.IGNORECASE,
)
def _mask_sql_literals(value: str) -> str:
    """Replace quoted SQL literals without a backtracking regex."""
    parts: list[str] = []
    index = 0
    size = len(value)
    while index < size:
        quote = value[index]
        if quote not in {"'", '"'}:
            parts.append(quote)
            index += 1
            continue
        cursor = index + 1
        closed = False
        while cursor < size:
            if value[cursor] == "\\":
                cursor += 2
                continue
            if value[cursor] == quote:
                parts.append("'[literal]'")
                index = cursor + 1
                closed = True
                break
            cursor += 1
        if not closed:
            parts.append(value[index:])
            break
    return "".join(parts)


def redact(value: Any) -> Any:
    """Strip secrets, token-bearing URLs and SQL literals before a model sees a payload."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        masked = _TOKEN_URL.sub(r"\1[redacted]", value)
        return _mask_sql_literals(masked)
    return value


class EvidenceEnvelope(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    schema_version: int = 1
    authorized_scope: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    result: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    measurement_method: str = "stored_scan"
    coverage: dict[str, Any] = Field(default_factory=dict)
    classification: Classification = "observed"
    source_links: list[dict[str, str]] = Field(default_factory=list)
    source_record_ids: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)
    error_category: str | None = None
    truncated: bool = False
    row_count: int = 0

    def for_model(self, *, send_row_values: bool = False) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if not send_row_values:
            result = dict(payload.get("result") or {})
            result.pop("row_values", None)
            result.pop("sample_rows", None)
            payload["result"] = result
        redacted = redact(payload)
        return redacted if isinstance(redacted, dict) else {}

    def bounded_result(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        payload = redact(self.result)
        encoded = str(payload)
        if len(encoded) <= max_bytes:
            return payload if isinstance(payload, dict) else {"value": payload}
        return {
            "truncated": True,
            "preview": encoded[: max_bytes // 2],
            "warning": "result exceeded the evidence size cap",
        }
