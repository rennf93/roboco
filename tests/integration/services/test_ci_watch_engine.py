"""CiWatchEngine — originate a fix task per red opted-in repo, bounded + deduped.

Mirrors the self-heal engine: opens a PENDING ci_watch task per red project,
never merges/approves; dedupes per repo (git_url) so a still-red repo with an
open task gets none; honours per-cycle + rolling caps; a None-signal project
(no sample) yields no task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, Team
from roboco.services.ci_watch_engine import get_ci_watch_engine
from roboco.services.task import CI_WATCH_SOURCE, get_task_service
from roboco.services.telemetry.source import TelemetrySample

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid


class _FakeSource:
    def __init__(self, samples: list[TelemetrySample]) -> None:
        self._samples = samples

    async def fetch(self, _projects: list[object]) -> list[TelemetrySample]:
        return list(self._samples)


def _breach(slug: str, *, failed: bool = True) -> TelemetrySample:
    return TelemetrySample(
        signal_name=f"ci_conclusion:{slug}",
        value=1.0 if failed else 0.0,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint=slug,
        observed_at="2026-06-25T00:00:00Z",
        raw_ref=f"https://github.com/x/{slug}/actions/runs/1",
        detail=f"CI on {slug}@master concluded 'failure'",
    )


async def _get_or_create_agent(
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


async def _seed_project(
    db: AsyncSession, slug: str, git_url: str, *, workflow: str | None = None
) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=git_url,
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        ci_watch_enabled=True,
        ci_watch_workflow=workflow,
    )
    db.add(project)
    await db.flush()
    return project


@pytest.fixture(autouse=True)
async def _enabled(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_max_per_cycle", 5)
    monkeypatch.setattr(settings, "ci_watch_max_open_tasks", 5)
    await _get_or_create_agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _get_or_create_agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


@pytest.mark.asyncio
async def test_red_project_opens_one_fix_task(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "red-a", "https://github.com/x/a.git")
    engine = get_ci_watch_engine(db_session, source=_FakeSource([_breach("red-a")]))

    created = await engine.run_cycle([proj])

    assert len(created) == 1
    task = created[0]
    assert task.project_id == proj.id
    assert task.source == CI_WATCH_SOURCE
    assert task.confirmed_by_human is True
    assert task.status == TaskStatus.PENDING  # opened, never merged/approved


@pytest.mark.asyncio
async def test_still_red_with_open_task_opens_nothing(
    db_session: AsyncSession,
) -> None:
    proj = await _seed_project(db_session, "red-b", "https://github.com/x/b.git")
    src = _FakeSource([_breach("red-b")])
    engine = get_ci_watch_engine(db_session, source=src)
    first = await engine.run_cycle([proj])
    assert len(first) == 1
    # Second cycle, same repo still red → deduped (one open task per git_url)
    second = await engine.run_cycle([proj])
    assert second == []


@pytest.mark.asyncio
async def test_per_cycle_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ci_watch_max_per_cycle", 1)
    p1 = await _seed_project(db_session, "red-c", "https://github.com/x/c.git")
    p2 = await _seed_project(db_session, "red-d", "https://github.com/x/d.git")
    src = _FakeSource([_breach("red-c"), _breach("red-d")])
    created = await get_ci_watch_engine(db_session, source=src).run_cycle([p1, p2])
    assert len(created) == 1  # capped at one per cycle


@pytest.mark.asyncio
async def test_green_or_no_signal_opens_nothing(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "quiet", "https://github.com/x/q.git")
    # No sample at all (None signal) — engine must not originate.
    none_engine = get_ci_watch_engine(db_session, source=_FakeSource([]))
    assert await none_engine.run_cycle([proj]) == []
    # A green (non-breaching) sample — also no task.
    green_engine = get_ci_watch_engine(
        db_session, source=_FakeSource([_breach("quiet", failed=False)])
    )
    assert await green_engine.run_cycle([proj]) == []
    assert await get_task_service(db_session).list_open_ci_watch_tasks() == []


@pytest.mark.asyncio
async def test_disabled_is_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ci_watch_enabled", False)
    proj = await _seed_project(db_session, "red-e", "https://github.com/x/e.git")
    src = _FakeSource([_breach("red-e")])
    assert await get_ci_watch_engine(db_session, source=src).run_cycle([proj]) == []


# ---------------------------------------------------------------------------
# #44: dedupe by (git_url, workflow) — a multi-workflow monorepo with two red
# workflows gets a fix task per workflow, not one collapsed task per repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_red_workflows_of_one_monorepo_both_open(
    db_session: AsyncSession,
) -> None:
    """#44: same git_url, DIFFERENT workflows, both red → two fix tasks."""
    git = "https://github.com/x/mono.git"
    p_a = await _seed_project(db_session, "mono-wf-a", git, workflow="wf-a.yml")
    p_b = await _seed_project(db_session, "mono-wf-b", git, workflow="wf-b.yml")
    src = _FakeSource([_breach("mono-wf-a"), _breach("mono-wf-b")])
    projects = [p_a, p_b]
    created = await get_ci_watch_engine(db_session, source=src).run_cycle(projects)
    assert len(created) == len(projects)
    assert {c.project_id for c in created} == {p_a.id, p_b.id}


@pytest.mark.asyncio
async def test_same_git_url_same_workflow_still_deduped(
    db_session: AsyncSession,
) -> None:
    """Regression guard: two cell-projects on one repo, SAME workflow → one task
    (the (git_url, workflow) key still collapses a same-workflow monorepo)."""
    git = "https://github.com/x/mono2.git"
    p1 = await _seed_project(db_session, "mono2-a", git, workflow="wf-a.yml")
    p2 = await _seed_project(db_session, "mono2-b", git, workflow="wf-a.yml")
    src = _FakeSource([_breach("mono2-a"), _breach("mono2-b")])
    created = await get_ci_watch_engine(db_session, source=src).run_cycle([p1, p2])
    assert len(created) == 1


@pytest.mark.asyncio
async def test_default_workflow_null_rows_deduped(
    db_session: AsyncSession,
) -> None:
    """Two NULL-workflow project rows on one repo both use the default workflow
    → deduped to one task (coalesce treats NULL as the default workflow)."""
    git = "https://github.com/x/mono3.git"
    p1 = await _seed_project(db_session, "mono3-a", git)  # ci_watch_workflow=None
    p2 = await _seed_project(db_session, "mono3-b", git)
    src = _FakeSource([_breach("mono3-a"), _breach("mono3-b")])
    created = await get_ci_watch_engine(db_session, source=src).run_cycle([p1, p2])
    assert len(created) == 1
