"""Server-side cursor pagination utilities.

Used by the inventory endpoints (``/tables``, ``/runs``, ``/scans``) when
the result set exceeds the request budget for a single page. The cursor
is an opaque, URL-safe token that fully encodes the server-side state
needed to resume — clients only see the cursor string; the encoding
details are this module's contract.

The encoding is *not* a security primitive: do NOT pass untrusted
cursors from end users as if they were authorization. The cursor embeds
the current SQL filter/sort so the server can re-execute the same
query deterministically; if you want stricter guarantees, pair this with
an authenticated principal check at the route boundary.

Format (base64url-encoded JSON after envelope validation):

    {"v": 1, "k": "<stable serialization of the last row's key>",
     "o": <offset within that key group>, "f": "<opaque filter repr>",
     "s": "<opaque sort repr>"}

The "k"/"o" pair is server-defined; the only requirement is that
"encoding a row's key + position in the key-group" + "sort+filter
strings" round-trips to "fetch the row that comes immediately after".

Refs docs/strategy-next-moves-2026-07.md Move 23.
"""

from __future__ import annotations

import base64
import json
from typing import Any


class CursorError(ValueError):
    """Raised when a cursor cannot be decoded or is structurally invalid."""


class PageCursor:
    """Opaque cursor token returned by paginated endpoints.

    Construct via ``PageCursor.encode(...)`` from server-side code; parse
    via ``PageCursor.decode(token)`` from inbound ``cursor=`` query values.
    Callers should not read the attributes directly — they are an
    implementation detail of the encoding. Use the property accessors.
    """

    __slots__ = ("version", "key", "offset_within_key", "filter_repr", "sort_repr")

    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        key: str,
        offset_within_key: int,
        filter_repr: str | None,
        sort_repr: str | None,
    ) -> None:
        self.version = self.SCHEMA_VERSION
        self.key = key
        self.offset_within_key = offset_within_key
        self.filter_repr = filter_repr
        self.sort_repr = sort_repr

    def encode(self) -> str:
        payload = {
            "v": self.version,
            "k": self.key,
            "o": self.offset_within_key,
            "f": self.filter_repr,
            "s": self.sort_repr,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, token: str) -> PageCursor:
        if not token:
            raise CursorError("empty cursor token")
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CursorError(f"cursor is not valid base64-json: {exc}") from exc

        if not isinstance(payload, dict) or payload.get("v") != cls.SCHEMA_VERSION:
            raise CursorError(
                f"cursor schema version mismatch: got {payload.get('v')!r}, "
                f"expected {cls.SCHEMA_VERSION}"
            )

        key = payload.get("k")
        offset = payload.get("o")
        filter_repr = payload.get("f")
        sort_repr = payload.get("s")
        if not isinstance(key, str) or not isinstance(offset, int):
            raise CursorError("cursor missing required string 'k' / int 'o' fields")
        if filter_repr is not None and not isinstance(filter_repr, str):
            raise CursorError("cursor 'f' must be a string or null")
        if sort_repr is not None and not isinstance(sort_repr, str):
            raise CursorError("cursor 's' must be a string or null")

        return cls(
            key=key,
            offset_within_key=offset,
            filter_repr=filter_repr,
            sort_repr=sort_repr,
        )

    def matches_request(self, *, filter_repr: str | None, sort_repr: str | None) -> bool:
        """Verify the cursor was issued for the same filter/sort as the current request.

        Cursors are sticky to a request shape — a request whose filter or
        sort differs MUST start from offset=0 rather than reuse a stale
        cursor. Callers must check this before applying the cursor.
        """
        return self.filter_repr == filter_repr and self.sort_repr == sort_repr

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "key": self.key,
            "offset_within_key": self.offset_within_key,
            "filter_repr": self.filter_repr,
            "sort_repr": self.sort_repr,
        }


def make_filter_repr(predicate: Any) -> str | None:
    """Stable string representation of a request's filter predicate.

    SQLAlchemy expressions don't have a deterministic repr, so callers
    pass an already-stringified form (the JSON ``filter=...`` query
    value is the natural canonical form). Returned as ``None`` if no
    filter was applied — both ``None`` and ``""`` round-trip as the same
    "no filter" cursor state.
    """
    if predicate is None:
        return None
    if isinstance(predicate, str):
        return predicate or None
    return str(predicate)


def make_sort_repr(sort: Any) -> str | None:
    """Stable string representation of a request's sort spec."""
    if sort is None:
        return None
    if isinstance(sort, str):
        return sort or None
    return str(sort)
