"""Structured logging configuration for Cryo.

Configures JSON-formatted logging with contextual fields for:
- Request tracing
- User attribution
- Performance tracking
- Error reporting
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from cryo.observability.correlation import correlation_fields
from cryo.observability.log_redaction import SensitiveDataFilter

try:
    import structlog

    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False


def quiet_third_party_loggers() -> None:
    """Entrypoints using ``logging.basicConfig`` instead of ``configure_logging`` call this."""
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # httpx logs every request URL at INFO; Slack incoming-webhook URLs are the credential
    # itself, and the root-logger SensitiveDataFilter never sees propagated child records.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)
    # botocore/boto3/s3transfer log every request's full signing details, headers, and retry
    # hooks at DEBUG -- ~15-20 lines per S3 call. Under concurrent S3-heavy work (orphan
    # scanning, bucket-logging checks, request-cost log ingestion) with DEBUG-level logging
    # (the default for environment=dev), that log I/O becomes the actual throughput bottleneck,
    # not the network calls themselves -- observed dropping concurrent S3 GET throughput by two
    # orders of magnitude in production. WARNING still surfaces real request failures.
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)


def configure_process_logging() -> None:
    """JSON logs in staging/prod, console logs in dev. Shared by every process entrypoint."""
    from cryo.config import get_settings

    settings = get_settings()
    configure_logging(
        level="DEBUG" if settings.environment == "dev" else "INFO",
        json_format=settings.environment in {"staging", "prod"},
        environment=settings.environment,
    )


def configure_logging(
    *,
    level: str = "INFO",
    json_format: bool = True,
    environment: str = "development",
) -> None:
    """Configure structured logging for Cryo.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, output JSON; if False, use console format
        environment: Deployment environment (development, staging, production)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    if HAS_STRUCTLOG and json_format:
        # Configure structlog for JSON output
        def _inject_correlation(
            logger: Any, method_name: str, event_dict: dict[str, Any]
        ) -> dict[str, Any]:
            event_dict.update(correlation_fields())
            return event_dict

        processors: list[Any] = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            _inject_correlation,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ]
        structlog.configure(
            processors=processors,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        # structlog.stdlib.LoggerFactory() routes every call through the standard-library
        # logging module -- JSONRenderer above only formats the final message string, it
        # doesn't emit it anywhere. Without a handler on the root logger here, every daemon
        # process (governor, worker, scheduler) silently drops every log line: no stdout
        # output, no stderr, nothing, since Python's logging.lastResort fallback only fires
        # for WARNING+ and this repo's DaemonRunner base class logs its own startup/tick
        # lines at INFO. Found live, 2026-07-22: a stuck governor pod produced zero log
        # output for 20+ minutes, including its own "Starting governor" banner and repeated
        # "Error in governor tick" exception logs -- there was no way to tell whether the
        # process was idle, hung, or crash-looping without exec-ing in and re-deriving state
        # by hand. `basicConfig` below only runs in the non-JSON branch, so it never covers
        # this path.
        root_logger = logging.getLogger()
        if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            root_logger.addHandler(handler)
    else:
        # Configure standard logging for console output
        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=log_level,
            stream=sys.stdout,
        )

    # Set root logger level
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    if not any(isinstance(f, SensitiveDataFilter) for f in root_logger.filters):
        root_logger.addFilter(SensitiveDataFilter())

    quiet_third_party_loggers()

    # Log configuration
    logger = get_logger(__name__)
    if HAS_STRUCTLOG:
        logger.info(
            "Logging configured",
            level=level,
            json_format=json_format,
            environment=environment,
            has_structlog=HAS_STRUCTLOG,
        )
    else:
        logger.info(
            "Logging configured (level=%s, json_format=%s, environment=%s, has_structlog=%s)",
            level,
            json_format,
            environment,
            HAS_STRUCTLOG,
        )


def get_logger(name: str) -> Any:
    """Get a logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance (structlog or stdlib logging)
    """
    if HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding contextual fields to logs.

    Usage:
        with LogContext(table_id=table.id, operation="optimize"):
            logger.info("Starting operation")
    """

    def __init__(self, **context: Any) -> None:
        self.context = context
        self.bound_logger = None

    def __enter__(self) -> Any:
        if HAS_STRUCTLOG:
            self.bound_logger = structlog.get_logger().bind(**self.context)
            return self.bound_logger
        return logging.getLogger()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


# Pre-configured logger instances for common modules
cryo_logger = get_logger("cryo")
api_logger = get_logger("cryo.api")
worker_logger = get_logger("cryo.worker")
scanner_logger = get_logger("cryo.scanner")
planner_logger = get_logger("cryo.planner")
governor_logger = get_logger("cryo.governor")
scheduler_logger = get_logger("cryo.scheduler")
