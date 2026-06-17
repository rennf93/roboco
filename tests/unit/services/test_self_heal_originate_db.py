"""Self-heal task origination against a real Postgres DB.

The loop opens a fix task only when ``self_heal_originate_enabled``, dedupes one
open task per regression fingerprint, honors the per-cycle and rolling open-task
caps, and creates the task PENDING + UNASSIGNED + ``confirmed_by_human=False`` so
it sits inert until the CEO Approve-&-Starts it. Crucially it NEVER calls
start / approve / merge / deploy — asserted here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, Team
from roboco.services.notification import NotificationService
from roboco.services.self_heal_engine import SelfHealEngine
from roboco.services.task import TaskService, get_task_service
from roboco.services.telemetry import TelemetrySample
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SLUG = "roboco"
ONE = 1


class _FakeSource:
    def __init__(self, samples: list[TelemetrySample]) -> None:
        self._samples = samples

    async def fetch(self) -> list[TelemetrySample]:
        return list(self._samples)


def _breach(signal: str) -> TelemetrySample:
    return TelemetrySample(
        signal_name=signal,
        value=1.0,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint=SLUG,
        observed_at="2026-06-17T00:00:00Z",
        raw_ref="https://github.com/x/roboco/actions/runs/1",
        detail=f"{signal} concluded 'failure'",
    )


async def _seed_project(session: AsyncSession, slug: str = SLUG) -> None:
    """Seed the system agent (FK target for created_by) + RoboCo's own project.

    The system agent has a FIXED foundation uuid (origination hardcodes it as
    created_by). Another test in the full suite may have already committed it
    into the session-scoped DB, so get-or-create by id — a plain insert collides
    on pk_agents (green in isolation, red in the full CI run).
    """
    if await session.get(AgentTable, SYSTEM_UUID) is None:
        session.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="System",
                slug=f"system-{slug}",
                role=AgentRole.SYSTEM,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="system",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=slug,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_max_open_tasks", 5)
    monkeypatch.setattr(cfg, "self_heal_max_per_cycle", 5)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)
    # Keep notification a no-op (its own session/IO is out of scope here).
    monkeypatch.setattr(NotificationService, "send_ack_notification", AsyncMock())


@pytest.mark.asyncio
async def test_disabled_originate_creates_no_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch, self_heal_originate_enabled=False)
    engine = SelfHealEngine(db_session, source=_FakeSource([_breach("ci:roboco")]))
    obs = await engine.run_cycle()
    assert len(obs) == ONE  # detected + notified
    assert await get_task_service(db_session).list_open_self_heal_tasks() == []


@pytest.mark.asyncio
async def test_originate_creates_pending_unassigned_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch)
    engine = SelfHealEngine(db_session, source=_FakeSource([_breach("ci:roboco")]))
    await engine.run_cycle()

    open_tasks = await get_task_service(db_session).list_open_self_heal_tasks()
    assert len(open_tasks) == ONE
    task = open_tasks[0]
    assert task.status == TaskStatus.PENDING
    assert task.assigned_to is None  # inert until the CEO Approve-&-Starts it
    assert task.confirmed_by_human is False
    assert task.team == Team.MAIN_PM
    assert task.source == "self_heal"
    assert task.acceptance_criteria  # non-empty (AC-guardrail)
    assert "self_heal_fp=" in (task.quick_context or "")


@pytest.mark.asyncio
async def test_dedupe_no_second_task_same_fingerprint(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch)
    src = _FakeSource([_breach("ci:roboco")])
    await SelfHealEngine(db_session, source=src).run_cycle()
    await SelfHealEngine(db_session, source=src).run_cycle()  # same fingerprint
    open_tasks = await get_task_service(db_session).list_open_self_heal_tasks()
    assert len(open_tasks) == ONE


@pytest.mark.asyncio
async def test_per_cycle_cap_limits_origination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch, self_heal_max_per_cycle=1)
    # Two distinct regressions in one cycle, cap = 1 → only one task opens.
    src = _FakeSource([_breach("ci:roboco:a"), _breach("ci:roboco:b")])
    await SelfHealEngine(db_session, source=src).run_cycle()
    assert len(await get_task_service(db_session).list_open_self_heal_tasks()) == ONE


@pytest.mark.asyncio
async def test_open_task_cap_blocks_further_origination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch, self_heal_max_open_tasks=1)
    await SelfHealEngine(
        db_session, source=_FakeSource([_breach("ci:roboco:a")])
    ).run_cycle()
    # A different regression next cycle, but one task is already open → blocked.
    await SelfHealEngine(
        db_session, source=_FakeSource([_breach("ci:roboco:b")])
    ).run_cycle()
    assert len(await get_task_service(db_session).list_open_self_heal_tasks()) == ONE


@pytest.mark.asyncio
async def test_unresolved_project_is_notify_only(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch)
    ghost = TelemetrySample(
        signal_name="ci:ghost",
        value=1.0,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint="not-a-registered-project",
        observed_at="",
        raw_ref="",
    )
    # No crash, no task — the regression can't be tied to a repo.
    await SelfHealEngine(db_session, source=_FakeSource([ghost])).run_cycle()
    assert await get_task_service(db_session).list_open_self_heal_tasks() == []


@pytest.mark.asyncio
async def test_loop_never_starts_or_approves(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_project(db_session)
    _enable(monkeypatch)
    approve = AsyncMock()
    ceo_approve = AsyncMock()
    monkeypatch.setattr(TaskService, "approve_and_start", approve)
    monkeypatch.setattr(TaskService, "ceo_approve", ceo_approve)
    await SelfHealEngine(
        db_session, source=_FakeSource([_breach("ci:roboco")])
    ).run_cycle()

    approve.assert_not_awaited()
    ceo_approve.assert_not_awaited()
    open_tasks = await get_task_service(db_session).list_open_self_heal_tasks()
    assert len(open_tasks) == ONE
    assert open_tasks[0].status == TaskStatus.PENDING  # never advanced by the loop


@pytest.mark.asyncio
async def test_ceo_approve_and_start_flips_the_confirmation_gate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opened task is unconfirmed (held out of dispatch); the CEO's
    approve_and_start flips confirmed_by_human=True so it can then dispatch."""
    await _seed_project(db_session)
    # approve_and_start reassigns to the main-pm agent — get-or-create by slug
    # (the full suite may already have committed a "main-pm" agent).
    existing_pm = (
        await db_session.execute(select(AgentTable).where(AgentTable.slug == "main-pm"))
    ).scalar_one_or_none()
    if existing_pm is None:
        db_session.add(
            AgentTable(
                id=uuid4(),
                name="Main PM",
                slug="main-pm",
                role=AgentRole.MAIN_PM,
                team=Team.MAIN_PM,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="pm",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db_session.flush()
    _enable(monkeypatch)
    # approve_and_start emits a stream event; stub it out (no bus in the test).
    monkeypatch.setattr(TaskService, "_emit_task_event", AsyncMock())

    await SelfHealEngine(
        db_session, source=_FakeSource([_breach("ci:roboco")])
    ).run_cycle()
    task = (await get_task_service(db_session).list_open_self_heal_tasks())[0]
    assert task.confirmed_by_human is False  # inert: held out of dispatch

    started = await TaskService(db_session).approve_and_start(UUID(str(task.id)))
    assert started is not None
    assert started.confirmed_by_human is True  # gate flipped → now dispatchable
    assert started.status == TaskStatus.PENDING  # reassignment, not a transition
