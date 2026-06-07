"""roboco.exceptions coverage — every concrete exception's __init__ path.

Exercises every `__init__` branch (with and without optional `details`)
and verifies `to_dict` shape on the base class. Pure construction tests —
no DB, no fixtures.
"""

from __future__ import annotations

from uuid import uuid4

from roboco.exceptions import (
    AgentError,
    AuthenticationError,
    ChannelAccessDeniedError,
    ChannelError,
    DatabaseError,
    GitCommandError,
    GitError,
    GitTimeoutError,
    InvalidStateError,
    NotFoundError,
    NotificationError,
    PermissionDeniedError,
    RobocoError,
    ServiceError,
    SessionClosedError,
    TaskError,
    TaskLifecycleError,
    ValidationError,
)

_TIMEOUT_SECONDS = 30


def test_roboco_error_to_dict_shape() -> None:
    err = RobocoError("boom", code="X", details={"a": 1})
    out = err.to_dict()
    assert out["error"]["code"] == "X"
    assert out["error"]["message"] == "boom"
    assert out["error"]["details"] == {"a": 1}


def test_roboco_error_default_details_is_empty_dict() -> None:
    err = RobocoError("no details")
    assert err.details == {}


def test_not_found_error_with_details() -> None:
    rid = uuid4()
    err = NotFoundError("Task", rid, details={"hint": "check spelling"})
    assert err.code == "NOT_FOUND"
    assert err.details["hint"] == "check spelling"
    assert err.details["resource_id"] == str(rid)


def test_validation_error_with_field_and_value() -> None:
    err = ValidationError("Invalid", field="email", value="not-an-email")
    assert err.code == "VALIDATION_ERROR"
    assert err.details["field"] == "email"
    assert err.details["value"] == "not-an-email"


def test_validation_error_with_none_value() -> None:
    err = ValidationError("Required", field="name")
    assert err.details["value"] is None


def test_validation_error_with_extra_details() -> None:
    err = ValidationError("Bad", field="x", value=1, details={"extra": "yes"})
    assert err.details["extra"] == "yes"


def test_invalid_state_error_with_allowed_states() -> None:
    err = InvalidStateError(
        current_state="closed", operation="reopen", allowed_states=["open"]
    )
    assert err.code == "INVALID_STATE"
    assert "allowed from: open" in err.message


def test_invalid_state_error_no_allowed_states() -> None:
    err = InvalidStateError("done", "modify")
    assert err.code == "INVALID_STATE"
    assert "allowed from" not in err.message


def test_invalid_state_error_with_extra_details() -> None:
    err = InvalidStateError("a", "b", details={"x": 1})
    assert err.details["x"] == 1


def test_permission_denied_error_with_resource() -> None:
    err = PermissionDeniedError(
        action="delete", resource="task/123", agent_id="be-dev-1"
    )
    assert "on task/123" in err.message
    assert err.details["agent_id"] == "be-dev-1"


def test_permission_denied_error_no_resource() -> None:
    err = PermissionDeniedError(action="cancel")
    assert "Permission denied: cancel" in err.message
    assert err.details["agent_id"] is None


def test_permission_denied_error_with_uuid_agent() -> None:
    aid = uuid4()
    err = PermissionDeniedError(action="x", agent_id=aid)
    assert err.details["agent_id"] == str(aid)


def test_permission_denied_error_with_extra_details() -> None:
    err = PermissionDeniedError(action="x", details={"extra": "yes"})
    assert err.details["extra"] == "yes"


def test_authentication_error_default_message() -> None:
    err = AuthenticationError()
    assert err.code == "AUTHENTICATION_REQUIRED"
    assert err.message == "Authentication required"


def test_authentication_error_with_custom_message() -> None:
    err = AuthenticationError("Token expired")
    assert err.message == "Token expired"


def test_task_error_with_uuid_task_id() -> None:
    tid = uuid4()
    err = TaskError("oops", task_id=tid)
    assert err.details["task_id"] == str(tid)


def test_task_error_no_task_id() -> None:
    err = TaskError("oops")
    assert err.details["task_id"] is None


def test_task_error_with_extra_details() -> None:
    err = TaskError("x", task_id="t1", details={"extra": "y"})
    assert err.details["extra"] == "y"


def test_task_lifecycle_error_default_message() -> None:
    err = TaskLifecycleError("pending", "claimed")
    assert err.code == "TASK_LIFECYCLE_ERROR"
    assert "pending" in err.message
    assert "claimed" in err.message


def test_task_lifecycle_error_with_valid_transitions() -> None:
    err = TaskLifecycleError(
        "pending", "claimed", valid_transitions=["pending->in_progress"]
    )
    assert "pending->in_progress" in err.message


def test_task_lifecycle_error_uses_hint_when_known() -> None:
    """The (claimed, awaiting_documentation) pair has a transition hint."""
    err = TaskLifecycleError("claimed", "awaiting_documentation")
    assert "i_will_work_on" in err.message


def test_task_lifecycle_error_with_explicit_message() -> None:
    err = TaskLifecycleError("a", "b", message="Custom")
    assert err.message == "Custom"


