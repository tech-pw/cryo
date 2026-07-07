from __future__ import annotations

from typing import Any

from cryo.sources.base import SourceCatalog, TableIdentifier
from cryo.sources.latency import get_catalog_latency_monitor, timed_catalog_call


class MonitoredSourceCatalog:
    """Wrap a SourceCatalog and record per-operation latency."""

    def __init__(self, catalog: SourceCatalog, *, source_id: str) -> None:
        self._catalog = catalog
        self._monitor = get_catalog_latency_monitor(source_id)

    def list_tables(self) -> list[TableIdentifier]:
        return timed_catalog_call(self._monitor, "list_tables", self._catalog.list_tables)

    def list_namespaces(self) -> list[str]:
        return timed_catalog_call(self._monitor, "list_namespaces", self._catalog.list_namespaces)

    def list_all_namespaces(self, *, max_depth: int = 3) -> list[str]:
        return timed_catalog_call(
            self._monitor,
            "list_all_namespaces",
            lambda: self._catalog.list_all_namespaces(max_depth=max_depth),
        )

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        return timed_catalog_call(
            self._monitor,
            "list_tables_in_namespace",
            lambda: self._catalog.list_tables_in_namespace(namespace),
        )

    def load_table(self, schema: str, table: str) -> Any:
        return timed_catalog_call(
            self._monitor,
            "load_table",
            lambda: self._catalog.load_table(schema, table),
        )


def wrap_catalog(catalog: SourceCatalog, *, source_id: str) -> SourceCatalog:
    return MonitoredSourceCatalog(catalog, source_id=source_id)
