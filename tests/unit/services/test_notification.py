"""NotificationService coverage — mock the DB context.

The service uses `get_db_context()` internally rather than taking a session.
We patch it to a fake context that records inserted notification rows so we
can assert each `send_*` helper builds the right `CreateNotificationParams`
without spinning up a Postgres + Redis stack.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.models import NotificationPriority, NotificationType
from roboco.models.notification import CreateNotificationParams
from roboco.services.notification import (
    NotificationService,
    _resolve_agent_uuid,
)


class _FakeDb:
    """Stand-in for AsyncSession that records inserts and pretends to flush."""

    def __init__(self, *, agent_uuid: UUID | None = None) -> None:
        self.added: list = []
        self.committed = False
        self._agent_uuid = agent_uuid

    def add(self, obj) -> None:
        self.added.append(obj)
        # The notification row needs an `id` for delivery_service.deliver().
        obj.id = uuid4()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def execute(self, *_args, **_kwargs):
        # Two paths use this: agent slug→UUID resolution and the
        # notification_delivery service's own DB queries. We return a
        # MagicMock that supports `scalar_one_or_none()` returning either
        # an agent (with .id) or None depending on the configured agent_uuid.
        result = MagicMock()
        if self._agent_uuid:
            agent = MagicMock()
            agent.id = self._agent_uuid
            agent.slug = "test-agent"
            result.scalar_one_or_none.return_value = agent
        else:
            result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    async def scalar(self, *_args, **_kwargs):
        # _create_notification's purpose-dedup lookup runs db.scalar(); model
        # "no existing duplicate" so creation proceeds.
        return None


@asynccontextmanager
async def _fake_ctx(db: _FakeDb):
    yield db


@pytest.fixture
def svc() -> NotificationService:
    return NotificationService()


@pytest.mark.asyncio
async def test_resolve_agent_uuid_returns_none_for_blank() -> None:
    db = _FakeDb()
    assert await _resolve_agent_uuid(db, None) is None
    assert await _resolve_agent_uuid(db, "") is None


@pytest.mark.asyncio
async def test_resolve_agent_uuid_passes_through_uuid() -> None:
    aid = uuid4()
    db = _FakeDb()
    assert await _resolve_agent_uuid(db, aid) == aid


@pytest.mark.asyncio
async def test_resolve_agent_uuid_parses_uuid_string() -> None:
    aid = uuid4()
    db = _FakeDb()
    assert await _resolve_agent_uuid(db, str(aid)) == aid


@pytest.mark.asyncio
async def test_resolve_agent_uuid_resolves_slug() -> None:
    expected = uuid4()
    db = _FakeDb(agent_uuid=expected)
    resolved = await _resolve_agent_uuid(db, "be-dev-1")
    assert resolved == expected


@pytest.mark.asyncio
async def test_resolve_agent_uuid_returns_none_for_unknown_slug() -> None:
    db = _FakeDb(agent_uuid=None)
    assert await _resolve_agent_uuid(db, "ghost") is None


class _PatchDbContext:
    """Patch get_db_context + notification_delivery in one block."""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        delivery_mock = MagicMock()
        delivery_mock.deliver = AsyncMock(return_value=None)
        self._patches = [
            patch(
                "roboco.services.notification.get_db_context",
                lambda: _fake_ctx(db),
            ),
            patch(
                "roboco.services.notification_delivery.get_notification_delivery_service",
                lambda _db: delivery_mock,
            ),
        ]

    def __enter__(self) -> None:
        for p in self._patches:
            p.start()

    def __exit__(self, *_args) -> None:
        for p in self._patches:
            p.stop()


def _patch_db_context(db: _FakeDb) -> _PatchDbContext:
    return _PatchDbContext(db)


@pytest.mark.asyncio
async def test_send_blocker_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_blocker_notification(
            task_id="t1",
            blocker_reason="reason",
            from_agent="system",
            to_pm="cell-pm",
        )
    assert any("Task t1" in row.subject for row in db.added)


@pytest.mark.asyncio
async def test_send_qa_ready_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_qa_ready_notification(
            task_id="t1", from_agent="be-dev-1", to_qa="be-qa"
        )
    assert any("ready for QA" in row.subject for row in db.added)


@pytest.mark.asyncio
async def test_send_docs_ready_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_docs_ready_notification(
            task_id="t1", from_agent="be-qa", to_documenter="be-doc"
        )
    assert any("needs documentation" in row.subject for row in db.added)


@pytest.mark.asyncio
async def test_send_handoff_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_handoff_notification(
            task_id="t1",
            handoff_id="h1",
            from_agent="be-pm",
            to_documenter="be-doc",
        )
    assert any("Handoff required" in row.subject for row in db.added)


@pytest.mark.asyncio
async def test_send_qa_failed_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_qa_failed_notification(
            task_id="t1", qa_notes="fix this", to_developer="be-dev-1"
        )
    assert any("QA Failed" in row.subject for row in db.added)


@pytest.mark.asyncio
async def test_send_a2a_notification(svc: NotificationService) -> None:
    """priority=URGENT writes the row + the [URGENT] cosmetic prefix.

    Pre-P3-Task-9 this used `urgent: True`; the contract is now a
    tristate `priority` so HIGH can survive end-to-end. See
    tests/integration/test_a2a_priority_tristate.py for the full
    HIGH/NORMAL coverage.
    """
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_a2a_notification(
            task_id="t1",
            a2a_context={
                "from_agent": "be-dev-1",
                "to_agent": "fe-dev-1",
                "skill": "react",
                "message": "hi",
                "priority": NotificationPriority.URGENT,
            },
        )
    # Urgent prefix appears in subject.
    assert any("URGENT" in row.subject for row in db.added)
    assert any(row.priority == NotificationPriority.URGENT for row in db.added)


@pytest.mark.asyncio
async def test_send_board_review_complete_notification(
    svc: NotificationService,
) -> None:
    """Board-review-complete handoff is an APPROVAL notification to the CEO
    carrying the task_id (cluster C5 / finding #2)."""
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_board_review_complete_notification(task_id="t1")
    assert any("Board review complete" in row.subject for row in db.added)
    assert any(row.type == NotificationType.APPROVAL for row in db.added)
    assert any(row.priority == NotificationPriority.HIGH for row in db.added)
    assert any(row.related_task_id == "t1" for row in db.added)


@pytest.mark.asyncio
async def test_send_ack_notification(svc: NotificationService) -> None:
    aid = uuid4()
    db = _FakeDb(agent_uuid=aid)
    with _patch_db_context(db):
        await svc.send_ack_notification(
            from_agent="main-pm",
            to_agent="ceo",
            body="please review",
            priority=NotificationPriority.HIGH,
        )
    assert db.added  # Notification row recorded.


@pytest.mark.asyncio
async def test_create_notification_skips_when_from_agent_unresolvable(
    svc: NotificationService,
) -> None:
    """Unresolvable from_agent → log and skip, no row inserted."""
    db = _FakeDb(agent_uuid=None)  # All slug lookups return None.
    with _patch_db_context(db):
        await svc._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BLOCKER_ESCALATION,
                priority=NotificationPriority.HIGH,
                from_agent="ghost-agent",
                to_agents=["be-pm"],
                subject="x",
                body="y",
            )
        )
    assert db.added == []


@pytest.mark.asyncio
async def test_create_notification_skips_when_no_resolvable_recipients(
    svc: NotificationService,
) -> None:
    """All recipients unresolvable → skip with warn."""
    aid = uuid4()

    # First call resolves from_agent, subsequent slug lookups still hit our
    # fake — which always returns the same agent. Use a fake that returns the
    # configured agent only on the first lookup.
    class _OnceFake(_FakeDb):
        def __init__(self) -> None:
            super().__init__(agent_uuid=aid)
            self._calls = 0

        async def execute(self, *_args, **_kwargs):
            self._calls += 1
            result = MagicMock()
            if self._calls == 1:
                # from_agent resolution succeeds
                agent = MagicMock()
                agent.id = aid
                result.scalar_one_or_none.return_value = agent
            else:
                result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
            return result

    db = _OnceFake()
    with _patch_db_context(db):
        await svc._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BLOCKER_ESCALATION,
                priority=NotificationPriority.HIGH,
                from_agent="be-pm",
                to_agents=["ghost1", "ghost2"],
                subject="x",
                body="y",
            )
        )
    assert db.added == []
