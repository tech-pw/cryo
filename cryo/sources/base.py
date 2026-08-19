from __future__ import annotations

from typing import Any, Protocol

from cryo.sources.locations import CatalogTableLocation

TableIdentifier = tuple[str, str]


class SourceCatalog(Protocol):
    """Read-side Iceberg catalog adapter."""

    def list_tables(self) -> list[TableIdentifier]:
        """Return ``(schema, table)`` identifiers."""

    def list_namespaces(self) -> list[str]:
        """Return top-level namespace names only -- one cheap call, no per-namespace table listing.

        Used for connection tests, which need to prove reachability quickly, not enumerate the
        whole catalog. See PyIcebergSourceCatalog.list_namespaces for why this matters.
        """

    def list_all_namespaces(self, *, max_depth: int = 3) -> list[str]:
        """Recursively enumerate every namespace under the catalog root, up to ``max_depth`` levels.

        ``list_namespaces()`` returns only top-level namespaces per the Iceberg spec -- for catalogs
        that nest schemas (e.g. ``prod_data.bronze.events``) that's just ``prod_data``, which is
        useless for the scan-scope picker. This walks ``list_namespaces(parent)`` for every parent
        found so far. Capped at ``max_depth`` so a misconfigured catalog can't pin a worker.

        Returns dotted namespace names (matching what ``list_tables_in_namespace`` accepts).
        """

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        """Table names within one already-known namespace -- one cheap call, not the
        O(all namespaces) walk list_tables() does. See PyIcebergSourceCatalog for why this
        matters."""

    def load_table(self, schema: str, table: str) -> Any:
        """Load and return a PyIceberg table object."""

    def list_table_locations(self) -> list[CatalogTableLocation]:
        """Return warehouse locations from catalog metadata (not Iceberg metadata.json).

        Hive implementations MUST query HMS over Thrift (``ThriftHiveMetastore``)
        for ``StorageDescriptor.location``. Glue MUST use ``GetTables``. REST
        catalogs fall back to ``load_table().location()`` and record per-table errors.
        """
