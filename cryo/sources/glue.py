from __future__ import annotations

from collections.abc import Mapping

from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog


class GlueSourceCatalog(PyIcebergSourceCatalog):
    """PyIceberg Glue catalog wrapper."""

    def __init__(self, name: str, config: Mapping[str, object]) -> None:
        super().__init__(name=name, config=config, default_catalog_type="glue")
