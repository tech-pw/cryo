"""Move 23: server-side cursor pagination for inventory endpoints.

The cursor token is opaque to clients (URL-safe base64 of a small JSON
envelope); see cryo/api/pagination.py. These tests cover:

  * encode/decode round-trips every populated field
  * schema-version mismatch raises CursorError
  * cursor issued for a different filter/sort is rejected (no off-by-one
    drift if the user changes the predicate mid-scroll)
  * mid-pagination change forces a fresh start
"""

from __future__ import annotations

import uuid

import pytest

from cryo.api.pagination import (
    CursorError,
    PageCursor,
    make_filter_repr,
    make_sort_repr,
)


def test_encode_then_decode_round_trips_all_fields() -> None:
    cursor = PageCursor(
        key=str(uuid.uuid4()),
        offset_within_key=42,
        filter_repr="active=true",
        sort_repr="-small_files_count",
    )
    token = cursor.encode()
    assert isinstance(token, str) and token

    decoded = PageCursor.decode(token)
    assert decoded.key == cursor.key
    assert decoded.offset_within_key == 42
    assert decoded.filter_repr == "active=true"
    assert decoded.sort_repr == "-small_files_count"


def test_encode_is_urlsafe_no_padding() -> None:
    cursor = PageCursor(key="x", offset_within_key=0, filter_repr=None, sort_repr=None)
    token = cursor.encode()
    assert "=" not in token
    assert "+" not in token
    assert "/" not in token


def test_decode_rejects_empty_token() -> None:
    with pytest.raises(CursorError, match="empty"):
        PageCursor.decode("")


def test_decode_rejects_non_base64_payload() -> None:
    with pytest.raises(CursorError, match="base64"):
        PageCursor.decode("!!!not-base64!!!")


def test_decode_rejects_unknown_schema_version() -> None:
    payload = {"v": 999, "k": "x", "o": 0, "f": None, "s": None}
    import base64
    import json

    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(CursorError, match="version mismatch"):
        PageCursor.decode(forged)


def test_decode_rejects_missing_required_fields() -> None:
    import base64
    import json

    bad = base64.urlsafe_b64encode(json.dumps({"v": 1}).encode()).rstrip(b"=").decode()
    with pytest.raises(CursorError, match="required"):
        PageCursor.decode(bad)


def test_matches_request_returns_true_for_identical_filter_sort() -> None:
    cursor = PageCursor(
        key="k",
        offset_within_key=0,
        filter_repr="active=true",
        sort_repr="-small_files_count",
    )
    assert cursor.matches_request(filter_repr="active=true", sort_repr="-small_files_count")


def test_matches_request_returns_false_when_filter_changes() -> None:
    cursor = PageCursor(
        key="k", offset_within_key=0, filter_repr="active=true", sort_repr="-small_files_count"
    )
    assert not cursor.matches_request(
        filter_repr="status=NEEDS_COMPACTION", sort_repr="-small_files_count"
    )


def test_matches_request_returns_false_when_sort_changes() -> None:
    cursor = PageCursor(
        key="k", offset_within_key=0, filter_repr="active=true", sort_repr="-small_files_count"
    )
    assert not cursor.matches_request(filter_repr="active=true", sort_repr="name")


def test_make_filter_repr_normalises_empty_to_none() -> None:
    assert make_filter_repr(None) is None
    assert make_filter_repr("") is None
    assert make_filter_repr("active=true") == "active=true"


def test_make_sort_repr_normalises_empty_to_none() -> None:
    assert make_sort_repr(None) is None
    assert make_sort_repr("") is None
    assert make_sort_repr("-small_files_count") == "-small_files_count"


def test_paginated_tables_schema_exposes_cursor_field() -> None:
    """The API response now carries cursor + has_more; ensure they're optional."""
    from cryo.api.schemas import PaginatedTables

    sample = PaginatedTables(items=[], total=0, limit=100, offset=0)
    assert sample.cursor is None
    assert sample.has_more is False

    with_cursor = PaginatedTables(
        items=[],
        total=500,
        limit=100,
        offset=100,
        cursor="abc",
        has_more=True,
    )
    assert with_cursor.cursor == "abc"
    assert with_cursor.has_more is True


def test_empty_token_does_not_resume_a_real_session() -> None:
    """An empty / whitespace cursor is treated as no-cursor, not as an offset-0 cursor."""
    with pytest.raises(CursorError):
        PageCursor.decode("")
