from __future__ import annotations

from cryo.db.models import Table


def catalog_name_for_table(table: Table) -> str:
    """Return the display catalog for a table's source."""
    source = table.source
    catalog_name = source.config.get("catalog_name") or source.config.get("catalog")
    if isinstance(catalog_name, str) and catalog_name:
        return catalog_name
    return source.name


def qualified_table_name(table: Table) -> str:
    return f"{catalog_name_for_table(table)}.{table.schema_name}.{table.table_name}"
