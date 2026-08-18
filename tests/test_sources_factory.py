from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cryo.sources.factory import catalog_from_config, is_hive_source_type
from cryo.sources.glue import GlueSourceCatalog
from cryo.sources.hive import HiveSourceCatalog
from cryo.sources.rest import RestSourceCatalog


def test_factory_supports_rest() -> None:
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        catalog = catalog_from_config("local", "rest", {"uri": "http://localhost:8181"})
    assert isinstance(catalog, RestSourceCatalog)


def test_factory_supports_hive_aliases() -> None:
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        catalog = catalog_from_config("hive", "hms", {"uri": "thrift://localhost:9083"})
    assert isinstance(catalog, HiveSourceCatalog)


def test_factory_passes_hms_config_to_pyiceberg() -> None:
    config = {
        "catalog_name": "hms",
        "type": "hive",
        "uri": "thrift://localhost:9083",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://localhost:9000",
        "s3.access-key-id": "minioadmin",
        "s3.secret-access-key": "minioadmin",
        "s3.path-style-access": "true",
    }
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        catalog_from_config("local-hms", "hive", config)
    load_catalog.assert_called_once_with(
        "hms",
        uri="thrift://localhost:9083",
        warehouse="s3://warehouse/",
        **{
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.path-style-access": "true",
            "type": "hive",
        },
    )


def test_factory_supports_glue_aliases() -> None:
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        catalog = catalog_from_config("glue", "aws-glue", {"warehouse": "s3://bucket/"})
    assert isinstance(catalog, GlueSourceCatalog)


def test_factory_supports_polaris_and_nessie_as_rest() -> None:
    # Polaris and Nessie both speak the standard Iceberg REST Catalog protocol, so they're routed
    # through the same adapter as a plain REST catalog.
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        polaris = catalog_from_config("polaris", "polaris", {"uri": "http://localhost:8181"})
        nessie = catalog_from_config("nessie", "nessie", {"uri": "http://localhost:19120/api/v1"})
    assert isinstance(polaris, RestSourceCatalog)
    assert isinstance(nessie, RestSourceCatalog)


def test_factory_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported source type"):
        catalog_from_config("x", "made-up-catalog-type", {})


def test_is_hive_source_type() -> None:
    assert is_hive_source_type("hive") is True
    assert is_hive_source_type("HMS") is True
    assert is_hive_source_type("hive_metastore") is True
    assert is_hive_source_type("rest") is False
