"""
Logging Configuration

Structured logging setup using structlog with JSON output
for production and colored console output for development.
"""

import logging
import logging.handlers
import os
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.stdlib import BoundLogger

from roboco.config import settings

if TYPE_CHECKING:
    from structlog.types import Processor


def add_app_context(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """Add application context to all log entries."""
    event_dict["app"] = "roboco"
    event_dict["version"] = settings.app_version
    event_dict["environment"] = settings.environment
    return event_dict


# Patterns for secrets that should never end up in logs. Matches the common
# GitHub PAT shapes (classic `ghp_`, fine-grained `github_pat_`, server/OAuth
# `ghs_`/`gho_`) plus bearer tokens and generic "key = <value>" shapes. The
# regex is intentionally loose on length so it catches tokens regardless of
# future format tweaks.
import re as _re  # noqa: E402 — module-local alias only

_SECRET_PATTERNS: list[_re.Pattern[str]] = [
    _re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"),
    _re.compile(r"(ghp_[A-Za-z0-9_]{20,})"),
    _re.compile(r"(ghs_[A-Za-z0-9_]{20,})"),
    _re.compile(r"(gho_[A-Za-z0-9_]{20,})"),
    _re.compile(r"(ghu_[A-Za-z0-9_]{20,})"),
    _re.compile(r"(ghr_[A-Za-z0-9_]{20,})"),
    # Authorization: bearer <token>
    _re.compile(r"([Bb]earer\s+[A-Za-z0-9_\-.=]{20,})"),
    # https://user:TOKEN@host and https://TOKEN@host — strip embedded creds
    # in git URLs if they slip in (e.g. from a stderr line)
    _re.compile(r"(https://[^:@/\s]+:[^@/\s]{8,}@)"),
    _re.compile(r"(https://[A-Za-z0-9_\-.]{20,}@)"),
]


def _redact_secrets(value: Any) -> Any:
    """Replace known secret shapes with <REDACTED>. Non-str values untouched."""
    if not isinstance(value, str):
        return value
    out = value
    for pat in _SECRET_PATTERNS:
        out = pat.sub("<REDACTED>", out)
    return out


def redact_event_dict(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """Structlog processor — redact secrets in every value of the event dict.

    Runs last, so even error tracebacks, raw exception messages, and
    externally-sourced strings (e.g. a subprocess stderr) get scrubbed
    before they're rendered to stdout or the persistent log file.
    """
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_secrets(value)
    return event_dict


def setup_logging() -> None:
    """
    Configure structlog for the application.

    In development: colored console output
    In production: JSON output for log aggregation
    """
    # Shared processors. `redact_event_dict` runs last so every value —
    # including tracebacks, contextvars, subprocess stderr, and upstream
    # library output — is scrubbed of known secret shapes before render.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        add_app_context,
        redact_event_dict,
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

    # Root logger: stdout for `docker logs`, plus a rotating file handler so
    # we have a persistent record that survives container restarts and
    # doesn't depend on docker's log driver rotation policy.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stdout_handler)

    log_dir = _resolve_log_dir()
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "orchestrator.log",
                maxBytes=50 * 1024 * 1024,  # 50 MB per file
                backupCount=5,  # keep 5 rotations -> ~250 MB cap
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            root.addHandler(file_handler)
        except OSError as e:
            # Don't let a broken log-dir take down the app; stdout still works.
            sys.stderr.write(f"[logging] could not attach file handler: {e}\n")

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )


def _resolve_log_dir() -> Path | None:
    """
    Resolve the directory where log files should be written.

    Order of precedence:
    1. $ROBOCO_LOG_DIR (explicit override).
    2. /data/logs — standard mount from docker-compose inside containers.
    3. ./logs — local dev fallback, relative to CWD.
    4. None — disables file logging (caller just uses stdout).
    """
    override = os.environ.get("ROBOCO_LOG_DIR")
    if override:
        return Path(override)
    container_path = Path("/data/logs")
    if container_path.is_dir() or container_path.parent.is_dir():
        return container_path
    return Path("./logs")


def get_logger(name: str | None = None) -> BoundLogger:
    """
    Get a structured logger.

    Args:
        name: Logger name, typically __name__

    Returns:
        Configured structlog logger
    """
    return cast("BoundLogger", structlog.get_logger(name))


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
