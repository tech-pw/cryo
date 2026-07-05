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

from cryo.observability.log_redaction import SensitiveDataFilter

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False


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
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.processors.JSONRenderer(),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
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
    
    # Configure third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)
    
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
