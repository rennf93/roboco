"""
Logging Configuration

Structured logging setup using structlog with JSON output
for production and colored console output for development.
"""

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog

from roboco.config import settings

if TYPE_CHECKING:
    from structlog.types import Processor


def add_app_context(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Add application context to all log entries."""
    event_dict["app"] = "roboco"
    event_dict["version"] = settings.app_version
    event_dict["environment"] = settings.environment
    return event_dict


def setup_logging() -> None:
    """
    Configure structlog for the application.

    In development: colored console output
    In production: JSON output for log aggregation
    """
    # Shared processors
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        add_app_context,
    ]

    if settings.environment == "development":
        # Development: colored console output
        processors: list[Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # Production: JSON output
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    log_level = logging.DEBUG if settings.debug else logging.INFO

    # Root logger
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger.

    Args:
        name: Logger name, typically __name__

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


# =============================================================================
# LOGGING UTILITIES
# =============================================================================


class LogContext:
    """
    Context manager for adding temporary context to logs.

    Usage:
        with LogContext(task_id=task.id, agent_id=agent.id):
            logger.info("Processing task")
    """

    def __init__(self, **kwargs: Any):
        self.context = kwargs

    def __enter__(self) -> "LogContext":
        structlog.contextvars.bind_contextvars(**self.context)
        return self

    def __exit__(self, *args: Any) -> None:
        for key in self.context:
            structlog.contextvars.unbind_contextvars(key)


def log_operation(
    operation: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Create a structured log context for an operation.

    Usage:
        logger.info("Task created", **log_operation("create", "task", task.id))
    """
    context = {
        "operation": operation,
    }
    if resource_type:
        context["resource_type"] = resource_type
    if resource_id:
        context["resource_id"] = resource_id
    context.update(extra)
    return context
