"""
RoboCo - AI Agents Company

A virtual organization of 18 AI agents + 1 human CEO,
designed to operate as a complete software development workforce.
"""

__version__ = "0.1.0"

# Core exports
from roboco.config import settings
from roboco.logging import setup_logging, get_logger, LogContext
from roboco.exceptions import (
    RobocoError,
    NotFoundError,
    ValidationError,
    PermissionDeniedError,
    TaskError,
    TaskLifecycleError,
    AgentError,
    ChannelError,
)

__all__ = [
    "__version__",
    "settings",
    "setup_logging",
    "get_logger",
    "LogContext",
    "RobocoError",
    "NotFoundError",
    "ValidationError",
    "PermissionDeniedError",
    "TaskError",
    "TaskLifecycleError",
    "AgentError",
    "ChannelError",
]
