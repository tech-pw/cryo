"""Helpers for catalog-native table warehouse locations (no Iceberg metadata load)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HMS_TABLE_BATCH_SIZE = 100


@dataclass(frozen=True)
class CatalogTableLocation:
    """Warehouse location as stored in the catalog, plus a per-table error if unreadable."""

    schema_name: str
    table_name: str
    location: str | None
    error: str | None = None


def iceberg_table_location(table_obj: object) -> str | None:
    """Resolve ``location`` on a loaded PyIceberg table without importing the scanner."""
    location = getattr(table_obj, "location", None)
    if callable(location):
        try:
            location = location()
        except TypeError:
            location = None
    if location:
        return str(location)
    metadata = getattr(table_obj, "metadata", None)
    if metadata is not None:
        meta_location = getattr(metadata, "location", None)
        if meta_location:
            return str(meta_location)
    return None


def hive_storage_location(hive_table: object) -> str | None:
    sd = getattr(hive_table, "sd", None)
    if sd is None and isinstance(hive_table, dict):
        sd = hive_table.get("sd") or hive_table.get("StorageDescriptor")
    if sd is None:
        return None
    location = getattr(sd, "location", None)
    if location is None and isinstance(sd, dict):
        location = sd.get("location") or sd.get("Location")
    return str(location) if location else None


def hive_table_name(hive_table: object) -> str | None:
    for attr in ("tableName", "table_name"):
        value = getattr(hive_table, attr, None)
        if value:
            return str(value)
    if isinstance(hive_table, dict):
        value = (
            hive_table.get("tableName") or hive_table.get("table_name") or hive_table.get("Name")
        )
        return str(value) if value else None
    return None


def hive_db_name(hive_table: object, default: str) -> str:
    for attr in ("dbName", "db_name"):
        value = getattr(hive_table, attr, None)
        if value:
            return str(value)
    if isinstance(hive_table, dict):
        value = (
            hive_table.get("dbName") or hive_table.get("db_name") or hive_table.get("DatabaseName")
        )
        if value:
            return str(value)
    return default


def call_hms(client: object, method: str, *args: Any) -> Any:
    fn = getattr(client, method, None)
    if fn is None:
        raise AttributeError(method)
    return fn(*args)


def list_hive_table_locations(client: object) -> list[CatalogTableLocation]:
    """Enumerate HMS ``sd.location`` without loading Iceberg metadata.json."""
    databases = list(call_hms(client, "get_all_databases"))
    results: list[CatalogTableLocation] = []
    for database in databases:
        db_name = str(database)
        try:
            names = [str(name) for name in call_hms(client, "get_all_tables", db_name)]
        except Exception as exc:
            results.append(
                CatalogTableLocation(
                    schema_name=db_name,
                    table_name="*",
                    location=None,
                    error=f"list tables failed: {exc}"[:500],
                )
            )
            continue
        for hive_table in _hive_tables_for_db(client, db_name, names):
            table_name = hive_table_name(hive_table)
            if not table_name:
                continue
            schema_name = hive_db_name(hive_table, db_name)
            try:
                location = hive_storage_location(hive_table)
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=location,
                    )
                )
            except Exception as exc:
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=None,
                        error=str(exc)[:500],
                    )
                )
    return results


def _hive_tables_for_db(client: object, db_name: str, names: list[str]) -> list[object]:
    if not names:
        return []
    if hasattr(client, "get_table_objects_by_name"):
        tables: list[object] = []
        for start in range(0, len(names), HMS_TABLE_BATCH_SIZE):
            chunk = names[start : start + HMS_TABLE_BATCH_SIZE]
            try:
                tables.extend(list(call_hms(client, "get_table_objects_by_name", db_name, chunk)))
            except Exception:
                tables.extend(_hive_tables_one_by_one(client, db_name, chunk))
        return tables
    return _hive_tables_one_by_one(client, db_name, names)


def _hive_tables_one_by_one(client: object, db_name: str, names: list[str]) -> list[object]:
    tables: list[object] = []
    for name in names:
        try:
            tables.append(call_hms(client, "get_table", db_name, name))
        except Exception:
            continue
    return tables


def glue_storage_location(glue_table: dict[str, Any]) -> str | None:
    descriptor = glue_table.get("StorageDescriptor") or {}
    location = descriptor.get("Location")
    return str(location) if location else None


def list_glue_table_locations(glue_client: object) -> list[CatalogTableLocation]:
    """Enumerate Glue ``StorageDescriptor.Location`` without loading Iceberg metadata."""
    results: list[CatalogTableLocation] = []
    for database in _glue_database_names(glue_client):
        try:
            for glue_table in _glue_tables_in_database(glue_client, database):
                table_name = str(glue_table.get("Name") or "")
                if not table_name:
                    continue
                schema_name = str(glue_table.get("DatabaseName") or database)
                results.append(
                    CatalogTableLocation(
                        schema_name=schema_name,
                        table_name=table_name,
                        location=glue_storage_location(glue_table),
                    )
                )
        except Exception as exc:
            results.append(
                CatalogTableLocation(
                    schema_name=database,
                    table_name="*",
                    location=None,
                    error=f"list tables failed: {exc}"[:500],
                )
            )
    return results


def _glue_database_names(glue_client: object) -> list[str]:
    names: list[str] = []
    paginator = getattr(glue_client, "get_paginator", None)
    if callable(paginator):
        for page in paginator("get_databases").paginate():
            for database in page.get("DatabaseList", []):
                name = database.get("Name")
                if name:
                    names.append(str(name))
        return names
    response = glue_client.get_databases()  # type: ignore[attr-defined]
    for database in response.get("DatabaseList", []):
        name = database.get("Name")
        if name:
            names.append(str(name))
    return names


def _glue_tables_in_database(glue_client: object, database: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    paginator = getattr(glue_client, "get_paginator", None)
    if callable(paginator):
        for page in paginator("get_tables").paginate(DatabaseName=database):
            tables.extend(page.get("TableList", []))
        return tables
    response = glue_client.get_tables(DatabaseName=database)  # type: ignore[attr-defined]
    tables.extend(response.get("TableList", []))
    return tables
