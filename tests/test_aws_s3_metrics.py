from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from cryo.integrations.aws_s3_metrics import (
    AWSCredentials,
    fetch_bucket_storage_metrics,
    fetch_s3_price_per_gb,
)
from cryo.integrations.aws_s3_metrics import test_aws_connection as check_aws_connection


def _credentials() -> AWSCredentials:
    return AWSCredentials(region="us-east-1", access_key_id="AKIA...", secret_access_key="secret")


def test_test_aws_connection_reports_success() -> None:
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/cryo",
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_sts

    with patch("boto3.Session", return_value=mock_session):
        result = check_aws_connection(_credentials())

    assert result.ok is True
    assert result.account_id == "123456789012"
    assert result.arn == "arn:aws:iam::123456789012:user/cryo"
    assert result.error is None


def test_test_aws_connection_reports_failure_without_raising() -> None:
    mock_session = MagicMock()
    mock_session.client.side_effect = RuntimeError("no credentials found")

    with patch("boto3.Session", return_value=mock_session):
        result = check_aws_connection(_credentials())

    assert result.ok is False
    assert result.account_id is None
    assert "no credentials found" in (result.error or "")


def test_fetch_bucket_storage_metrics_aggregates_storage_classes() -> None:
    mock_cloudwatch = MagicMock()

    def get_metric_statistics(*, MetricName, Dimensions, **_kwargs):
        storage_type = next((d["Value"] for d in Dimensions if d["Name"] == "StorageType"), None)
        if MetricName == "BucketSizeBytes" and storage_type == "StandardStorage":
            return {
                "Datapoints": [
                    {"Average": 1_000.0, "Timestamp": datetime(2026, 7, 4, tzinfo=UTC)},
                    {"Average": 2_000.0, "Timestamp": datetime(2026, 7, 5, tzinfo=UTC)},
                ]
            }
        if MetricName == "NumberOfObjects":
            return {
                "Datapoints": [
                    {"Average": 42.0, "Timestamp": datetime(2026, 7, 5, tzinfo=UTC)},
                ]
            }
        return {"Datapoints": []}

    mock_cloudwatch.get_metric_statistics.side_effect = get_metric_statistics
    mock_session = MagicMock()
    mock_session.client.return_value = mock_cloudwatch

    with patch("boto3.Session", return_value=mock_session):
        metrics = fetch_bucket_storage_metrics("my-bucket", _credentials())

    assert metrics.bucket == "my-bucket"
    assert metrics.size_bytes_by_storage_class["StandardStorage"] == 2_000
    assert metrics.total_size_bytes == 2_000
    assert metrics.object_count == 42
    assert metrics.measured_at == datetime(2026, 7, 5, tzinfo=UTC)


def test_fetch_bucket_storage_metrics_handles_no_data() -> None:
    mock_cloudwatch = MagicMock()
    mock_cloudwatch.get_metric_statistics.return_value = {"Datapoints": []}
    mock_session = MagicMock()
    mock_session.client.return_value = mock_cloudwatch

    with patch("boto3.Session", return_value=mock_session):
        metrics = fetch_bucket_storage_metrics("empty-bucket", _credentials())

    assert metrics.total_size_bytes == 0
    assert metrics.object_count is None
    assert metrics.measured_at is None


def test_fetch_bucket_storage_metrics_uses_get_metric_statistics_not_get_metric_data() -> None:
    """GetMetricData is always billed regardless of the CloudWatch free tier; GetMetricStatistics
    falls under the 1M-free-requests/month quota — this integration must only ever call the
    latter, since it's supposed to be free to run."""
    mock_cloudwatch = MagicMock()
    mock_cloudwatch.get_metric_statistics.return_value = {"Datapoints": []}
    mock_session = MagicMock()
    mock_session.client.return_value = mock_cloudwatch

    with patch("boto3.Session", return_value=mock_session):
        fetch_bucket_storage_metrics("my-bucket", _credentials())

    assert mock_cloudwatch.get_metric_statistics.called
    assert not mock_cloudwatch.get_metric_data.called


def test_fetch_s3_price_per_gb_returns_none_for_unmapped_region() -> None:
    result = fetch_s3_price_per_gb("mars-central-1", _credentials())
    assert result is None


def test_fetch_s3_price_per_gb_parses_real_shaped_price_list_response() -> None:
    price_list_item = json.dumps(
        {
            "terms": {
                "OnDemand": {
                    "ABC123.JRTCKXETXF": {
                        "priceDimensions": {
                            "ABC123.JRTCKXETXF.6YS6EN2CT7": {
                                "pricePerUnit": {"USD": "0.0230000000"}
                            }
                        }
                    }
                }
            }
        }
    )
    mock_pricing = MagicMock()
    mock_pricing.get_products.return_value = {"PriceList": [price_list_item]}
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = fetch_s3_price_per_gb("us-east-1", _credentials())

    assert result is not None
    assert result.price_per_gb_month == 0.023
    assert result.region == "us-east-1"


def test_fetch_s3_price_per_gb_returns_none_on_api_error() -> None:
    mock_pricing = MagicMock()
    mock_pricing.get_products.side_effect = RuntimeError("access denied")
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = fetch_s3_price_per_gb("us-east-1", _credentials())

    assert result is None
