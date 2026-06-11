"""
RoboCo - AI Agents Company

A virtual organization of 18 AI agents + 1 human CEO,
designed to operate as a complete software development workforce.
"""

__version__ = "0.2.0"

# Core exports
from roboco.config import settings
from roboco.exceptions import (
    AgentError,
    ChannelError,
    NotFoundError,
    PermissionDeniedError,
    RobocoError,
    TaskError,
    TaskLifecycleError,
    ValidationError,
)
from roboco.logging import LogContext, get_logger, setup_logging

__all__ = [
    "AgentError",
    "ChannelError",
    "LogContext",
    "NotFoundError",
    "PermissionDeniedError",
    "RobocoError",
    "TaskError",
    "TaskLifecycleError",
    "ValidationError",
    "__version__",
    "get_logger",
    "settings",
    "setup_logging",
]
