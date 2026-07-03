from __future__ import annotations

from typing import Any, Protocol

TableIdentifier = tuple[str, str]


class SourceCatalog(Protocol):
    """Read-side Iceberg catalog adapter."""

    def list_tables(self) -> list[TableIdentifier]:
        """Return ``(schema, table)`` identifiers."""

    def load_table(self, schema: str, table: str) -> Any:
        """Load and return a PyIceberg table object."""
