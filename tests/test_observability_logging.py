"""Tests for cryo/observability/logging.py's third-party logger suppression.

Regression coverage: botocore/boto3/s3transfer weren't suppressed like urllib3/sqlalchemy.engine
already were, so DEBUG-level logging (the default for environment=dev) let botocore emit ~15-20
verbose lines per S3 request. Under concurrent S3-heavy work this turned log I/O into the actual
throughput bottleneck -- observed dropping effective S3 GET concurrency by roughly two orders of
magnitude in production.
"""

from __future__ import annotations

import io
import logging

from cryo.observability.logging import configure_logging


def test_configure_logging_json_format_actually_emits_to_stdout() -> None:
    """Regression, 2026-07-22: the structlog+json_format branch (used in staging/prod, see
    cli.py's `json_format=settings.environment in {"staging", "prod"}`) called
    structlog.configure(...) but never attached a logging.Handler to the root logger --
    JSONRenderer only formats the message string, it doesn't emit it anywhere. Every daemon
    process (governor, worker, scheduler) silently dropped every log line as a result: no
    stdout, no stderr, nothing below WARNING (and even WARNING/ERROR only escaped via
    Python's logging.lastResort fallback, which isn't guaranteed and wasn't observed live).
    A stuck governor pod produced zero log output for 20+ minutes -- including its own
    startup banner -- making it impossible to tell idle from hung from crash-looping
    without exec-ing into the pod and re-deriving state by hand."""
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    configure_logging(level="INFO", json_format=True, environment="prod")

    assert any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)

    stream = io.StringIO()
    probe_handler = logging.StreamHandler(stream)
    probe_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(probe_handler)
    try:
        logging.getLogger("cryo.governor").warning("Governor not available (missing deps)")
    finally:
        root_logger.removeHandler(probe_handler)

    assert stream.getvalue().strip() != ""


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
