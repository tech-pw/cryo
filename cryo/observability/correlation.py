"""Bounded correlation identifiers for log, metric, and trace stitching."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

CORRELATION_SUMMARY_KEY = "correlation_id"
MAX_CORRELATION_ID_LENGTH = 128

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")
_request_id: ContextVar[str | None] = ContextVar("cryo_request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("cryo_correlation_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("cryo_run_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("cryo_job_id", default=None)
_intent_id: ContextVar[str | None] = ContextVar("cryo_intent_id", default=None)
_attempt_id: ContextVar[str | None] = ContextVar("cryo_attempt_id", default=None)
_effect_id: ContextVar[str | None] = ContextVar("cryo_effect_id", default=None)
_table_id: ContextVar[str | None] = ContextVar("cryo_table_id", default=None)
_destination_id: ContextVar[str | None] = ContextVar("cryo_destination_id", default=None)
_engine_ref: ContextVar[str | None] = ContextVar("cryo_engine_ref", default=None)
_snapshot_id: ContextVar[str | None] = ContextVar("cryo_snapshot_id", default=None)

_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": _request_id,
    "correlation_id": _correlation_id,
    "run_id": _run_id,
    "job_id": _job_id,
    "intent_id": _intent_id,
    "attempt_id": _attempt_id,
    "effect_id": _effect_id,
    "table_id": _table_id,
    "destination_id": _destination_id,
    "engine_ref": _engine_ref,
    "snapshot_id": _snapshot_id,
}


def new_request_id() -> str:
    return str(uuid.uuid4())


def normalize_correlation_id(value: object, *, generate: bool = True) -> str | None:
    """Return a log-safe bounded identifier, replacing invalid external input."""
    text = str(value).strip() if value is not None else ""
    if text and len(text) <= MAX_CORRELATION_ID_LENGTH and _SAFE_ID.fullmatch(text) is not None:
        return text
    return new_request_id() if generate else None


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_job_id(value: str | None) -> None:
    _job_id.set(value)


def get_job_id() -> str | None:
    return _job_id.get()


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_context(*, replace: bool = False, **fields: object | None) -> None:
    """Set fields for long-running loops that cannot use a lexical context manager."""
    unknown = set(fields) - _CONTEXT_VARS.keys()
    if unknown:
        raise ValueError(f"unsupported correlation fields: {sorted(unknown)}")
    if replace:
        clear_context()
    for name, value in fields.items():
        _CONTEXT_VARS[name].set(None if value is None else str(value))


@contextmanager
def correlation_scope(*, clear: bool = False, **fields: object | None) -> Iterator[None]:
    """Bind one unit of work and restore the caller's context on every exit path."""
    unknown = set(fields) - _CONTEXT_VARS.keys()
    if unknown:
        raise ValueError(f"unsupported correlation fields: {sorted(unknown)}")
    values: dict[str, object | None] = {name: None for name in _CONTEXT_VARS} if clear else {}
    values.update(fields)
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    for name, value in values.items():
        context_var = _CONTEXT_VARS[name]
        tokens.append((context_var, context_var.set(None if value is None else str(value))))
    try:
        yield
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)


def correlation_fields() -> dict[str, str]:
    return {
        name: value
        for name, context_var in _CONTEXT_VARS.items()
        if (value := context_var.get()) is not None
    }


def clear_context() -> None:
    for context_var in _CONTEXT_VARS.values():
        context_var.set(None)


def correlation_summary(
    summary: Mapping[str, Any] | None = None,
    *,
    correlation_id: object | None = None,
) -> dict[str, Any]:
    """Attach a durable root correlation ID to a maintenance run."""
    result = dict(summary or {})
    candidate = correlation_id or get_correlation_id() or result.get(CORRELATION_SUMMARY_KEY)
    result[CORRELATION_SUMMARY_KEY] = normalize_correlation_id(candidate)
    return result


def correlation_id_from_summary(summary: object) -> str | None:
    if not isinstance(summary, Mapping):
        return None
    return normalize_correlation_id(summary.get(CORRELATION_SUMMARY_KEY), generate=False)
