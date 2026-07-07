"""
Common Response Schemas

Standardized response wrappers used across API and MCP layers.
Ensures consistent response format for all endpoints.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Standard error detail structure."""

    code: str = Field(..., description="Error code (e.g., NOT_FOUND, ACCESS_DENIED)")
    message: str = Field(..., description="Human-readable error message")
    details: dict[str, Any] | None = Field(
        default=None, description="Additional error context"
    )


class ApiResponse[T](BaseModel):
    """
    Standard API response wrapper.

    All API endpoints should return this structure for consistency.
    MCP tools can use this directly or convert to their format.
    """

    status: str = Field(..., description="Response status (success, error, etc.)")
    data: T | None = Field(default=None, description="Response payload")
    error: ErrorDetail | None = Field(default=None, description="Error details if any")
    guidance: str | None = Field(
        default=None, description="Actionable next step guidance"
    )
    next_step: str | None = Field(
        default=None, description="Workflow hint (e.g., PLAN, EXECUTE)"
    )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def success_response(
    data: Any,
    guidance: str | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    """Create a standard success response dict."""
    response: dict[str, Any] = {"status": "success", "data": data}
    if guidance:
        response["guidance"] = guidance
    if next_step:
        response["next_step"] = next_step
    return response


def error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    """
    Create a standard error response dict.

    Format matches existing middleware/exception handlers:
    {"error": {"code": "...", "message": "...", "details": {...}, "hint": "..."}}

    Args:
        code: Error code (e.g., NOT_FOUND)
        message: Human-readable error message
        details: Optional additional error context
        hint: Optional RAG search suggestion for finding solutions
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    if hint:
        error["hint"] = hint
    return {"error": error}


def list_response(
    items: list[Any],
    total: int,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Create a standard list response dict."""
    return {
        "items": items,
        "total": total,
        "has_more": offset + len(items) < total,
        "offset": offset,
        "limit": limit,
    }


# =============================================================================
# ERROR CODES
# =============================================================================


class ErrorCode:
    """Standard error codes used across API and MCP."""

    # General errors
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    INVALID_INPUT = "INVALID_INPUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    API_ERROR = "API_ERROR"

    # Auth/Permission errors
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FORBIDDEN = "FORBIDDEN"

    # Task-specific errors
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_ALREADY_CLAIMED = "TASK_ALREADY_CLAIMED"
    TASK_WRONG_STATUS = "TASK_WRONG_STATUS"
    TASK_NOT_OWNED = "TASK_NOT_OWNED"
    PLAN_REQUIRED = "PLAN_REQUIRED"
    SELF_REVIEW_FORBIDDEN = "SELF_REVIEW_FORBIDDEN"

    # Message errors
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    NO_WRITE_ACCESS = "NO_WRITE_ACCESS"
    NO_SESSION_FOR_TASK = "NO_SESSION_FOR_TASK"

    # Notification errors
    NOTIFICATION_NOT_FOUND = "NOTIFICATION_NOT_FOUND"
    CANNOT_NOTIFY_SELF = "CANNOT_NOTIFY_SELF"

    # Journal errors
    INVALID_ENTRY_TYPE = "INVALID_ENTRY_TYPE"
    JOURNAL_NOT_FOUND = "JOURNAL_NOT_FOUND"

    # KB/Optimal errors
    SEARCH_FAILED = "SEARCH_FAILED"
    RAG_FAILED = "RAG_FAILED"
    INDEX_FAILED = "INDEX_FAILED"
