from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pyiceberg.catalog import load_catalog

from cryo.sources.base import TableIdentifier
from cryo.sources.locations import CatalogTableLocation, iceberg_table_location

logger = logging.getLogger(__name__)


@dataclass
class PyIcebergSourceCatalog:
    """Shared PyIceberg catalog wrapper for REST, Hive, and Glue sources."""

    name: str
    config: Mapping[str, Any]
    default_catalog_type: str | None = None
    _catalog: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        properties = dict(self.config)
        catalog_name = (
            properties.pop("catalog_name", None)
            or properties.pop("catalog", None)
            or properties.pop("name", self.name)
        )
        catalog_type = properties.pop("type", None) or self.default_catalog_type
        if catalog_type and "type" not in properties:
            properties["type"] = catalog_type
        self._catalog = load_catalog(str(catalog_name), **properties)

    def list_tables(self) -> list[TableIdentifier]:
        tables: set[TableIdentifier] = set()
        for namespace in sorted(self._catalog.list_namespaces(), key=_namespace_name):
            for identifier in self._catalog.list_tables(namespace):
                tables.add(_schema_table(identifier, default_schema=_namespace_name(namespace)))
        return sorted(tables)

    def list_namespaces(self) -> list[str]:
        """Cheap connectivity check: one call, no per-namespace table enumeration.

        `list_tables()` above is O(namespaces) round-trips (list_tables per namespace) and can
        take minutes against a real Hive Metastore with many databases -- unsuitable for a
        "Test connection" click a human is waiting on. This is the single call that proves auth
        and reachability; real table discovery belongs to the async Scanner
        (cryo/scanner/run.py), not the test button.
        """
        return sorted({_namespace_name(namespace) for namespace in self._catalog.list_namespaces()})

    def list_all_namespaces(self, *, max_depth: int = 3) -> list[str]:
        """BFS walk that returns every namespace under root, joined as dotted strings.

        Iceberg's ``list_namespaces()`` is top-level only; nested namespaces (the common case in
        REST catalogs, e.g. ``account.bronze.events``) are invisible to it. The scan-scope picker
        calls this so users see the schemas they can actually target. The walk is breadth-first
        and bounded by ``max_depth`` to keep worst-case latency predictable on hostile catalogs.

        ``max_depth`` counts levels below root: ``max_depth=1`` returns only top-level namespaces,
        ``max_depth=2`` adds one level of children, etc.
        """
        if max_depth < 1:
            return []
        seen: set[str] = set()
        frontier: list[str] = [""]
        for level in range(max_depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            for parent in frontier:
                try:
                    children = (
                        self._catalog.list_namespaces(parent)
                        if parent
                        else self._catalog.list_namespaces()
                    )
                except Exception:
                    logger.warning(
                        "list_namespaces failed for parent=%r at level=%d; skipping subtree",
                        parent,
                        level,
                    )
                    continue
                for child in children:
                    if not child:
                        continue
                    name = _namespace_name(child)
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    if level + 1 < max_depth:
                        next_frontier.append(name)
            frontier = next_frontier
        return sorted(seen)

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        """Table names within a single, already-known namespace -- one call, same cost as
        list_namespaces() above, not the O(all namespaces) walk list_tables() does.

        Lets the scan-scope "Specific tables" picker offer real table names once a user has
        narrowed to one schema, even before a full scan of the whole lakehouse has ever run.
        """
        return sorted(
            _schema_table(identifier, default_schema=namespace)[1]
            for identifier in self._catalog.list_tables(namespace)
        )

    def load_table(self, schema: str, table: str) -> Any:
        return self._catalog.load_table((schema, table))

    def list_table_locations(self) -> list[CatalogTableLocation]:
        """REST/default path: load each Iceberg table. Hive/Glue override this."""
        results: list[CatalogTableLocation] = []
        for schema, table in self.list_tables():
            try:
                location = iceberg_table_location(self.load_table(schema, table))
                results.append(
                    CatalogTableLocation(schema_name=schema, table_name=table, location=location)
                )
            except Exception as exc:
                logger.warning(
                    "load_table failed for %s.%s.%s: %s",
                    self.name,
                    schema,
                    table,
                    exc,
                )
                results.append(
                    CatalogTableLocation(
                        schema_name=schema,
                        table_name=table,
                        location=None,
                        error=str(exc)[:500],
                    )
                )
        return results


def _namespace_name(namespace: Any) -> str:
    if isinstance(namespace, str):
        return namespace
    if isinstance(namespace, tuple):
        return ".".join(str(part) for part in namespace)
    return str(namespace)


def _schema_table(identifier: Any, *, default_schema: str) -> TableIdentifier:
    parts = tuple(str(part) for part in identifier) if isinstance(identifier, tuple) else ()
    if not parts:
        parts = tuple(str(identifier).split("."))
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return default_schema, parts[0]
    raise ValueError(f"Unable to parse Iceberg table identifier: {identifier!r}")
