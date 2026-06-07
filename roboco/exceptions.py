"""
RoboCo Custom Exceptions

Structured exception hierarchy for the AI Agents Company system.
All exceptions include context for debugging and logging.
"""

import re
from typing import Any, ClassVar
from uuid import UUID


class RobocoError(Exception):
    """
    Base exception for all RoboCo errors.

    All exceptions include:
    - message: Human-readable error description
    - code: Machine-readable error code
    - details: Additional context for debugging
    """

    def __init__(
        self,
        message: str,
        code: str = "ROBOCO_ERROR",
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


# =============================================================================
# RESOURCE ERRORS
# =============================================================================


class NotFoundError(RobocoError):
    """Resource not found."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str | UUID,
        details: dict[str, Any] | None = None,
    ):
        self.resource_type = resource_type
        self.resource_id = str(resource_id)
        super().__init__(
            message=f"{resource_type} not found: {resource_id}",
            code="NOT_FOUND",
            details={
                "resource_type": resource_type,
                "resource_id": self.resource_id,
                **(details or {}),
            },
        )


# =============================================================================
# VALIDATION ERRORS
# =============================================================================


class ValidationError(RobocoError):
    """Input validation failed."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details={
                "field": field,
                "value": str(value) if value is not None else None,
                **(details or {}),
            },
        )


class InvalidStateError(RobocoError):
    """Operation not allowed in current state."""

    def __init__(
        self,
        current_state: str,
        operation: str,
        allowed_states: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ):
        allowed = (
            f" (allowed from: {', '.join(allowed_states)})" if allowed_states else ""
        )
        super().__init__(
            message=f"Cannot {operation} in state '{current_state}'{allowed}",
            code="INVALID_STATE",
            details={
                "current_state": current_state,
                "operation": operation,
                "allowed_states": allowed_states,
                **(details or {}),
            },
        )


# =============================================================================
# PERMISSION ERRORS
# =============================================================================


class PermissionDeniedError(RobocoError):
    """Agent does not have permission for this action."""

    def __init__(
        self,
        action: str,
        resource: str | None = None,
        agent_id: str | UUID | None = None,
        details: dict[str, Any] | None = None,
    ):
        resource_str = f" on {resource}" if resource else ""
        super().__init__(
            message=f"Permission denied: {action}{resource_str}",
            code="PERMISSION_DENIED",
            details={
                "action": action,
                "resource": resource,
                "agent_id": str(agent_id) if agent_id else None,
                **(details or {}),
            },
        )


class AuthenticationError(RobocoError):
    """Authentication failed."""

    def __init__(
        self,
        message: str = "Authentication required",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="AUTHENTICATION_REQUIRED",
            details=details,
        )


# =============================================================================
# TASK ERRORS
# =============================================================================


class TaskError(RobocoError):
    """Base class for task-related errors."""

    def __init__(
        self,
        message: str,
        task_id: str | UUID | None = None,
        code: str = "TASK_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            details={
                "task_id": str(task_id) if task_id else None,
                **(details or {}),
            },
        )


class TaskLifecycleError(TaskError):
    """Invalid task state transition."""

    # Procedural hints for the common "I skipped a step" footguns. Keyed by
    # (current_status, target_status); value is the tool-call sequence the
    # agent needs to run to actually reach the target. Weak models read
    # "valid transitions: [...]" and then guess — giving them the tool
    # calls explicitly saves the guess cycle.
    _TRANSITION_HINTS: ClassVar[dict[tuple[str, str], str]] = {
        ("claimed", "awaiting_documentation"): (
            "QA pass skipped the in_progress step. "
            "Call gateway i_will_work_on(task_id, plan='...') first, "
            "then pass(task_id, notes=...)."
        ),
        ("claimed", "awaiting_pm_review"): (
            "Call i_will_work_on(task_id, plan='...') first to "
            "claimed → in_progress, then the handoff verb for your role."
        ),
        ("claimed", "completed"): (
            "Call i_will_work_on(task_id, plan='...') before complete(task_id)."
        ),
        ("claimed", "needs_revision"): (
            "QA fail from claimed needs the start step first. "
            "Call i_will_work_on(task_id, plan='...') then "
            "fail(task_id, issues=[...])."
        ),
        ("pending", "in_progress"): (
            "Pending tasks must be claimed + planned first. "
            "Call gateway i_will_work_on(task_id, plan='...')."
        ),
        ("backlog", "in_progress"): (
            "Activate the task first: PATCH /api/tasks/{id} "
            "(status=pending), then i_will_work_on(task_id, plan='...')."
        ),
    }

    def __init__(
        self,
        current_status: str,
        target_status: str,
        **kwargs: Any,
    ):
        """
        Initialize a TaskLifecycleError.

        Args:
            current_status: Current task status
            target_status: Target status that was rejected
            **kwargs: Optional: task_id, message, valid_transitions, or other details
        """
        valid_transitions = kwargs.pop("valid_transitions", None)
        message = kwargs.pop("message", None)
        task_id = kwargs.pop("task_id", None)

        default_msg = f"Cannot transition from '{current_status}' to '{target_status}'"
        if valid_transitions:
            default_msg += f". Valid transitions: {valid_transitions}"
        hint = self._TRANSITION_HINTS.get((current_status, target_status))
        if hint:
            default_msg += f". {hint}"

        super().__init__(
            message=message or default_msg,
            task_id=task_id,
            code="TASK_LIFECYCLE_ERROR",
            details={
                "current_status": current_status,
                "target_status": target_status,
                "valid_transitions": valid_transitions,
                "hint": hint,
                **kwargs,
            },
        )
        self.current_status = current_status
        self.target_status = target_status


