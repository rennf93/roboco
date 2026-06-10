"""A2A urgency must round-trip as the full Priority tristate.

Pre-Phase-3 the A2A path collapsed `priority` at the request boundary
into a boolean `urgent`, then mapped that boolean back to
NotificationPriority.URGENT / NORMAL — so the middle tier
NotificationPriority.HIGH was unreachable through the A2A code path.

After Task 9 the tristate (NORMAL/HIGH/URGENT) survives end-to-end:
the NotificationTable row inserted by `send_a2a_notification` carries
the priority the caller asked for.

These tests pin that contract on both layers:

  * unit — `NotificationService.send_a2a_notification` accepts a
    `Priority` in its `a2a_context` and writes that exact enum value
    to the inserted row.
  * service — `A2AService.create_a2a_notification` parses
    `metadata["priority"]` (preferring the tristate value) and passes
    a `Priority` through to NotificationService.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation.policy.communications import Priority
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.a2a import A2AMessage, SendMessageRequest, TextPart
from roboco.models.base import NotificationPriority, TaskNature, TaskStatus, TaskType
from roboco.services.a2a import A2AService
from roboco.services.notification import NotificationService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Foundation sanity — Priority IS the tristate, not a boolean.
# ---------------------------------------------------------------------------


def test_priority_has_three_distinct_values() -> None:
    """If this fails Phase 3 Task 1 regressed."""
    assert Priority.NORMAL != Priority.HIGH
    assert Priority.HIGH != Priority.URGENT
    assert Priority.NORMAL != Priority.URGENT
    # Same enum object as the DB column type.
    assert Priority is NotificationPriority


# ---------------------------------------------------------------------------
# NotificationService.send_a2a_notification — DB row carries tristate
# ---------------------------------------------------------------------------


class _FakeDb:
    """Records inserted notification rows so we can assert on .priority."""

    def __init__(self, *, agent_uuid: UUID) -> None:
        self.added: list = []
        self._agent_uuid = agent_uuid

    def add(self, obj) -> None:
        self.added.append(obj)
        obj.id = uuid4()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        result = MagicMock()
        agent = MagicMock()
        agent.id = self._agent_uuid
        agent.slug = "test-agent"
        result.scalar_one_or_none.return_value = agent
        result.scalars.return_value.all.return_value = []
        return result

    async def scalar(self, *_args, **_kwargs):
        # _create_notification's purpose-dedup lookup — no existing duplicate.
        return None


@asynccontextmanager
async def _fake_ctx(db: _FakeDb):
    yield db


class _PatchDbContext:
    def __init__(self, db: _FakeDb) -> None:
        delivery_mock = MagicMock()
        delivery_mock.deliver = AsyncMock(return_value=None)
        self._patches = [
            patch(
                "roboco.services.notification.get_db_context",
                lambda: _fake_ctx(db),
            ),
            patch(
                "roboco.services.notification_delivery."
                "get_notification_delivery_service",
                lambda _db: delivery_mock,
            ),
        ]

    def __enter__(self) -> None:
        for p in self._patches:
            p.start()

    def __exit__(self, *_args) -> None:
        for p in self._patches:
            p.stop()


@pytest.mark.asyncio
async def test_send_a2a_notification_high_priority_writes_high() -> None:
    """priority=Priority.HIGH ends up at NotificationTable.priority=HIGH.

    This is the heart of Task 9: pre-fix, HIGH was unreachable via this
    method because the contract was `urgent: bool`.
    """
    svc = NotificationService()
    db = _FakeDb(agent_uuid=uuid4())
    with _PatchDbContext(db):
        await svc.send_a2a_notification(
            task_id="t1",
            a2a_context={
                "from_agent": "be-dev-1",
                "to_agent": "fe-dev-1",
                "skill": "react",
                "message": "hi",
                "priority": Priority.HIGH,
            },
        )
    assert db.added, "Notification row should have been inserted."
    row = db.added[0]
    assert row.priority == NotificationPriority.HIGH
    # HIGH gets NO cosmetic [URGENT] prefix — that label is urgent-only.
    assert "[URGENT]" not in row.subject
    assert "[URGENT]" not in row.body


@pytest.mark.asyncio
async def test_send_a2a_notification_normal_priority_writes_normal() -> None:
    svc = NotificationService()
    db = _FakeDb(agent_uuid=uuid4())
    with _PatchDbContext(db):
        await svc.send_a2a_notification(
            task_id="t1",
            a2a_context={
                "from_agent": "be-dev-1",
                "to_agent": "fe-dev-1",
                "skill": "react",
                "message": "hi",
                "priority": Priority.NORMAL,
            },
        )
    row = db.added[0]
    assert row.priority == NotificationPriority.NORMAL
    assert "[URGENT]" not in row.subject


@pytest.mark.asyncio
async def test_send_a2a_notification_urgent_preserves_prefix() -> None:
    """URGENT still gets the cosmetic [URGENT] prefix in subject + body."""
    svc = NotificationService()
    db = _FakeDb(agent_uuid=uuid4())
    with _PatchDbContext(db):
        await svc.send_a2a_notification(
            task_id="t1",
            a2a_context={
                "from_agent": "be-dev-1",
                "to_agent": "fe-dev-1",
                "skill": "react",
                "message": "hi",
                "priority": Priority.URGENT,
            },
        )
    row = db.added[0]
    assert row.priority == NotificationPriority.URGENT
    assert "[URGENT]" in row.subject
    assert "[URGENT]" in row.body


# ---------------------------------------------------------------------------
# A2AService.create_a2a_notification — request parses + forwards Priority
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def a2a_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    # Same seeded slugs the existing a2a tests use — A2A policy
    # rejects unknown roles.
    dev = AgentTable(
        id=uuid4(),
        name="Dev",
        slug="be-dev-1",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug="be-qa",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([dev, qa])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="A-Proj",
        slug=f"a-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=dev.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": A2AService(db_session),
        "task_id": task.id,
    }


@pytest.mark.asyncio
async def test_create_a2a_notification_metadata_priority_high_propagates(
    a2a_setup: dict,
) -> None:
    """metadata={"priority": "high"} → NotificationService gets Priority.HIGH."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={
            "from_agent": "be-dev-1",
            "target_agent": "be-qa",
            "priority": "high",
        },
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        await svc.create_a2a_notification(req)
    mock_ns.send_a2a_notification.assert_awaited_once()
    kwargs = mock_ns.send_a2a_notification.await_args.kwargs
    a2a_context = kwargs["a2a_context"]
    assert a2a_context["priority"] == Priority.HIGH


