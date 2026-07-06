from __future__ import annotations

import uuid

from cryo.db.model_utils import catalog_name_for_table, qualified_name, qualified_table_name
from cryo.db.models import Source, Table


def test_qualified_table_name_uses_source_catalog_name() -> None:
    source = Source(
        id=uuid.uuid4(),
        name="rest-source",
        type="rest",
        config={"catalog_name": "prod"},
    )
    table = Table(
        id=uuid.uuid4(),
        source=source,
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
    )

    assert catalog_name_for_table(table) == "prod"
    assert qualified_table_name(table) == "prod.sales.orders"


def test_qualified_name_helper() -> None:
    assert qualified_name("prod", "sales", "orders") == "prod.sales.orders"


def test_qualified_table_name_falls_back_to_source_name() -> None:
    source = Source(
        id=uuid.uuid4(),
        name="local",
        type="rest",
        config={},
    )
    table = Table(
        id=uuid.uuid4(),
        source=source,
        source_id=source.id,
        schema_name="default",
        table_name="events",
    )

    assert qualified_table_name(table) == "local.default.events"
