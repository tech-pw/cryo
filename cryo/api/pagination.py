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

Two envelopes share this module:

* Version 1 (``PageCursor``) stores an offset. ``GET /tables`` no longer
  accepts it: an offset repeats or skips rows when the inventory changes
  between pages.
* Version 2 (``KeysetCursor``) stores the last row's sort values plus its
  table id. The next page is the rows strictly after that position, so an
  insert or delete ahead of the cursor does not shift the remainder.

Both bind the cursor to the filter and sort it was issued for.

Refs docs/strategy-next-moves-2026-07.md Move 23.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, or_
from sqlalchemy import false as sql_false
from sqlalchemy.sql.elements import ColumnElement


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


def keyset_predicate(
    keys: Sequence[tuple[ColumnElement[Any], bool, Any]],
) -> ColumnElement[bool]:
    """Rows strictly after ``keys`` in ``ORDER BY expr ASC/DESC NULLS LAST``.

    ``keys`` is ``(expression, descending, value_of_the_last_row)`` in sort
    order, and the last entry must be a unique non-null tie-break (the table
    id). A null value means the previous page ended in the ``NULLS LAST``
    group for that key, so only a later key can move forward.
    """
    clauses: list[ColumnElement[bool]] = []
    prefix: list[ColumnElement[bool]] = []
    for expression, descending, value in keys:
        after = _strictly_after(expression, descending, value)
        if after is not None:
            clauses.append(and_(*prefix, after) if prefix else after)
        prefix.append(_equal_key(expression, value))
    if not clauses:
        return sql_false()
    return cast(ColumnElement[bool], or_(*clauses))


def _equal_key(expression: ColumnElement[Any], value: Any) -> ColumnElement[bool]:
    if value is None:
        return expression.is_(None)
    return cast(ColumnElement[bool], expression == value)


def _strictly_after(
    expression: ColumnElement[Any], descending: bool, value: Any
) -> ColumnElement[bool] | None:
    if value is None:
        return None
    if descending:
        return cast(ColumnElement[bool], or_(expression < value, expression.is_(None)))
    return cast(ColumnElement[bool], or_(expression > value, expression.is_(None)))


class KeysetCursor:
    """Version-2 cursor: the last row's sort values, not an offset.

    ``values`` follows the request's sort, then the table id. ``filter_repr``
    and ``sort_repr`` are the same binding ``PageCursor`` uses.
    """

    __slots__ = ("filter_repr", "sort_repr", "values", "version")

    SCHEMA_VERSION = 2

    def __init__(
        self,
        *,
        values: Sequence[Any],
        filter_repr: str | None,
        sort_repr: str | None,
    ) -> None:
        self.version = self.SCHEMA_VERSION
        self.values = list(values)
        self.filter_repr = filter_repr
        self.sort_repr = sort_repr

    def encode(self) -> str:
        payload = {
            "v": self.version,
            "k": [_encode_sort_value(value) for value in self.values],
            "f": self.filter_repr,
            "s": self.sort_repr,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, token: str) -> KeysetCursor:
        payload = _load_cursor_payload(token)
        version = payload.get("v")
        if version == PageCursor.SCHEMA_VERSION:
            raise CursorError(
                "cursor resumes by offset and is no longer accepted; request the first page again"
            )
        if version != cls.SCHEMA_VERSION:
            raise CursorError(
                f"cursor schema version mismatch: got {version!r}, expected {cls.SCHEMA_VERSION}"
            )
        raw_keys = payload.get("k")
        filter_repr = payload.get("f")
        sort_repr = payload.get("s")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise CursorError("cursor missing sort values")
        if filter_repr is not None and not isinstance(filter_repr, str):
            raise CursorError("cursor 'f' must be a string or null")
        if sort_repr is not None and not isinstance(sort_repr, str):
            raise CursorError("cursor 's' must be a string or null")
        return cls(
            values=[_decode_sort_value(item) for item in raw_keys],
            filter_repr=filter_repr,
            sort_repr=sort_repr,
        )

    def matches_request(self, *, filter_repr: str | None, sort_repr: str | None) -> bool:
        return self.filter_repr == filter_repr and self.sort_repr == sort_repr


def _load_cursor_payload(token: str) -> dict[str, Any]:
    if not token:
        raise CursorError("empty cursor token")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CursorError(f"cursor is not valid base64-json: {exc}") from exc
    if not isinstance(payload, dict):
        raise CursorError("cursor payload must be an object")
    return payload


def _encode_sort_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"t": "z"}
    if isinstance(value, bool):
        return {"t": "b", "v": value}
    if isinstance(value, int):
        return {"t": "i", "v": value}
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CursorError("cursor cannot encode a non-finite sort value")
        return {"t": "f", "v": value}
    if isinstance(value, str):
        return {"t": "s", "v": value}
    if isinstance(value, uuid.UUID):
        return {"t": "u", "v": str(value)}
    if isinstance(value, datetime):
        return {"t": "ts", "v": value.isoformat()}
    if isinstance(value, Decimal):
        return {"t": "d", "v": format(value, "f")}
    raise CursorError(f"cursor cannot encode sort value of type {type(value).__name__}")


def _decode_sort_value(item: Any) -> Any:
    if not isinstance(item, dict):
        raise CursorError("cursor sort value must be an object")
    kind = item.get("t")
    if kind == "z":
        return None
    if "v" not in item:
        raise CursorError("cursor sort value is missing 'v'")
    raw = item["v"]
    if kind == "b":
        if not isinstance(raw, bool):
            raise CursorError("cursor boolean sort value is invalid")
        return raw
    if kind == "i":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise CursorError("cursor integer sort value is invalid")
        return raw
    if kind == "f":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CursorError("cursor float sort value is invalid")
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            raise CursorError("cursor float sort value is invalid")
        return value
    if kind == "s":
        if not isinstance(raw, str):
            raise CursorError("cursor string sort value is invalid")
        return raw
    if kind == "u":
        if not isinstance(raw, str):
            raise CursorError("cursor id sort value is invalid")
        try:
            return uuid.UUID(raw)
        except ValueError as exc:
            raise CursorError("cursor id sort value is invalid") from exc
    if kind == "ts":
        if not isinstance(raw, str):
            raise CursorError("cursor timestamp sort value is invalid")
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise CursorError("cursor timestamp sort value is invalid") from exc
    if kind == "d":
        if not isinstance(raw, str):
            raise CursorError("cursor decimal sort value is invalid")
        try:
            return Decimal(raw)
        except ArithmeticError as exc:
            raise CursorError("cursor decimal sort value is invalid") from exc
    raise CursorError("cursor sort value type is invalid")
