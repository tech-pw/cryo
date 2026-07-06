from __future__ import annotations

from cryo.db.models import Source, Table


def catalog_name_for_source(source: Source) -> str:
    """Return the display catalog name for a source."""
    catalog_name = source.config.get("catalog_name") or source.config.get("catalog")
    if isinstance(catalog_name, str) and catalog_name:
        return catalog_name
    return source.name


def catalog_name_for_table(table: Table) -> str:
    """Return the display catalog for a table's source."""
    return catalog_name_for_source(table.source)


def qualified_table_name(table: Table) -> str:
    return f"{catalog_name_for_table(table)}.{table.schema_name}.{table.table_name}"


def qualified_name(catalog: str, schema_name: str, table_name: str) -> str:
    return f"{catalog}.{schema_name}.{table_name}"