@pytest.mark.asyncio
async def test_create_a2a_notification_metadata_priority_urgent_propagates(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={
            "from_agent": "be-dev-1",
            "target_agent": "be-qa",
            "priority": "urgent",
        },
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        await svc.create_a2a_notification(req)
    a2a_context = mock_ns.send_a2a_notification.await_args.kwargs["a2a_context"]
    assert a2a_context["priority"] == Priority.URGENT


@pytest.mark.asyncio
async def test_create_a2a_notification_default_priority_is_normal(
    a2a_setup: dict,
) -> None:
    """No metadata.priority + no configuration.urgent → Priority.NORMAL."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={"from_agent": "be-dev-1", "target_agent": "be-qa"},
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        await svc.create_a2a_notification(req)
    a2a_context = mock_ns.send_a2a_notification.await_args.kwargs["a2a_context"]
    assert a2a_context["priority"] == Priority.NORMAL


@pytest.mark.asyncio
async def test_create_a2a_notification_legacy_urgent_bool_maps_to_urgent(
    a2a_setup: dict,
) -> None:
    """Backcompat: callers that still send `urgent: True` (e.g. agent_sdk
    fallback at server.py:258) are honored as Priority.URGENT until that
    path is refactored in a later task. priority key wins if both set."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={
            "from_agent": "be-dev-1",
            "target_agent": "be-qa",
            "urgent": True,
        },
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        await svc.create_a2a_notification(req)
    a2a_context = mock_ns.send_a2a_notification.await_args.kwargs["a2a_context"]
    assert a2a_context["priority"] == Priority.URGENT


@pytest.mark.asyncio
async def test_create_a2a_notification_unknown_priority_falls_back_to_normal(
    a2a_setup: dict,
) -> None:
    """Garbage priority string → NORMAL, not a crash."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={
            "from_agent": "be-dev-1",
            "target_agent": "be-qa",
            "priority": "nuclear",
        },
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        await svc.create_a2a_notification(req)
    a2a_context = mock_ns.send_a2a_notification.await_args.kwargs["a2a_context"]
    assert a2a_context["priority"] == Priority.NORMAL
