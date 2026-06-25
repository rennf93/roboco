"""Release-manager engine: propose a CEO-gated release, held + deduped, never publish.

Mirrors the self-heal engine tests. The engine proposes only past the threshold +
green gate, holds the proposal for the CEO (confirmed_by_human=False, owned by the
Secretary, never dispatched), dedupes to one open proposal, and NEVER publishes /
approves — asserted here against a real Postgres DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.notification import NotificationService
from roboco.services.release_manager_engine import ReleaseManagerEngine
from roboco.services.release_readiness import (
    Gap,
    ReleaseReadinessReport,
    report_from_dict,
    report_to_dict,
)
from roboco.services.task import RELEASE_MANAGER_SOURCE, TaskService, get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
SLUG = "roboco"
ONE = 1
MIN_COMMITS = 8
_VERSION = "0.13.0"


def _report(
    *,
    bump: str = "minor",
    gate: str = "green",
    kind: str = "feat",
    n_commits: int = 10,
    gaps: list[Gap] | None = None,
) -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind=bump,
        change_summary=[f"{kind}: change {i}" for i in range(n_commits)],
        drafted_changelog=f"## [{_VERSION}] - 2026-06-25\n\n### Added\n- stuff (#1)\n",
        version_bump_plan=["pyproject.toml"],
        gaps=gaps or [],
        migration_notes=[],
        gate_state=gate,
    )


def _assessor(report: ReleaseReadinessReport | None):
    async def _a() -> ReleaseReadinessReport | None:
        return report

    return _a


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY, None),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
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
            )
    await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
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
    monkeypatch.setattr(cfg, "release_manager_enabled", True)
    monkeypatch.setattr(cfg, "release_min_commits", MIN_COMMITS)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)
    monkeypatch.setattr(NotificationService, "send_ack_notification", AsyncMock())


def test_report_dict_round_trip() -> None:
    report = _report(gaps=[Gap("gate", "x"), Gap("changelog", "y")])
    assert report_from_dict(report_to_dict(report)) == report


@pytest.mark.asyncio
async def test_disabled_creates_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "release_manager_enabled", False)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_below_threshold_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    # Patch bump + few fix commits + no security → below the threshold.
    report = _report(bump="patch", kind="fix", n_commits=2)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(report))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_red_gate_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report(gate="red")))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_proposes_held_proposal_past_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    task = await engine.run_cycle()
    assert task is not None

    open_proposals = await get_task_service(db_session).list_open_release_proposals()
    assert len(open_proposals) == ONE
    proposal = open_proposals[0]
    assert proposal.status == TS.PENDING
    assert proposal.confirmed_by_human is False  # HELD for the CEO, not dispatched
    assert proposal.assigned_to == SECRETARY_UUID
    assert proposal.source == RELEASE_MANAGER_SOURCE
    assert "0.13.0" in proposal.title
    stored = markers.get_release_report(proposal)
    assert stored is not None
    assert report_from_dict(stored).proposed_version == "0.13.0"


@pytest.mark.asyncio
async def test_security_only_patch_still_proposes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    # One security fix (patch bump, below the commit floor) still warrants a release.
    report = _report(bump="patch", kind="security", n_commits=1)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(report))
    assert await engine.run_cycle() is not None


@pytest.mark.asyncio
async def test_dedupe_one_open_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    assert len(await get_task_service(db_session).list_open_release_proposals()) == ONE


@pytest.mark.asyncio
async def test_loop_never_publishes_or_approves(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    approve = AsyncMock()
    ceo_approve = AsyncMock()
    monkeypatch.setattr(TaskService, "approve_and_start", approve)
    monkeypatch.setattr(TaskService, "ceo_approve", ceo_approve)
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    approve.assert_not_awaited()
    ceo_approve.assert_not_awaited()
    proposals = await get_task_service(db_session).list_open_release_proposals()
    assert proposals[0].status == TS.PENDING  # never advanced by the loop


@pytest.mark.asyncio
async def test_none_assessment_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(None))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []
