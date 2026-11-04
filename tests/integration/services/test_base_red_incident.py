"""BaseRedIncidentService — open after N reds, one owner, close on green, clear."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, BaseRedIncidentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.services.base_red_incident import get_base_red_incident_service
from roboco.services.ci_watch_engine import get_ci_watch_engine
from roboco.services.telemetry.source import TelemetrySample
from sqlalchemy import func, select

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def _pid(project: ProjectTable) -> UUID:
    return cast("UUID", project.id)


SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid


def _sample(slug: str, *, red: bool) -> TelemetrySample:
    return TelemetrySample(
        signal_name=f"ci_conclusion:{slug}",
        value=1.0 if red else 0.0,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint=slug,
        observed_at="2026-11-03T00:00:00Z",
        raw_ref=f"https://github.com/x/{slug}/actions/runs/1",
        detail=f"CI on {slug}@master concluded 'failure'",
    )


class _Source:
    def __init__(self) -> None:
        self.samples: list[TelemetrySample] = []

    async def fetch(self, _projects: list[object]) -> list[TelemetrySample]:
        return list(self.samples)


async def _agent(
    db: AsyncSession, agent_id: object, role: AgentRole, slug: str
) -> None:
    if await db.get(AgentTable, agent_id) is None:
        db.add(
            AgentTable(
                id=agent_id,
                name=slug,
                slug=f"{slug}-{uuid4().hex[:8]}",
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
        await db.flush()


@pytest.fixture(autouse=True)
async def _setup(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_max_per_cycle", 5)
    monkeypatch.setattr(settings, "ci_watch_max_open_tasks", 5)
    monkeypatch.setattr(settings, "base_red_consecutive_threshold", 2)
    await _agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


async def _project(db: AsyncSession, slug: str) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=f"https://github.com/x/{slug}.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        ci_watch_enabled=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _live_count(db: AsyncSession, project_id: object) -> int:
    stmt = (
        select(func.count())
        .select_from(BaseRedIncidentTable)
        .where(
            BaseRedIncidentTable.project_id == project_id,
            BaseRedIncidentTable.status == "open",
        )
    )
    return int((await db.execute(stmt)).scalar_one())


@pytest.mark.asyncio
async def test_opens_only_after_consecutive_reds_with_single_owner(
    db_session: AsyncSession,
) -> None:
    proj = await _project(db_session, "br-open")
    svc = get_base_red_incident_service(db_session)
    red = [_sample("br-open", red=True)]

    await svc.observe(red, [proj])
    assert await svc.get_open_incident(_pid(proj)) is None  # 1 red: not yet

    await svc.observe(red, [proj])
    incident = await svc.get_open_incident(_pid(proj))
    assert incident is not None
    assert incident.owner_task_id is not None
    assert incident.owner_agent == "main-pm"
    assert incident.base_branch
    assert incident.opened_at is not None
    assert await svc.is_owner_task(incident.owner_task_id) is True
    assert await svc.is_owner_task(uuid4()) is False

    # Further reds keep exactly one open incident with the same owner.
    await svc.observe(red, [proj])
    await svc.observe(red, [proj])
    again = await svc.get_open_incident(_pid(proj))
    assert again is not None
    assert again.id == incident.id
    assert again.owner_task_id == incident.owner_task_id
    assert await _live_count(db_session, _pid(proj)) == 1


@pytest.mark.asyncio
async def test_green_closes_open_incident(db_session: AsyncSession) -> None:
    proj = await _project(db_session, "br-close")
    svc = get_base_red_incident_service(db_session)
    red = [_sample("br-close", red=True)]
    await svc.observe(red, [proj])
    await svc.observe(red, [proj])
    incident = await svc.get_open_incident(_pid(proj))
    assert incident is not None

    await svc.observe([_sample("br-close", red=False)], [proj])

    assert await svc.get_open_incident(_pid(proj)) is None
    assert incident.owner_task_id is not None
    assert await svc.is_owner_task(incident.owner_task_id) is False
    row = await db_session.get(BaseRedIncidentTable, incident.id)
    assert row is not None
    assert row.status == "closed"
    assert row.closed_at is not None
    assert row.cleared_by == "system"


@pytest.mark.asyncio
async def test_green_before_threshold_resets_streak(db_session: AsyncSession) -> None:
    proj = await _project(db_session, "br-flap")
    svc = get_base_red_incident_service(db_session)
    await svc.observe([_sample("br-flap", red=True)], [proj])
    await svc.observe([_sample("br-flap", red=False)], [proj])
    await svc.observe([_sample("br-flap", red=True)], [proj])

    assert await svc.get_open_incident(_pid(proj)) is None  # streak restarted


@pytest.mark.asyncio
async def test_unknown_readings_neither_open_nor_close(
    db_session: AsyncSession,
) -> None:
    proj = await _project(db_session, "br-unknown")
    svc = get_base_red_incident_service(db_session)
    red = [_sample("br-unknown", red=True)]

    await svc.observe(red, [proj])
    await svc.observe([], [proj])  # pending / lookup error -> no sample
    await svc.observe([], [proj])
    assert await svc.get_open_incident(_pid(proj)) is None

    await svc.observe(red, [proj])  # second red still counts: unknown didn't reset
    assert await svc.get_open_incident(_pid(proj)) is not None

    await svc.observe([], [proj])  # unknown does not close an open incident
    assert await svc.get_open_incident(_pid(proj)) is not None


@pytest.mark.asyncio
async def test_failure_in_one_project_fails_open(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = await _project(db_session, "br-err")
    svc = get_base_red_incident_service(db_session)

    async def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("ci lookup exploded")

    monkeypatch.setattr(svc, "_apply", _boom)
    await svc.observe([_sample("br-err", red=True)], [proj])  # must not raise

    assert await svc.get_open_incident(_pid(proj)) is None


@pytest.mark.asyncio
async def test_clear_closes_audited_and_is_noop_without_incident(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.base_red_incident.get_audit_service",
        lambda: SimpleNamespace(log_event=audit),
    )
    proj = await _project(db_session, "br-clear")
    svc = get_base_red_incident_service(db_session)
    assert await svc.clear(_pid(proj), "ceo", "nothing open") is None

    red = [_sample("br-clear", red=True)]
    await svc.observe(red, [proj])
    await svc.observe(red, [proj])

    cleared = await svc.clear(_pid(proj), "ceo", "false positive")

    assert cleared is not None
    assert cleared.status == "closed"
    assert cleared.cleared_by == "ceo"
    assert cleared.closed_reason == "false positive"
    cleared_calls = [
        c for c in audit.call_args_list if c.kwargs["event_type"] == "base_red.cleared"
    ]
    assert len(cleared_calls) == 1
    assert cleared_calls[0].kwargs["details"]["cleared_by"] == "ceo"
    assert await svc.get_open_incident(_pid(proj)) is None


@pytest.mark.asyncio
async def test_ci_watch_cycle_feeds_incident_and_adopts_fix_task(
    db_session: AsyncSession,
) -> None:
    proj = await _project(db_session, "br-wired")
    src = _Source()
    src.samples = [_sample("br-wired", red=True)]
    engine = get_ci_watch_engine(db_session, source=src)

    first = await engine.run_cycle([proj])
    svc = get_base_red_incident_service(db_session)
    assert await svc.get_open_incident(_pid(proj)) is None
    second = await engine.run_cycle([proj])

    incident = await svc.get_open_incident(_pid(proj))
    assert incident is not None
    assert len(first) == 1
    assert second == []
    assert incident.owner_task_id == first[0].id  # adopts the ci_watch fix task
