"""Live-timeline integration test for the board-review CEO gate (real DB).

The unit suite for this gate mocks both board reviewers as already-idle, so it
never drives the spawn -> active -> exit -> idle -> next-tick timeline — exactly
the boundary that matters and the failure mode that has burned us. This test
drives that real timeline against a real Postgres test DB:

  1. Seed a board/coordination task (pending, team=board) plus the system, CEO,
     Product Owner and Head of Marketing agents the handoff resolves against.
  2. Dispatch both board reviewers through the real ``_handle_board_assigned_task``
     path; the spawn stub registers each reviewer ACTIVE in ``_instances`` exactly
     as the real container spawn leaves it.
  3. While EITHER reviewer is active, assert ``board_review_complete`` is False —
     the flag is unset and no CEO notification exists.
  4. Mark both reviewers exited/idle, then run the next dispatch tick.
  5. Assert ``board_review_complete`` flips True in the DB and the formal CEO
     approval notification row is created.

The orchestrator's ``get_db_context()`` (and the NotificationService it opens)
are pointed at the same test database so the gate's real DB writes land where
the test reads them. Only Docker container spawning is stubbed — the gate and
handoff logic run unmodified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio
from roboco.db import base as db_base
from roboco.db.tables import AgentTable, NotificationTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import NotificationType, TaskNature, TaskStatus, TaskType
from roboco.models.runtime import AgentInstance, OrchestratorAgentState
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.seeds.initial_data import AGENT_UUIDS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PO_SLUG = "product-owner"
_HOM_SLUG = "head-marketing"
_PO_UUID = AGENT_UUIDS[_PO_SLUG]
_HOM_UUID = AGENT_UUIDS[_HOM_SLUG]
_CEO_UUID = AGENT_UUIDS["ceo"]
_SYSTEM_UUID = AGENT_UUIDS["system"]


def _agent(uuid: str, slug: str, role: AgentRole, team: Team | None) -> AgentTable:
    return AgentTable(
        id=UUID(uuid),
        name=slug,
        slug=slug,
        role=role,
        team=team,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def board_gate_setup(
    db_session: AsyncSession, _test_database_url: str
) -> AsyncIterator[dict]:
    """Seed agents + a board/coordination task and point the global DB holder
    at the test database so the orchestrator's own session writes land here."""
    db_session.add_all(
        [
            _agent(_SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
            _agent(_CEO_UUID, "ceo", AgentRole.CEO, None),
            _agent(_PO_UUID, _PO_SLUG, AgentRole.PRODUCT_OWNER, Team.BOARD),
            _agent(_HOM_UUID, _HOM_SLUG, AgentRole.HEAD_MARKETING, Team.BOARD),
        ]
    )
    await db_session.flush()

    # A board/coordination task: project_id NULL (git-exempt), team=board,
    # pending, assigned to one board reviewer (the review still fans out to both).
    task = TaskTable(
        id=UUID("00000000-0000-0000-00bd-000000000001"),
        title="Strategic feature to shape",
        description="A board-level task the PO + HoM must review before the CEO.",
        acceptance_criteria=["Board records requirements."],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=None,
        created_by=UUID(_SYSTEM_UUID),
        team=Team.BOARD,
        assigned_to=UUID(_PO_UUID),
    )
    db_session.add(task)
    await db_session.commit()

    # Point the global DB holder (used by get_db_context inside the handoff) at
    # the SAME test database, so the gate's real writes land where we read them.
    saved_engine = db_base._DbHolder.engine
    saved_factory = db_base._DbHolder.session_factory
    saved_loop = db_base._DbHolder.loop
    handoff_engine = create_async_engine(_test_database_url, future=True)
    db_base._DbHolder.engine = handoff_engine
    db_base._DbHolder.session_factory = async_sessionmaker(
        bind=handoff_engine, class_=AsyncSession, expire_on_commit=False
    )
    # Clear the loop stamp so the per-loop rebind guard claims the injected
    # engine for this test's loop instead of discarding it as foreign.
    db_base._DbHolder.loop = None

    # The dispatcher receives tasks from the HTTP API, which serializes
    # assigned_to as the agent UUID; _resolve_agent_slug maps it back to a slug.
    task_dict = {
        "id": str(task.id),
        "status": "pending",
        "team": "board",
        "task_type": "code",
        "title": task.title,
        "description": task.description,
        "assigned_to": _PO_UUID,
    }

    try:
        yield {"task_id": str(task.id), "task_dict": task_dict, "db": db_session}
    finally:
        await handoff_engine.dispose()
        db_base._DbHolder.engine = saved_engine
        db_base._DbHolder.session_factory = saved_factory
        db_base._DbHolder.loop = saved_loop


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._board_dispatched = set()
    orch._board_review_ceo_notified = set()
    # The board dispatch path consults the respawn circuit breaker now.
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    return orch


async def _board_review_flag(url: str, task_id: str) -> bool:
    """Read tasks.board_review_complete straight from the test DB."""
    engine = create_async_engine(url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession)
        async with factory() as session:
            row = await session.get(TaskTable, UUID(task_id))
            assert row is not None
            return row.board_review_complete
    finally:
        await engine.dispose()


async def _ceo_approval_notifications(
    url: str, task_id: str
) -> list[NotificationTable]:
    """Fetch CEO approval notifications carrying this task as related_task_id."""
    engine = create_async_engine(url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession)
        async with factory() as session:
            result = await session.execute(
                select(NotificationTable).where(
                    NotificationTable.related_task_id == UUID(task_id),
                    NotificationTable.type == NotificationType.APPROVAL,
                )
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_board_review_gate_flips_only_after_both_reviewers_go_idle(
    board_gate_setup: dict, _test_database_url: str
) -> None:
    orch = _make_orch()
    task_id = board_gate_setup["task_id"]
    task_dict = board_gate_setup["task_dict"]

    async def _spawn_active(**kwargs: object) -> None:
        # Mirror the real spawn's terminal effect: the agent instance is left
        # ACTIVE in _instances (the exact state the gate reads via
        # _is_agent_active). No Docker — only the state edge that matters.
        slug = str(kwargs["agent_id"])
        orch._instances[slug] = AgentInstance(
            agent_id=slug, state=OrchestratorAgentState.ACTIVE
        )

    with (
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", side_effect=_spawn_active),
        patch.object(orch, "_build_board_prompt", return_value="review prompt"),
    ):
        # Tick 1: dispatch both reviewers; both come up ACTIVE.
        await orch._handle_board_assigned_task(task_dict, _PO_UUID)

        assert orch._is_agent_active(_PO_SLUG) is True
        assert orch._is_agent_active(_HOM_SLUG) is True
        assert (_PO_SLUG, task_id) in orch._board_dispatched
        assert (_HOM_SLUG, task_id) in orch._board_dispatched

        # While both are active, the review is NOT complete: the gate stays
        # closed, the DB flag is unset, and no CEO notification exists.
        assert orch._board_review_complete(task_id) is False
        assert await _board_review_flag(_test_database_url, task_id) is False
        assert await _ceo_approval_notifications(_test_database_url, task_id) == []
        assert task_id not in orch._board_review_ceo_notified

        # One reviewer exits/idles; the other is still running -> still closed.
        orch._instances[_PO_SLUG].state = OrchestratorAgentState.IDLE
        assert orch._board_review_complete(task_id) is False

        # Both reviewers have now exited and gone idle.
        orch._instances[_HOM_SLUG].state = OrchestratorAgentState.IDLE

        # Tick 2 (next dispatch tick): the handoff fires. Reviewers are already
        # dispatched, so no respawn — only the gate + CEO handoff run.
        await orch._handle_board_assigned_task(task_dict, _PO_UUID)

    # The gate is now open: the flag is persisted and the CEO has a formal,
    # ack-required approval notification carrying the task.
    assert orch._board_review_complete(task_id) is True
    assert task_id in orch._board_review_ceo_notified
    assert await _board_review_flag(_test_database_url, task_id) is True

    notes = await _ceo_approval_notifications(_test_database_url, task_id)
    assert len(notes) == 1
    note = notes[0]
    assert note.from_agent == UUID(_SYSTEM_UUID)
    assert UUID(_CEO_UUID) in note.to_agents
    # Subjects are human-readable now: task title + #id8, never the raw UUID.
    assert f"#{str(task_id)[:8]}" in note.subject
