from __future__ import annotations

from collections.abc import Mapping

from cryo.sources.hms_thrift import hms_uri_from_config, list_hive_table_locations_from_uri
from cryo.sources.locations import CatalogTableLocation
from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog


class HiveSourceCatalog(PyIcebergSourceCatalog):
    """PyIceberg Hive Metastore catalog wrapper."""

    def __init__(self, name: str, config: Mapping[str, object]) -> None:
        super().__init__(name=name, config=config, default_catalog_type="hive")

    def list_table_locations(self) -> list[CatalogTableLocation]:
        """Read HMS ``sd.location`` over Thrift so broken Iceberg aliases still appear.

        ``load_table()`` opens metadata.json and fails for the unqueryable
        ``_evolving`` twins that caused the shared-S3 drop incident. This path
        talks to Hive Metastore directly (``ThriftHiveMetastore.Client``), not
        through PyIceberg.
        """
        return list_hive_table_locations_from_uri(hms_uri_from_config(self.config))
