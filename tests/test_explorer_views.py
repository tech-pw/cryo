"""Saved catalog views belong to the caller and do not schedule work."""

from __future__ import annotations

from cryo.api.routes_explorer_views import (
    ExplorerViewWrite,
    list_explorer_views,
    save_explorer_view,
)
from cryo.api.security import Principal, Role


def _principal(subject: str) -> Principal:
    return Principal(subject=subject, roles=frozenset({Role.VIEWER}))


def test_saved_view_is_private_to_the_owner(session) -> None:
    save_explorer_view(
        ExplorerViewWrite(name="Hot", filters={"needsCompaction": True}, sort="-small_files_count"),
        session,
        _principal("ada"),
    )
    save_explorer_view(
        ExplorerViewWrite(name="Hot", filters={"grades": ["F"]}),
        session,
        _principal("bea"),
    )

    ada = list_explorer_views(session, _principal("ada"))

    assert len(ada) == 1
    assert ada[0].filters == {"needsCompaction": True}
    assert ada[0].sort == "-small_files_count"


def test_saving_the_same_name_replaces_the_filter(session) -> None:
    owner = _principal("ada")
    save_explorer_view(ExplorerViewWrite(name="Hot", filters={"grades": ["D"]}), session, owner)
    replaced = save_explorer_view(
        ExplorerViewWrite(name="Hot", filters={"grades": ["F"]}),
        session,
        owner,
    )

    listed = list_explorer_views(session, owner)

    assert len(listed) == 1
    assert listed[0].id == replaced.id
    assert listed[0].filters == {"grades": ["F"]}
