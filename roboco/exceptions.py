"""
RoboCo Custom Exceptions

Structured exception hierarchy for the AI Agents Company system.
All exceptions include context for debugging and logging.
"""

from typing import Any
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


class AlreadyExistsError(RobocoError):
    """Resource already exists."""

    def __init__(
        self,
        resource_type: str,
        identifier: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"{resource_type} already exists: {identifier}",
            code="ALREADY_EXISTS",
            details={
                "resource_type": resource_type,
                "identifier": identifier,
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

        super().__init__(
            message=message or default_msg,
            task_id=task_id,
            code="TASK_LIFECYCLE_ERROR",
            details={
                "current_status": current_status,
                "target_status": target_status,
                "valid_transitions": valid_transitions,
                **kwargs,
            },
        )
        self.current_status = current_status
        self.target_status = target_status


class TaskBlockedError(TaskError):
    """Task is blocked by dependencies."""

    def __init__(
        self,
        task_id: str | UUID,
        blocking_task_ids: list[str | UUID],
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"Task is blocked by {len(blocking_task_ids)} task(s)",
            task_id=task_id,
            code="TASK_BLOCKED",
            details={
                "blocking_task_ids": [str(tid) for tid in blocking_task_ids],
                **(details or {}),
            },
        )


class TaskClaimError(TaskError):
    """Cannot claim task."""

    def __init__(
        self,
        task_id: str | UUID,
        reason: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"Cannot claim task: {reason}",
            task_id=task_id,
            code="TASK_CLAIM_ERROR",
            details={
                "reason": reason,
                **(details or {}),
            },
        )


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


class AgentNotAvailableError(AgentError):
    """Agent is not available."""

    def __init__(
        self,
        agent_id: str | UUID,
        status: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"Agent is not available (status: {status})",
            agent_id=agent_id,
            code="AGENT_NOT_AVAILABLE",
            details={
                "status": status,
                **(details or {}),
            },
        )


class AgentBusyError(AgentError):
    """Agent is busy with another task."""

    def __init__(
        self,
        agent_id: str | UUID,
        current_task_id: str | UUID,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message="Agent is currently working on another task",
            agent_id=agent_id,
            code="AGENT_BUSY",
            details={
                "current_task_id": str(current_task_id),
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


class NotificationPermissionError(NotificationError):
    """Agent cannot send notifications."""

    def __init__(
        self,
        agent_id: str | UUID,
        agent_role: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=f"Agent with role '{agent_role}' cannot send notifications",
            code="NOTIFICATION_PERMISSION_DENIED",
            details={
                "agent_id": str(agent_id),
                "agent_role": agent_role,
                **(details or {}),
            },
        )


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


class LLMError(ServiceError):
    """LLM service error."""

    def __init__(
        self,
        message: str,
        model: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            service="llm",
            message=message,
            details={
                "model": model,
                **(details or {}),
            },
        )


class RAGError(ServiceError):
    """RAG service error."""

    def __init__(
        self,
        message: str,
        operation: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            service="rag",
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


class GitCommandError(GitError):
    """Git command execution failed."""

    def __init__(self, command: str, stderr: str) -> None:
        super().__init__(
            message=f"Command failed: {command}",
            details={"command": command, "stderr": stderr},
        )
        self.command = command
        self.stderr = stderr


class GitTimeoutError(GitError):
    """Git command timed out."""

    def __init__(self, command: str, timeout: int) -> None:
        super().__init__(
            message=f"Command timed out after {timeout}s",
            details={"command": command, "timeout": timeout},
        )
        self.command = command
        self.timeout = timeout
