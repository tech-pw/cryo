"""#418: the tables cursor resumes after the last row, not at an offset."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select

from cryo.api.pagination import KeysetCursor, PageCursor
from cryo.db.models import Table, TableStats
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS


def _source(api_db) -> UUID:
    created = api_db.client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "keyset", "type": "rest", "config": {}},
    )
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


def _add(api_db, source_id: UUID, rows: list[tuple[str, str]]) -> None:
    with api_db.session_factory() as session:
        session.add_all(
            Table(source_id=source_id, schema_name=schema, table_name=name) for schema, name in rows
        )
        session.commit()


def _stats(table_id: UUID, *, small_files: int, scan_at: datetime) -> TableStats:
    return TableStats(
        table_id=table_id,
        scan_at=scan_at,
        needs_compaction=False,
        compaction_priority="low",
        grade="A",
        overall_score=80,
        total_files=1,
        small_files_count=small_files,
        total_size_gb=1.0,
    )


def _page(client, **params: object) -> dict:
    response = client.get("/api/v1/tables", headers=VIEWER_HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _names(body: dict) -> list[str]:
    return [row["table_name"] for row in body["items"]]


def _walk(client, **params: object) -> list[str]:
    names: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page_params = dict(params)
        if cursor is not None:
            page_params["cursor"] = cursor
        body = _page(client, **page_params)
        names.extend(_names(body))
        cursor = body["cursor"]
        if not body["has_more"]:
            assert cursor is None
            return names
        assert cursor
    raise AssertionError("pagination did not end")


def test_cursor_does_not_repeat_a_row_when_one_sorts_before_it(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("a", "b", "c", "d")])
    first = _page(api_db.client, sort="table_name", limit=2)
    assert _names(first) == ["a", "b"]
    assert first["has_more"] is True
    assert first["cursor"]

    _add(api_db, source_id, [("s", "aa")])
    second = _page(api_db.client, sort="table_name", limit=2, cursor=first["cursor"], offset=2)
    # `aa` sorts before the cursor, so it is not on this page, and `b` is not repeated.
    # `offset` is ignored once a cursor is present: applying it would skip `c`.
    assert _names(second) == ["c", "d"]
    assert second["has_more"] is False
    assert second["offset"] == 0


def test_cursor_does_not_skip_a_row_when_an_earlier_one_is_deleted(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("a", "b", "c", "d")])
    first = _page(api_db.client, sort="table_name", limit=2)
    assert _names(first) == ["a", "b"]

    with api_db.session_factory() as session:
        session.execute(delete(Table).where(Table.table_name == "a"))
        session.commit()

    second = _page(api_db.client, sort="table_name", limit=2, cursor=first["cursor"])
    assert _names(second) == ["c", "d"]


def test_descending_cursor_stays_after_the_last_row(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("a", "b", "c", "d")])
    first = _page(api_db.client, sort="-table_name", limit=2)
    assert _names(first) == ["d", "c"]

    _add(api_db, source_id, [("s", "cz")])
    second = _page(api_db.client, sort="-table_name", limit=2, cursor=first["cursor"])
    assert _names(second) == ["b", "a"]


def test_duplicate_sort_values_resume_on_the_table_id(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s1", "b"), ("s2", "b"), ("s", "a"), ("s", "c")])
    walked = _walk(api_db.client, sort="table_name", limit=1)
    assert walked == ["a", "b", "b", "c"]
    whole = _names(_page(api_db.client, sort="table_name", limit=10))
    assert walked == whole


def test_null_sort_values_stay_last_and_a_new_lower_value_is_not_repeated(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("low", "high", "bare")])
    scanned = datetime(2026, 1, 1, tzinfo=UTC)
    with api_db.session_factory() as session:
        ids = dict(session.execute(select(Table.table_name, Table.id)).all())
        session.add(_stats(ids["low"], small_files=1, scan_at=scanned))
        session.add(_stats(ids["high"], small_files=5, scan_at=scanned))
        session.commit()

    first = _page(api_db.client, sort="small_files_count", limit=1)
    assert _names(first) == ["low"]
    _add(api_db, source_id, [("s", "tiny")])
    with api_db.session_factory() as session:
        tiny_id = session.scalar(select(Table.id).where(Table.table_name == "tiny"))
        assert tiny_id is not None
        session.add(_stats(tiny_id, small_files=0, scan_at=scanned))
        session.commit()

    assert _names(
        _page(api_db.client, sort="small_files_count", limit=2, cursor=first["cursor"])
    ) == [
        "high",
        "bare",
    ]


def test_timestamp_cursor_round_trips_and_includes_a_row_inserted_after_it(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", "early"), ("s", "late")])
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)
    with api_db.session_factory() as session:
        ids = dict(session.execute(select(Table.table_name, Table.id)).all())
        session.add(_stats(ids["early"], small_files=1, scan_at=early))
        session.add(_stats(ids["late"], small_files=1, scan_at=late))
        session.commit()

    first = _page(api_db.client, sort="scan_at", limit=1)
    assert _names(first) == ["early"]
    position = KeysetCursor.decode(first["cursor"])
    assert isinstance(position.values[0], datetime)
    assert isinstance(position.values[-1], UUID)
    _add(api_db, source_id, [("s", "mid")])
    with api_db.session_factory() as session:
        mid_id = session.scalar(select(Table.id).where(Table.table_name == "mid"))
        assert mid_id is not None
        session.add(_stats(mid_id, small_files=1, scan_at=datetime(2026, 3, 1, tzinfo=UTC)))
        session.commit()

    assert _names(_page(api_db.client, sort="scan_at", limit=2, cursor=first["cursor"])) == [
        "mid",
        "late",
    ]


def test_computed_sort_cursor_walks_each_row_once(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("a", "b", "c")])
    walked = _walk(api_db.client, sort="score_delta_7d", limit=1)
    assert walked == _names(_page(api_db.client, sort="score_delta_7d", limit=10))
    assert sorted(walked) == ["a", "b", "c"]


def test_offset_still_selects_a_positional_page(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", name) for name in ("a", "b", "c", "d")])
    body = _page(api_db.client, sort="table_name", limit=2, offset=2)
    assert _names(body) == ["c", "d"]
    assert body["offset"] == 2
    assert body["cursor"] is None
    assert body["has_more"] is False


def test_offset_cursor_is_rejected(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", "a")])
    legacy = PageCursor(
        key="abc", offset_within_key=0, filter_repr=None, sort_repr="table_name"
    ).encode()
    response = api_db.client.get(
        "/api/v1/tables",
        headers=VIEWER_HEADERS,
        params={"sort": "table_name", "cursor": legacy},
    )
    assert response.status_code == 400
    assert "offset" in response.json()["detail"]


def test_cursor_is_bound_to_the_sort(api_db) -> None:
    source_id = _source(api_db)
    _add(api_db, source_id, [("s", "a"), ("s", "b")])
    first = _page(api_db.client, sort="table_name", limit=1)
    response = api_db.client.get(
        "/api/v1/tables",
        headers=VIEWER_HEADERS,
        params={"sort": "-table_name", "limit": 1, "cursor": first["cursor"]},
    )
    assert response.status_code == 409
