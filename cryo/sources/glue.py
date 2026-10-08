from __future__ import annotations

import logging
from collections.abc import Mapping

from cryo.sources.locations import CatalogTableLocation, list_glue_table_locations
from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog

logger = logging.getLogger(__name__)

_GLUE_KEY_FALLBACKS = {
    "s3.access-key-id": ("glue.access-key-id", "client.access-key-id"),
    "s3.secret-access-key": ("glue.secret-access-key", "client.secret-access-key"),
    "s3.session-token": ("glue.session-token", "client.session-token"),
    "s3.region": ("glue.region", "client.region"),
}


def _glue_client_keys(config: Mapping[str, object]) -> dict[str, object]:
    """PyIceberg Glue reads glue.* / client.*, not the s3.* keys Cryo stores."""
    prepared = dict(config)
    for source_key, destinations in _GLUE_KEY_FALLBACKS.items():
        value = prepared.get(source_key)
        if not isinstance(value, str) or not value.strip():
            continue
        for destination in destinations:
            current = prepared.get(destination)
            if not isinstance(current, str) or not current.strip():
                prepared[destination] = value
    return prepared


class GlueSourceCatalog(PyIcebergSourceCatalog):
    """PyIceberg Glue catalog wrapper."""

    def __init__(self, name: str, config: Mapping[str, object]) -> None:
        prepared = _glue_client_keys(config)
        super().__init__(name=name, config=prepared, default_catalog_type="glue")

    def list_table_locations(self) -> list[CatalogTableLocation]:
        """Read Glue ``StorageDescriptor.Location`` without loading Iceberg metadata."""
        glue_client = getattr(self._catalog, "glue", None) or getattr(self._catalog, "_glue", None)
        if glue_client is None:
            logger.warning(
                "Glue catalog %s has no Glue client; falling back to load_table locations",
                self.name,
            )
            return super().list_table_locations()
        try:
            return list_glue_table_locations(glue_client)
        except Exception:
            logger.exception(
                "Glue location listing failed for %s; falling back to load_table",
                self.name,
            )
            return super().list_table_locations()
