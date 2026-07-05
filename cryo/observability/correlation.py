"""Request and job correlation identifiers for log/trace stitching."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("cryo_request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("cryo_job_id", default=None)


def new_request_id() -> str:
    return str(uuid.uuid4())


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_job_id(value: str | None) -> None:
    _job_id.set(value)


def get_job_id() -> str | None:
    return _job_id.get()


def correlation_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    request_id = get_request_id()
    job_id = get_job_id()
    if request_id:
        fields["request_id"] = request_id
    if job_id:
        fields["job_id"] = job_id
    return fields
