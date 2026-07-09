"""Tests for cryo/observability/logging.py's third-party logger suppression.

Regression coverage: botocore/boto3/s3transfer weren't suppressed like urllib3/sqlalchemy.engine
already were, so DEBUG-level logging (the default for environment=dev) let botocore emit ~15-20
verbose lines per S3 request. Under concurrent S3-heavy work this turned log I/O into the actual
throughput bottleneck -- observed dropping effective S3 GET concurrency by roughly two orders of
magnitude in production.
"""

from __future__ import annotations

import logging

from cryo.observability.logging import configure_logging


def test_configure_logging_suppresses_botocore_family_even_at_debug() -> None:
    configure_logging(level="DEBUG", json_format=False, environment="dev")

    assert logging.getLogger("botocore").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("boto3").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("s3transfer").getEffectiveLevel() == logging.WARNING


def test_configure_logging_still_suppresses_existing_noisy_loggers() -> None:
    configure_logging(level="DEBUG", json_format=False, environment="dev")

    assert logging.getLogger("urllib3").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("sqlalchemy.engine").getEffectiveLevel() == logging.WARNING


def test_configure_logging_root_logger_still_respects_requested_level() -> None:
    configure_logging(level="DEBUG", json_format=False, environment="dev")

    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
