from __future__ import annotations

from typing import Any, Protocol

TableIdentifier = tuple[str, str]


class SourceCatalog(Protocol):
    """Read-side Iceberg catalog adapter."""

    def list_tables(self) -> list[TableIdentifier]:
        """Return ``(schema, table)`` identifiers."""

    def list_namespaces(self) -> list[str]:
        """Return namespace names only -- one cheap call, no per-namespace table listing.

        Used for connection tests, which need to prove reachability quickly, not enumerate the
        whole catalog. See PyIcebergSourceCatalog.list_namespaces for why this matters.
        """

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        """Table names within one already-known namespace -- one cheap call, not the
        O(all namespaces) walk list_tables() does. See PyIcebergSourceCatalog for why this
        matters."""

    def load_table(self, schema: str, table: str) -> Any:
        """Load and return a PyIceberg table object."""
