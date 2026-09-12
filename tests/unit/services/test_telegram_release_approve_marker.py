"""Round-2 pr_gate finding F-08121d68: only the HTTP approve route stamped
``ceo_approved_at``, so a Telegram-approved release certified it null —
worse than the ``completed_at`` field it replaced. Proves
``TelegramInboundService._approve_release`` — via the REAL ``dispatch_approve``
/ ``ReleaseProposalService.approve()`` chokepoint, not a mocked stand-in — now
populates the marker exactly like the HTTP route does, since both surfaces
route through that one shared chokepoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, TaskNature, TaskStatus, TaskType
from roboco.models.base import Team as T
from roboco.services import telegram_inbound as ti
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_proposal import ReleaseProposalService
from roboco.services.release_readiness import ReleaseReadinessReport, report_to_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    import asyncio
    from uuid import UUID

_VERSION = "0.19.0"


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog=f"## [{_VERSION}]\n\n### Added\n- a thing\n",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


async def _seed_proposal(session: AsyncSession) -> TaskTable:
    system_uuid = _foundation.AGENTS["system"].uuid
    secretary_uuid = _foundation.AGENTS["secretary-1"].uuid
    ceo_uuid = _foundation.AGENTS["ceo"].uuid
    for uuid_, slug, role in (
        (system_uuid, "system", AgentRole.SYSTEM),
        (secretary_uuid, "secretary-1", AgentRole.SECRETARY),
        # _mark_audit's telegram audit row FKs agent_id to the CEO's agent
        # row (_CEO_UUID) -- must exist for that insert to satisfy the FK.
        (ceo_uuid, "ceo", AgentRole.CEO),
    ):
        if await session.get(AgentTable, uuid_) is None:
            session.add(
                AgentTable(
                    id=uuid_,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=T.BACKEND,
        created_by=system_uuid,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        project_id=project.id,
        created_by=system_uuid,
        assigned_to=secretary_uuid,
        team=T.MAIN_PM,
        source=RELEASE_MANAGER_SOURCE,
        confirmed_by_human=False,
        orchestration_markers={"release_report": report_to_dict(_report())},
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_telegram_approve_release_stamps_ceo_approved_at(
    db_session: AsyncSession,
) -> None:
    """The Telegram approve path dispatches the same background execute the
    HTTP route does; once it completes, ceo_approved_at must be populated —
    not just for an HTTP-dispatched approve."""
    task = await _seed_proposal(db_session)
    task_id = cast("UUID", task.id)
    await db_session.commit()

    published = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha="abc123",
        release_url=f"https://example.com/releases/v{_VERSION}",
        detail="ok",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=published)
    factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    real_dispatch = ti.dispatch_approve
    captured: dict[str, asyncio.Task[None]] = {}

    def _capturing_dispatch(tid: UUID, _factory: Any) -> asyncio.Task[None]:
        bg = real_dispatch(tid, factory)
        captured["task"] = bg
        return bg

    engine = ti.TelegramInboundEngine(db_session)
    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch.object(ti, "dispatch_approve", side_effect=_capturing_dispatch),
        patch.object(
            ReleaseProposalService,
            "_acquire_release_lock",
            AsyncMock(return_value="tok"),
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        ok, _text = await engine._approve_release(task, "aaaaaaaa", "", None)
        assert ok
        # Await the background execute WHILE the executor patch is still
        # active (the dispatched task runs the faked publish).
        bg = captured["task"]
        await bg

    fresh = factory()
    try:
        final = await fresh.get(TaskTable, task_id)
        assert final is not None
        assert final.status == TaskStatus.COMPLETED
        # The reviewer's finding: this used to stay null on a
        # Telegram-approved release since only the HTTP route stamped it.
        assert markers.get_release_approved_at(final) is not None
    finally:
        await fresh.close()
