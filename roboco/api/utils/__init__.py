"""
API Utilities

Common utilities for API routes:
- HTTP error factories
- Response helpers
- Dependency utilities
"""

from roboco.api.utils.errors import (
    conflict,
    forbidden,
    handle_service_error,
    not_found,
    service_error_handler,
    service_unavailable,
    unauthorized,
    validation_error,
)
from roboco.api.utils.resources import (
    get_by_field_or_404,
    get_or_404,
    require_membership,
    require_ownership,
    require_recipient,
)

__all__ = [
    "conflict",
    "forbidden",
    "get_by_field_or_404",
    "get_or_404",
    "handle_service_error",
    "not_found",
    "require_membership",
    "require_ownership",
    "require_recipient",
    "service_error_handler",
    "service_unavailable",
    "unauthorized",
    "validation_error",
]
