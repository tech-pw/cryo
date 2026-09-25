"""Regression, 2026-07-24: _resolve_spark_fallback (shared by the silent-data-loss repair
and ad-hoc diagnostic endpoints) had the same bug as native_admit._fallback_engine_config --
it hardcoded a "cdp" catalog-name default and never propagated the source's own
catalog_uri/warehouse, so a table from any other catalog got dispatched against whatever
catalog/metastore/warehouse the shared fallback destination's static config happened to be
tuned for. Confirmed live: lms.silver_adminactions (source catalog_name "hive", dev
metastore/warehouse) fell back to a destination statically configured for "cdp" against the
prod metastore/warehouse, and Spark failed with MISSING_CATALOG_ABILITY.PROCEDURES.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from cryo.api.advanced_routes import _resolve_spark_fallback
from cryo.db.models import Destination, Preset, Source, Table, TableAssignment


def _seed(session, *, source_config: dict, fallback_config: dict) -> object:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config=source_config)
    main_destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    fallback_destination = Destination(
        id=uuid4(), name=f"fallback-{uuid4()}", engine="spark_emr", config=fallback_config
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="lms",
        table_name="silver_adminactions",
        is_active=True,
    )
    session.add_all([source, main_destination, fallback_destination, table])
    session.flush()

    main_destination.config = {"sql_fallback_destination_id": str(fallback_destination.id)}
    preset = Preset(
        id=uuid4(),
        name=f"preset-{uuid4()}",
        destination_id=main_destination.id,
        operations=["expire_snapshots"],
        params={},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    session.add(TableAssignment(table_id=table.id, preset_id=preset.id))
    session.commit()
    return table.id


def test_source_catalog_connection_overrides_destination_default(session) -> None:
    table_id = _seed(
        session,
        source_config={
            "catalog_name": "hive",
            "uri": "thrift://metastore-dev.example.invalid:9083",
            "warehouse": "s3://example-dev-bucket/",
        },
        fallback_config={
            "catalog": "cdp",
            "spark_k8s": {
                "catalog_impl": "hive",
                "catalog_uri": "thrift://metastore.example.invalid:9083",
                "catalog_warehouse": "s3://example-prod-bucket/",
            },
        },
    )

    client, table_ref = _resolve_spark_fallback(session, table_id)

    assert table_ref == "hive.lms.silver_adminactions"
    assert client._config.catalog_uri == "thrift://metastore-dev.example.invalid:9083"
    assert client._config.catalog_warehouse == "s3://example-dev-bucket/"


def test_missing_source_catalog_name_raises_clear_error(session) -> None:
    table_id = _seed(
        session,
        source_config={"uri": "thrift://x:9083", "warehouse": "s3://x/"},
        fallback_config={"catalog": "cdp"},
    )

    with pytest.raises(HTTPException) as exc_info:
        _resolve_spark_fallback(session, table_id)
    assert exc_info.value.status_code == 422
    assert "catalog_name" in exc_info.value.detail
