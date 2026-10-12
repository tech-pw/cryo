from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog


def _make_catalog() -> tuple[PyIcebergSourceCatalog, MagicMock]:
    fake = MagicMock()
    with patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=fake):
        catalog = PyIcebergSourceCatalog(name="local", config={"uri": "http://localhost:8181"})
    return catalog, fake


def test_list_all_namespaces_returns_top_level_only_when_no_nesting() -> None:
    catalog, fake = _make_catalog()
    fake.list_namespaces.side_effect = lambda parent=(): (
        [("admin",), ("mview",)] if not parent else []
    )

    assert catalog.list_all_namespaces() == ["admin", "mview"]


def test_list_all_namespaces_walks_nested_namespaces() -> None:
    """REST catalogs nest schemas under an account/tenant prefix; the top-level-only
    list_namespaces() hides them. list_all_namespaces() walks every level so the scan-scope
    picker shows users the schemas they can actually target.
    """
    catalog, fake = _make_catalog()

    def fake_list(parent=()):
        if not parent:
            return [("sample_catalog",)]
        if parent == "sample_catalog":
            return [
                ("sample_catalog.bronze",),
                ("sample_catalog.silver",),
                ("sample_catalog.gold",),
            ]
        if parent == "sample_catalog.bronze":
            return [("sample_catalog.bronze.events",)]
        return []

    fake.list_namespaces.side_effect = fake_list

    result = catalog.list_all_namespaces()
    assert result == [
        "sample_catalog",
        "sample_catalog.bronze",
        "sample_catalog.bronze.events",
        "sample_catalog.gold",
        "sample_catalog.silver",
    ]


def test_list_all_namespaces_respects_max_depth() -> None:
    catalog, fake = _make_catalog()

    def fake_list(parent=()):
        if not parent:
            return [("a",)]
        if parent == "a":
            return [("a.b",)]
        if parent == "a.b":
            return [("a.b.c",)]
        return []

    fake.list_namespaces.side_effect = fake_list

    assert catalog.list_all_namespaces(max_depth=1) == ["a"]
    assert catalog.list_all_namespaces(max_depth=2) == ["a", "a.b"]
    assert catalog.list_all_namespaces(max_depth=3) == ["a", "a.b", "a.b.c"]
    assert catalog.list_all_namespaces(max_depth=0) == []


def test_list_all_namespaces_skips_failing_subtrees() -> None:
    """A misconfigured or transiently unavailable subtree shouldn't kill the whole enumeration."""
    catalog, fake = _make_catalog()

    def fake_list(parent=()):
        if not parent:
            return [("ok",), ("bad",)]
        if parent == "ok":
            return [("ok.inner",)]
        if parent == "bad":
            raise RuntimeError("connection reset")
        return []

    fake.list_namespaces.side_effect = fake_list

    result = catalog.list_all_namespaces()
    assert "ok" in result
    assert "ok.inner" in result
    assert "bad" in result


def test_list_all_namespaces_raises_when_root_list_fails() -> None:
    catalog, fake = _make_catalog()
    fake.list_namespaces.side_effect = RuntimeError("401 Unauthorized")
    with pytest.raises(RuntimeError, match="401"):
        catalog.list_all_namespaces()


def test_list_all_namespaces_skips_empty_children() -> None:
    catalog, fake = _make_catalog()
    fake.list_namespaces.side_effect = lambda parent=(): [
        ("admin",),
        ("mview",),
        ("",),
    ]

    result = catalog.list_all_namespaces()
    assert "admin" in result
    assert "mview" in result
    assert "" not in result
