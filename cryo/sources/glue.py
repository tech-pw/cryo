from __future__ import annotations

import logging
from collections.abc import Mapping

from cryo.sources.locations import CatalogTableLocation, list_glue_table_locations
from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog

logger = logging.getLogger(__name__)


class GlueSourceCatalog(PyIcebergSourceCatalog):
    """PyIceberg Glue catalog wrapper."""

    def __init__(self, name: str, config: Mapping[str, object]) -> None:
        super().__init__(name=name, config=config, default_catalog_type="glue")

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
