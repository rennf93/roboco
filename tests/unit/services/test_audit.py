"""AuditService coverage — log methods all best-effort, never raise."""

from __future__ import annotations

from uuid import uuid4

import pytest
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
async def test_log_task_action_denial(svc: AuditService) -> None:
    await svc.log_task_action_denial(
        agent_id=uuid4(),
        agent_role="developer",
        task_id=uuid4(),
        action="claim",
        reason="wrong team",
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


@pytest.mark.asyncio
async def test_log_task_event_basic(svc: AuditService) -> None:
    await svc.log_task_event(
        event_type="task.created",
        task_id=uuid4(),
        agent_id=uuid4(),
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


# ---------------------------------------------------------------------------
# _resolve_actor_role_from_db — Task 7 of the gateway introspection plan.
# Pre-fix, denial-log calls trusted the caller-supplied agent_role string.
# The 2026-05-08 trace caught actor=main-pm with agent_role=cell_pm because
# the supplied role was the verb's expected role, not the actor's actual
# role. Fix: read agents.role at write time, fall back to the supplied
# string only when the DB lookup fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_actor_role_returns_none_for_none(svc: AuditService) -> None:
    assert await svc._resolve_actor_role_from_db(None) is None


@pytest.mark.asyncio
async def test_resolve_actor_role_returns_none_for_invalid_uuid(
    svc: AuditService,
) -> None:
    """A slug or malformed UUID coerces to None and short-circuits."""
    assert await svc._resolve_actor_role_from_db("be-dev-1") is None


@pytest.mark.asyncio
async def test_resolve_actor_role_returns_none_when_db_unavailable(
    svc: AuditService,
) -> None:
    """No DB session factory configured -> best-effort returns None."""
    # `get_session_factory()` will raise without a configured engine.
    # The helper catches the error and returns None so audit writes
    # still proceed with the caller-supplied role as fallback.
    result = await svc._resolve_actor_role_from_db(uuid4())
    assert result is None
