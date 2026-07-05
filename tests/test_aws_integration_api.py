"""Tests for the free-tier-only AWS S3 cost/usage integration API.

Exercises the route handlers directly against the in-memory session fixture (same pattern as
test_quality_profiler_integration.py) rather than a full HTTP TestClient, since these are plain
functions taking a session + typed payload. AWS calls themselves are mocked — no network, no real
AWS credentials needed to run this suite.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from cryo.api.routes import (
    create_aws_integration,
    delete_aws_integration,
    get_aws_integration,
    get_aws_usage,
    patch_aws_integration,
)
from cryo.api.routes import test_aws_integration as run_aws_integration_test
from cryo.api.schemas import AWSIntegrationCreate, AWSIntegrationPatch
from cryo.db.models import Source
from cryo.integrations.aws_s3_metrics import (
    AWSConnectionTestResult,
    BucketStorageMetrics,
    S3PriceInfo,
)


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    session.flush()
    return source


def test_create_get_and_patch_aws_integration(session):
    source = _seed_source(session)

    created = create_aws_integration(
        source.id,
        AWSIntegrationCreate(
            bucket="my-bucket",
            region="us-east-1",
            access_key_id="AKIA123",
            secret_access_key="shh",
        ),
        session,
    )
    assert created.bucket == "my-bucket"
    assert created.region == "us-east-1"
    assert created.access_key_id == "AKIA123"
    assert not hasattr(created, "secret_access_key")  # never round-tripped back out

    fetched = get_aws_integration(source.id, session)
    assert fetched.id == created.id
    assert fetched.bucket == "my-bucket"

    patched = patch_aws_integration(
        source.id, AWSIntegrationPatch(bucket="renamed-bucket"), session
    )
    assert patched.bucket == "renamed-bucket"
    assert patched.region == "us-east-1"  # untouched fields survive


def test_create_aws_integration_rejects_duplicate(session):
    source = _seed_source(session)
    payload = AWSIntegrationCreate(bucket="b", region="us-east-1")
    create_aws_integration(source.id, payload, session)

    with pytest.raises(HTTPException) as exc_info:
        create_aws_integration(source.id, payload, session)
    assert exc_info.value.status_code == 400


def test_create_aws_integration_requires_existing_source(session):
    with pytest.raises(HTTPException) as exc_info:
        create_aws_integration(
            uuid4(), AWSIntegrationCreate(bucket="b", region="us-east-1"), session
        )
    assert exc_info.value.status_code == 404


def test_get_aws_integration_404_when_not_configured(session):
    source = _seed_source(session)
    with pytest.raises(HTTPException) as exc_info:
        get_aws_integration(source.id, session)
    assert exc_info.value.status_code == 404


def test_delete_aws_integration_removes_it(session):
    source = _seed_source(session)
    create_aws_integration(
        source.id, AWSIntegrationCreate(bucket="b", region="us-east-1"), session
    )

    delete_aws_integration(source.id, session)

    with pytest.raises(HTTPException):
        get_aws_integration(source.id, session)


def test_test_aws_integration_records_result(session):
    source = _seed_source(session)
    create_aws_integration(
        source.id, AWSIntegrationCreate(bucket="b", region="us-east-1"), session
    )

    with patch(
        "cryo.api.routes.check_aws_connection",
        return_value=AWSConnectionTestResult(
            ok=True, account_id="123456789012", arn="arn:aws:iam::123456789012:user/x"
        ),
    ):
        response = run_aws_integration_test(source.id, session)

    assert response.ok is True
    assert response.account_id == "123456789012"

    stored = get_aws_integration(source.id, session)
    assert stored.last_test_ok is True
    assert stored.last_tested_at is not None


def test_get_aws_usage_combines_metrics_and_pricing(session):
    source = _seed_source(session)
    create_aws_integration(
        source.id, AWSIntegrationCreate(bucket="b", region="us-east-1"), session
    )

    with (
        patch(
            "cryo.api.routes.fetch_bucket_storage_metrics",
            return_value=BucketStorageMetrics(
                bucket="b",
                region="us-east-1",
                measured_at=None,
                size_bytes_by_storage_class={"StandardStorage": 5_000_000_000},
                object_count=1234,
            ),
        ),
        patch(
            "cryo.api.routes.fetch_s3_price_per_gb",
            return_value=S3PriceInfo(
                region="us-east-1", storage_class="General Purpose", price_per_gb_month=0.023
            ),
        ),
    ):
        usage = get_aws_usage(source.id, session)

    assert usage.bucket == "b"
    assert usage.total_size_bytes == 5_000_000_000
    assert usage.object_count == 1234
    assert usage.price_per_gb_month == 0.023
    assert usage.price_source == "aws_price_list_api"


def test_get_aws_usage_404_when_no_integration(session):
    source = _seed_source(session)
    with pytest.raises(HTTPException) as exc_info:
        get_aws_usage(source.id, session)
    assert exc_info.value.status_code == 404


def test_get_aws_usage_404_when_disabled(session):
    source = _seed_source(session)
    create_aws_integration(
        source.id,
        AWSIntegrationCreate(bucket="b", region="us-east-1", enabled=False),
        session,
    )
    with pytest.raises(HTTPException) as exc_info:
        get_aws_usage(source.id, session)
    assert exc_info.value.status_code == 404
