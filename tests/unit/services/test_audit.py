"""AuditService coverage — log methods all best-effort, never raise."""

from __future__ import annotations

from uuid import uuid4

import pytest
from roboco.models.audit import (
    PermissionDenialContext,
    StateTransitionDenialContext,
)
from roboco.services.audit import (
    AuditService,
    _AuditEvent,
    _coerce_uuid,
    get_audit_service,
)


@pytest.fixture
def svc() -> AuditService:
    """SingletonService — bypass init for unit tests."""
    return get_audit_service()


# ---------------------------------------------------------------------------
# _coerce_uuid
# ---------------------------------------------------------------------------


def test_coerce_uuid_returns_none_for_none() -> None:
    assert _coerce_uuid(None) is None


def test_coerce_uuid_passthrough_uuid() -> None:
    u = uuid4()
    assert _coerce_uuid(u) == u


def test_coerce_uuid_parses_string() -> None:
    u = uuid4()
    assert _coerce_uuid(str(u)) == u


def test_coerce_uuid_returns_none_for_invalid() -> None:
    assert _coerce_uuid("not-a-uuid") is None
    assert _coerce_uuid("be-dev-1") is None


# ---------------------------------------------------------------------------
# Log methods — best-effort; verify they don't raise even with no DB.
# Note: get_audit_service returns a singleton, so log methods will try to
# connect to a real DB. We test that they don't crash when called.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_permission_denial_does_not_raise(svc: AuditService) -> None:
    await svc.log_permission_denial(
        PermissionDenialContext(
            agent_id=uuid4(),
            action="create_task",
            resource="task",
            reason="not allowed",
        )
    )


@pytest.mark.asyncio
async def test_log_channel_access_denial(svc: AuditService) -> None:
    await svc.log_channel_access_denial(
        agent_id=str(uuid4()),
        channel_slug="backend-cell",
        access_type="write",
        reason="not member",
    )


@pytest.mark.asyncio
async def test_log_task_action_denial(svc: AuditService) -> None:
    await svc.log_task_action_denial(
        agent_id=uuid4(),
        agent_role="developer",
        task_id=uuid4(),
        action="claim",
        reason="wrong team",
    )


@pytest.mark.asyncio
async def test_log_state_transition_denial(svc: AuditService) -> None:
    await svc.log_state_transition_denial(
        StateTransitionDenialContext(
            agent_id=uuid4(),
            agent_role="qa",
            task_id=uuid4(),
            current_status="pending",
            target_status="completed",
            reason="invalid transition",
        )
    )


@pytest.mark.asyncio
async def test_log_notification_denial(svc: AuditService) -> None:
    await svc.log_notification_denial(
        agent_id=str(uuid4()),
        agent_role="developer",
        notification_type="blocker",
        reason="dev cannot notify qa directly",
    )


@pytest.mark.asyncio
async def test_log_security_event(svc: AuditService) -> None:
    from roboco.models.audit import AuditEventType

    await svc.log_security_event(
        event_type=AuditEventType.PERMISSION_DENIED,
        agent_id=str(uuid4()),
        description="bad token",
        details={"reason": "bad token"},
    )


@pytest.mark.asyncio
async def test_log_event_generic(svc: AuditService) -> None:
    await svc.log_event(
        event_type="task_created",
        agent_id=uuid4(),
        task_id=uuid4(),
        severity="info",
        details={"foo": "bar"},
    )


@pytest.mark.asyncio
async def test_log_agent_event(svc: AuditService) -> None:
    await svc.log_agent_event(
        event_type="agent_spawned",
        agent_slug="be-dev-1",
        details={"role": "developer"},
    )


# ---------------------------------------------------------------------------
# _AuditEvent dataclass
# ---------------------------------------------------------------------------


def test_audit_event_has_severity_default() -> None:
    e = _AuditEvent(event_type="t", agent_id=uuid4())
    assert e.severity == "info"  # Default per dataclass.


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_audit_service_returns_singleton() -> None:
    a = get_audit_service()
    b = get_audit_service()
    assert a is b
