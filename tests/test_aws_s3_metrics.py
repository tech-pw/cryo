from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from cryo.integrations.aws_s3_metrics import (
    AWSCredentials,
    fetch_bucket_storage_metrics,
    fetch_s3_price_per_gb,
    fetch_s3_request_prices,
    get_cached_request_price,
)
from cryo.integrations.aws_s3_metrics import test_aws_connection as check_aws_connection

_FALLBACK_PUT = 0.005
_FALLBACK_GET = 0.0004


def _price_list_item(group: str, price: str) -> str:
    return json.dumps(
        {
            "product": {"attributes": {"group": group}},
            "terms": {
                "OnDemand": {
                    "ABC.XYZ": {
                        "priceDimensions": {
                            "ABC.XYZ.1": {"pricePerUnit": {"USD": price}},
                        }
                    }
                }
            },
        }
    )


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


def _fetch_request_prices(region: str = "eu-west-1", *, use_cache: bool = False):
    return fetch_s3_request_prices(
        region,
        _credentials(),
        use_cache=use_cache,
        fallback_price_per_1k_put=_FALLBACK_PUT,
        fallback_price_per_1k_get=_FALLBACK_GET,
    )


def test_fetch_s3_request_prices_full_success_uses_live_values() -> None:
    mock_pricing = MagicMock()
    mock_pricing.get_products.return_value = {
        "PriceList": [
            _price_list_item("Tier1", "0.0055"),
            _price_list_item("Tier2", "0.00044"),
        ]
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = _fetch_request_prices()

    assert result.source == "aws_price_list_api"
    assert result.price_per_1k_put == 0.0055
    assert result.price_per_1k_get == 0.00044


def test_fetch_s3_request_prices_never_returns_none() -> None:
    """Contract: always a usable S3RequestPriceInfo, so every caller has exactly one fallback
    code path (the .source field) instead of a duplicate None-check in each caller."""
    mock_pricing = MagicMock()
    mock_pricing.get_products.side_effect = RuntimeError("access denied")
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = _fetch_request_prices()

    assert result is not None
    assert result.source == "fallback_default"


def test_fetch_s3_request_prices_falls_back_to_configured_defaults_on_error_not_zero() -> None:
    """Regression: a missing/undeliverable price must fall back to the caller's configured
    default rate, never to 0.0 -- zero would make that operation type look completely free
    rather than approximately priced (docs/s3-request-cost-tracking.md §6.4)."""
    mock_pricing = MagicMock()
    mock_pricing.get_products.side_effect = RuntimeError("access denied")
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = _fetch_request_prices()

    assert result.price_per_1k_put == _FALLBACK_PUT
    assert result.price_per_1k_get == _FALLBACK_GET
    assert result.price_per_1k_put != 0.0
    assert result.price_per_1k_get != 0.0


def test_fetch_s3_request_prices_empty_response_falls_back_and_warns(caplog) -> None:
    mock_pricing = MagicMock()
    mock_pricing.get_products.return_value = {"PriceList": []}
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        with caplog.at_level("WARNING"):
            result = _fetch_request_prices()

    assert result.source == "fallback_default"
    assert result.price_per_1k_put == _FALLBACK_PUT
    assert result.price_per_1k_get == _FALLBACK_GET
    assert any("falling back" in r.message.lower() for r in caplog.records)


def test_fetch_s3_request_prices_partial_response_uses_live_tier_and_fallback_for_missing() -> None:
    """Only the GET (Tier2) SKU comes back -- PUT must use the configured fallback, not 0.0,
    and .source must distinguish this from a fully-live fetch."""
    mock_pricing = MagicMock()
    mock_pricing.get_products.return_value = {"PriceList": [_price_list_item("Tier2", "0.00044")]}
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        result = _fetch_request_prices()

    assert result.source == "aws_price_list_api_partial"
    assert result.price_per_1k_get == 0.00044
    assert result.price_per_1k_put == _FALLBACK_PUT


def test_fetch_s3_request_prices_unmapped_region_falls_back() -> None:
    result = _fetch_request_prices(region="mars-central-1")

    assert result.source == "fallback_default"
    assert result.price_per_1k_put == _FALLBACK_PUT
    assert result.price_per_1k_get == _FALLBACK_GET


def test_fetch_s3_request_prices_caches_per_region_and_storage_class() -> None:
    mock_pricing = MagicMock()
    mock_pricing.get_products.return_value = {
        "PriceList": [
            _price_list_item("Tier1", "0.0055"),
            _price_list_item("Tier2", "0.00044"),
        ]
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_pricing

    with patch("boto3.Session", return_value=mock_session):
        first = fetch_s3_request_prices(
            "eu-west-2",
            _credentials(),
            use_cache=True,
            fallback_price_per_1k_put=_FALLBACK_PUT,
            fallback_price_per_1k_get=_FALLBACK_GET,
        )
        second = fetch_s3_request_prices(
            "eu-west-2",
            _credentials(),
            use_cache=True,
            fallback_price_per_1k_put=_FALLBACK_PUT,
            fallback_price_per_1k_get=_FALLBACK_GET,
        )

    assert first == second
    assert mock_pricing.get_products.call_count == 1
    assert get_cached_request_price("eu-west-2", "General Purpose") == first