def test_task_lifecycle_error_attaches_state_attrs() -> None:
    err = TaskLifecycleError("pending", "claimed")
    assert err.current_status == "pending"
    assert err.target_status == "claimed"


def test_task_lifecycle_error_extra_kwargs() -> None:
    """Extra kwargs end up in details."""
    err = TaskLifecycleError("a", "b", extra="present")
    assert err.details["extra"] == "present"


def test_agent_error_with_uuid() -> None:
    aid = uuid4()
    err = AgentError("oops", agent_id=aid)
    assert err.details["agent_id"] == str(aid)


def test_agent_error_no_agent_id() -> None:
    err = AgentError("oops")
    assert err.details["agent_id"] is None


def test_agent_error_with_extra_details() -> None:
    err = AgentError("x", agent_id="a1", details={"k": "v"})
    assert err.details["k"] == "v"


def test_channel_error_with_uuid() -> None:
    cid = uuid4()
    err = ChannelError("nope", channel_id=cid)
    assert err.details["channel_id"] == str(cid)


def test_channel_error_no_channel_id() -> None:
    err = ChannelError("nope")
    assert err.details["channel_id"] is None


def test_channel_error_with_extra_details() -> None:
    err = ChannelError("x", channel_id="c1", details={"k": "v"})
    assert err.details["k"] == "v"


def test_channel_access_denied_error() -> None:
    err = ChannelAccessDeniedError(channel_id="c1", agent_id="a1", access_type="write")
    assert err.code == "CHANNEL_ACCESS_DENIED"
    assert err.details["access_type"] == "write"


def test_channel_access_denied_error_default_access_type() -> None:
    err = ChannelAccessDeniedError(channel_id="c1", agent_id="a1")
    assert err.details["access_type"] == "read"


def test_channel_access_denied_error_with_details() -> None:
    err = ChannelAccessDeniedError(channel_id="c1", agent_id="a1", details={"k": "v"})
    assert err.details["k"] == "v"


def test_session_closed_error_default_reason() -> None:
    sid = uuid4()
    err = SessionClosedError(session_id=sid)
    assert err.code == "SESSION_CLOSED"
    assert err.details["session_id"] == str(sid)


def test_session_closed_error_custom_reason() -> None:
    err = SessionClosedError(session_id="s1", reason="Timeout")
    assert err.message == "Timeout"


def test_session_closed_error_with_details() -> None:
    err = SessionClosedError(session_id="s1", details={"k": "v"})
    assert err.details["k"] == "v"


def test_notification_error_basic() -> None:
    err = NotificationError("nope")
    assert err.code == "NOTIFICATION_ERROR"
    assert err.message == "nope"


def test_notification_error_with_custom_code() -> None:
    err = NotificationError("nope", code="CUSTOM")
    assert err.code == "CUSTOM"


def test_service_error_basic() -> None:
    err = ServiceError(service="redis", message="connection refused")
    assert err.code == "SERVICE_ERROR"
    assert "redis" in err.message


def test_service_error_with_extra_details() -> None:
    err = ServiceError(service="r", message="x", details={"k": "v"})
    assert err.details["k"] == "v"


def test_database_error_basic() -> None:
    err = DatabaseError("query failed", operation="select")
    assert err.code == "SERVICE_ERROR"
    assert err.details["operation"] == "select"


def test_database_error_with_details() -> None:
    err = DatabaseError("x", operation="select", details={"k": "v"})
    assert err.details["k"] == "v"


def test_git_error_basic() -> None:
    err = GitError("conflict")
    assert err.code == "SERVICE_ERROR"


def test_git_error_with_details() -> None:
    err = GitError("x", details={"k": "v"})
    assert err.details["k"] == "v"


def test_git_command_error() -> None:
    err = GitCommandError(command="git push", stderr="rejected")
    assert err.command == "git push"
    assert err.stderr == "rejected"
    assert err.details["command"] == "git push"


def test_git_command_error_surfaces_stderr_in_message() -> None:
    err = GitCommandError(
        command="push -u origin feature/x",
        stderr="remote: Permission to owner/repo.git denied.\nfatal: unable to access",
    )
    # The real reason must reach callers that only render ``.message``.
    assert "Command failed: push -u origin feature/x" in err.message
    assert "Permission to owner/repo.git denied" in err.message


def test_git_command_error_scrubs_credentials() -> None:
    leaky = (
        "fatal: unable to access "
        "'https://x-access-token:ghp_AbC123456789012345678901234567890@github.com/o/r.git'"
    )
    err = GitCommandError(command="push", stderr=leaky)
    # The injected PAT must never survive into the message, stderr, or details.
    assert "ghp_" not in err.message
    assert "ghp_" not in err.stderr
    assert "ghp_" not in err.details["stderr"]
    assert "https://***@github.com" in err.stderr


def test_git_timeout_error() -> None:
    err = GitTimeoutError(command="git fetch", timeout=_TIMEOUT_SECONDS)
    assert err.command == "git fetch"
    assert err.timeout == _TIMEOUT_SECONDS
    assert err.details["timeout"] == _TIMEOUT_SECONDS
