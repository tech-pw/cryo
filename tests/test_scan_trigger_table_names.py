"""Tests for table_names support in cryo/db/repositories/datalake.py's scan-trigger validation.

table_names lets scope="tables" target a table that's never been scanned before (no Table
row/id exists for it yet) -- see cryo/api/schemas.py::TableNameRef and
cryo/scanner/run.py::_discover_candidates for the rest of the flow this feeds into.
"""

from __future__ import annotations

import pytest

from cryo.db.repositories.datalake import _validate_scan_scope


def test_table_names_with_source_id_is_valid(session) -> None:
    _validate_scan_scope(
        session,
        {
            "scope": "tables",
            "source_id": "11111111-1111-1111-1111-111111111111",
            "table_names": [{"schema_name": "ai_grader", "table_name": "gold_tests"}],
        },
    )


def test_table_names_without_source_id_is_rejected(session) -> None:
    with pytest.raises(ValueError, match="table_names requires source_id"):
        _validate_scan_scope(
            session,
            {
                "scope": "tables",
                "table_names": [{"schema_name": "ai_grader", "table_name": "gold_tests"}],
            },
        )


def test_tables_scope_requires_ids_or_names(session) -> None:
    with pytest.raises(ValueError, match="tables scan requires table_ids or table_names"):
        _validate_scan_scope(session, {"scope": "tables"})
