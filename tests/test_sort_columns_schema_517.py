"""#517: sort recommendations only offer columns the table actually has."""

from __future__ import annotations

import uuid

from cryo.optimizer.schema_columns import resolve_columns
from cryo.telemetry.parser import parse_query_predicates
from tests.test_path_to_a import _seed
from tests.test_presets_api_db import ApiDb


def test_resolve_columns_uses_schema_spelling_and_drops_unknowns() -> None:
    resolved, unknown = resolve_columns(
        ["order_no", "gold_orders_summ", "JO", "ORDER_NO"], ["Order_No", "status"]
    )
    assert resolved == ["Order_No"]
    assert unknown == ["gold_orders_summ", "JO"]


def test_join_columns_are_attributed_only_to_their_own_table() -> None:
    info = parse_query_predicates(
        "SELECT * FROM sales.orders o JOIN sales.customers c ON o.cid = c.id "
        "WHERE o.order_date > '2026-01-01' AND c.region = 'x'"
    )
    assert [c.column for c in info.filter_columns_for("orders")] == ["order_date"]
    assert [c.column for c in info.filter_columns_for("customers")] == ["region"]


def test_a_table_name_is_never_a_column() -> None:
    info = parse_query_predicates("SELECT * FROM a.orders WHERE orders = 1 AND status = 'x'")
    assert [c.column for c in info.filter_columns_for("orders")] == ["status"]


def _recommend(session, table_id: uuid.UUID, columns: list[str]) -> None:
    from cryo.db.models import LayoutRecommendation

    session.add(
        LayoutRecommendation(
            id=uuid.uuid4(),
            table_id=table_id,
            recommended_strategy="sort",
            recommended_columns=[{"column": c} for c in columns],
            status="pending",
        )
    )


def test_path_offers_only_schema_columns(api_db: ApiDb) -> None:
    from cryo.api.routes_path_to_a import _sort_columns
    from cryo.db.models import TableColumn

    session = api_db.session_factory()
    table_id = _seed(session)
    session.add(TableColumn(table_id=table_id, column_name="Order_No", data_type="string"))
    _recommend(session, table_id, ["order_no", "gold_orders_summ", "JO"])
    session.commit()
    assert _sort_columns(session, table_id) == ["Order_No"]
    session.close()


def test_path_offers_no_sort_step_when_no_column_is_real(api_db: ApiDb) -> None:
    from cryo.api.routes_path_to_a import _sort_columns
    from cryo.db.models import TableColumn

    session = api_db.session_factory()
    table_id = _seed(session)
    session.add(TableColumn(table_id=table_id, column_name="status", data_type="string"))
    _recommend(session, table_id, ["gold_orders_summ", "JO"])
    session.commit()
    assert _sort_columns(session, table_id) == []
    session.close()
