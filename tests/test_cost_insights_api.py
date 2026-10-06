"""End-to-end tests for the /cost-insights API routes.

Uses the same direct-function pattern as ``tests/test_aws_integration_api.py`` — exercises the
route handlers directly against the in-memory session fixture rather than going through HTTP.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from uuid import uuid4

from cryo.api.cost_insights_routes import (
    cost_by_bucket,
    cost_daily,
    create_cost_insight_config,
    delete_cost_insight_config,
    get_cost_insight_config,
    ingest_cur,
    ingest_inventory,
    patch_cost_insight_config,
    size_by_prefix,
)
from cryo.api.schemas import (
    AWSCostInsightConfigCreate,
    AWSCostInsightConfigPatch,
)
from cryo.db.models import (
    AWSCostInsightConfig,
    AWSInventoryObject,
    Source,
)


def _seed_source(session) -> Source:
    src = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="glue",
        config={},
        enabled=True,
        maintenance_schema_names=[],
    )
    session.add(src)
    session.flush()
    return src


def _write_cur_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "lineItem/UsageStartDate",
                "lineItem/ProductCode",
                "lineItem/UsageType",
                "lineItem/ResourceId",
                "lineItem/UnblendedCost",
            ]
        )
        writer.writerow(
            [
                "2026-07-01T00:00:00Z",
                "AmazonS3",
                "TimedStorage-ByteHrs",
                "arn:aws:s3:::lake-bucket/db/sales/x",
                "10.0",
            ]
        )
        writer.writerow(
            [
                "2026-07-02T00:00:00Z",
                "AmazonS3",
                "Requests-Tier1",
                "arn:aws:s3:::lake-bucket/db/sales/x",
                "0.05",
            ]
        )


def test_get_returns_404_when_no_config(session) -> None:
    src = _seed_source(session)
    try:
        get_cost_insight_config(src.id, session)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_create_then_get_then_patch(session) -> None:
    src = _seed_source(session)
    created = create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(
            cur_bucket="my-cur-bucket",
            cur_prefix="cur-prod/",
            cur_report_name="cryo-monthly",
            inventory_bucket="my-inv-bucket",
            inventory_prefix="inventory/prod/",
            access_key_id="AKIA123",
            secret_access_key="shh",
        ),
        session,
    )
    assert created.cur_bucket == "my-cur-bucket"
    assert created.enabled is True

    fetched = get_cost_insight_config(src.id, session)
    assert fetched.id == created.id

    patched = patch_cost_insight_config(
        src.id,
        AWSCostInsightConfigPatch(cur_bucket="renamed-cur-bucket"),
        session,
    )
    assert patched.cur_bucket == "renamed-cur-bucket"
    assert patched.cur_prefix == "cur-prod/"  # untouched field survives


def test_create_rejects_duplicate(session) -> None:
    src = _seed_source(session)
    payload = AWSCostInsightConfigCreate(cur_bucket="b", cur_prefix="p", secret_access_key="x")
    create_cost_insight_config(src.id, payload, session)
    try:
        create_cost_insight_config(src.id, payload, session)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_ingest_cur_from_fixture_persists_and_updates_config(session, tmp_path) -> None:
    src = _seed_source(session)
    fixture = tmp_path / "cur.csv"
    _write_cur_csv(fixture)
    create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(fixture_path=str(fixture)),
        session,
    )
    cfg = session.scalar(
        __import__("sqlalchemy")
        .select(AWSCostInsightConfig)
        .where(AWSCostInsightConfig.source_id == src.id)
    )
    assert cfg is not None

    response = ingest_cur(src.id, session)
    assert response.error is None
    assert response.rows_ingested == 2
    assert response.through_date == date(2026, 7, 2)

    # Config was updated with success metadata
    assert cfg.last_ingest_ok is True
    assert cfg.last_ingest_error is None
    assert cfg.last_ingested_at is not None


def test_by_bucket_route_reads_aggregated_cost(session, tmp_path) -> None:
    src = _seed_source(session)
    fixture = tmp_path / "cur.csv"
    _write_cur_csv(fixture)
    create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(fixture_path=str(fixture)),
        session,
    )
    ingest_cur(src.id, session)

    response = cost_by_bucket(src.id, session)
    assert response.source_id == src.id
    assert response.total_cost_usd == 10.05
    assert len(response.items) == 1
    assert response.items[0].bucket == "lake-bucket"


def test_daily_route_returns_series(session, tmp_path) -> None:
    src = _seed_source(session)
    fixture = tmp_path / "cur.csv"
    _write_cur_csv(fixture)
    create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(fixture_path=str(fixture)),
        session,
    )
    ingest_cur(src.id, session)

    series = cost_daily(src.id, session)
    assert len(series.items) == 2
    assert series.items[0].usage_date == date(2026, 7, 1)
    assert series.items[1].usage_date == date(2026, 7, 2)


def test_inventory_route_reads_prefix_aggregation(session) -> None:
    src = _seed_source(session)
    session.add(
        AWSInventoryObject(
            source_id=src.id,
            inventory_date=date(2026, 7, 1),
            bucket="lake-bucket",
            key="db/sales/a.parquet",
            size_bytes=1024,
            storage_class="STANDARD",
            table_prefix="db/sales",
        )
    )
    session.commit()

    response = size_by_prefix(src.id, session)
    assert len(response.items) == 1
    assert response.items[0].table_prefix == "db/sales"
    assert response.items[0].size_bytes == 1024


def test_ingest_cur_returns_404_when_no_config(session) -> None:
    src = _seed_source(session)
    try:
        ingest_cur(src.id, session)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_ingest_inventory_returns_404_when_no_config(session) -> None:
    src = _seed_source(session)
    try:
        ingest_inventory(src.id, session)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_delete_removes_config(session) -> None:
    src = _seed_source(session)
    create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(cur_bucket="b", cur_prefix="p"),
        session,
    )
    delete_cost_insight_config(src.id, session)
    try:
        get_cost_insight_config(src.id, session)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_secret_is_encrypted_at_rest(session) -> None:
    src = _seed_source(session)
    create_cost_insight_config(
        src.id,
        AWSCostInsightConfigCreate(
            cur_bucket="b",
            cur_prefix="p",
            access_key_id="AKIA",
            secret_access_key="plaintext-secret",
        ),
        session,
    )
    cfg = session.scalar(
        __import__("sqlalchemy")
        .select(AWSCostInsightConfig)
        .where(AWSCostInsightConfig.source_id == src.id)
    )
    assert cfg is not None
    assert cfg.secret_ref is not None
    assert cfg.secret_ref != "plaintext-secret"
    # The read response must never return the secret back to callers.
    fetched = get_cost_insight_config(src.id, session)
    assert not hasattr(fetched, "secret_access_key")
    assert not hasattr(fetched, "secret_ref")
