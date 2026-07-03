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

    def load_table(self, schema: str, table: str) -> Any:
        return timed_catalog_call(
            self._monitor,
            "load_table",
            lambda: self._catalog.load_table(schema, table),
        )


def wrap_catalog(catalog: SourceCatalog, *, source_id: str) -> SourceCatalog:
    return MonitoredSourceCatalog(catalog, source_id=source_id)
