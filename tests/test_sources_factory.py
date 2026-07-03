from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cryo.sources.factory import catalog_from_config
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


def test_factory_supports_glue_aliases() -> None:
    with patch("cryo.sources.pyiceberg_catalog.load_catalog") as load_catalog:
        load_catalog.return_value = MagicMock()
        catalog = catalog_from_config("glue", "aws-glue", {"warehouse": "s3://bucket/"})
    assert isinstance(catalog, GlueSourceCatalog)


def test_factory_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported source type"):
        catalog_from_config("x", "polaris", {})