# =============================================================================
# AGENT ERRORS
# =============================================================================


class AgentError(RobocoError):
    """Base class for agent-related errors."""

    def __init__(
        self,
        message: str,
        agent_id: str | UUID | None = None,
        code: str = "AGENT_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            details={
                "agent_id": str(agent_id) if agent_id else None,
                **(details or {}),
            },
        )


# =============================================================================
# CHANNEL/MESSAGING ERRORS
# =============================================================================


class ChannelError(RobocoError):
    """Base class for channel-related errors."""

    def __init__(
        self,
        message: str,
        channel_id: str | UUID | None = None,
        code: str = "CHANNEL_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            details={
                "channel_id": str(channel_id) if channel_id else None,
                **(details or {}),
            },
        )


class ChannelAccessDeniedError(ChannelError):
    """Agent does not have access to channel."""

    def __init__(
        self,
        channel_id: str | UUID,
        agent_id: str | UUID,
        access_type: str = "read",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"No {access_type} access to channel",
            channel_id=channel_id,
            code="CHANNEL_ACCESS_DENIED",
            details={
                "agent_id": str(agent_id),
                "access_type": access_type,
                **(details or {}),
            },
        )


class SessionClosedError(RobocoError):
    """Session is closed."""

    def __init__(
        self,
        session_id: str | UUID,
        reason: str = "Session has been closed",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=reason,
            code="SESSION_CLOSED",
            details={
                "session_id": str(session_id),
                **(details or {}),
            },
        )


# =============================================================================
# NOTIFICATION ERRORS
# =============================================================================


class NotificationError(RobocoError):
    """Base class for notification errors."""

    def __init__(
        self,
        message: str,
        code: str = "NOTIFICATION_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, details=details)


# =============================================================================
# SERVICE ERRORS
# =============================================================================


class ServiceError(RobocoError):
    """External service error."""

    def __init__(
        self,
        service: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"{service} error: {message}",
            code="SERVICE_ERROR",
            details={
                "service": service,
                **(details or {}),
            },
        )


class DatabaseError(ServiceError):
    """Database operation failed."""

    def __init__(
        self,
        message: str,
        operation: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            service="database",
            message=message,
            details={
                "operation": operation,
                **(details or {}),
            },
        )


# =============================================================================
# GIT ERRORS
# =============================================================================


class GitError(ServiceError):
    """Base exception for git operation errors."""

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            service="git",
            message=message,
            details=details,
        )


def _scrub_git_secrets(text: str) -> str:
    """Redact credentials a git command may echo into stderr.

    Push/fetch run with the PAT injected via a URL or an ``http.extraheader``
    Basic header; never surface those verbatim in an error message or log.
    """
    if not text:
        return text
    text = re.sub(r"(://)[^/@\s]+@", r"\1***@", text)
    text = re.sub(r"(?i)(authorization:\s*basic\s+)\S+", r"\1***", text)
    text = re.sub(r"(?i)(extraheader=\S*?basic\s+)\S+", r"\1***", text)
    text = re.sub(
        r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b", "***", text
    )
    return text


class GitCommandError(GitError):
    """Git command execution failed."""

    def __init__(self, command: str, stderr: str) -> None:
        scrubbed = _scrub_git_secrets(stderr or "")
        # Surface a short, secret-free tail of git's own stderr in the message so
        # the real reason (403, non-fast-forward, ...) is visible to callers that
        # only render ``.message`` instead of swallowing it as "Command failed".
        tail = " ".join(scrubbed.split())[-300:]
        message = f"Command failed: {command}"
        if tail:
            message = f"{message} — {tail}"
        super().__init__(
            message=message,
            details={"command": command, "stderr": scrubbed},
        )
        self.command = command
        self.stderr = scrubbed


class GitTimeoutError(GitError):
    """Git command timed out."""

    def __init__(self, command: str, timeout: int) -> None:
        super().__init__(
            message=f"Command timed out after {timeout}s",
            details={"command": command, "timeout": timeout},
        )
        self.command = command
        self.timeout = timeout
