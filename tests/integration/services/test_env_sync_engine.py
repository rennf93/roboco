"""EnvSyncEngine — cascade prod->head; conflict opens a PR + task, never prod.

Mirrors the ci-watch engine test: seeds projects + agents, mocks the GitService
(the merges API + PR open are GitHub calls) so the cascade is driven by the
fake's queued statuses, and asserts the engine's contract:

* clean cascade (merged / already_ancestor) opens nothing,
* a conflict opens ONE sync PR + tracked task and stops the cascade,
* a tokenless project is skipped,
* per-cycle + rolling caps + per-repo dedup are honoured,
* the cascade target is never prod (the lower rung of every pair is non-prod).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, Team
from roboco.services import env_sync_engine as env_sync_module
from roboco.services.env_sync_engine import get_env_sync_engine
from roboco.services.task import ENV_SYNC_SOURCE, get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid

# A two-rung ladder: head=dev (PR target), prod=master (release target).
_LADDER = [
    {"name": "head", "branch": "dev"},
    {"name": "prod", "branch": "master"},
]


class _FakeGit:
    """Stand-in for GitService: drives the cascade from queued statuses."""

    def __init__(self, statuses: list[str], *, pr_number: int = 42) -> None:
        self._statuses = list(statuses)
        self.sync_calls: list[tuple[str, str, str]] = []
        self.pr_calls: list[tuple[str, str, str, str]] = []
        self._pr_number = pr_number

    async def sync_env_branch(
        self, slug: str, target_branch: str, source_branch: str
    ) -> dict[str, Any]:
        self.sync_calls.append((slug, target_branch, source_branch))
        status = self._statuses.pop(0) if self._statuses else "already_ancestor"
        return {"status": status}

    async def open_sync_pr(
        self, slug: str, source_branch: str, target_branch: str, body: str
    ) -> dict[str, Any] | None:
        self.pr_calls.append((slug, source_branch, target_branch, body))
        return {
            "number": self._pr_number,
            "url": f"https://github.com/x/{slug}/pull/{self._pr_number}",
        }


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
    db: AsyncSession,
    slug: str,
    git_url: str,
    *,
    environments: list[dict[str, str]] | None = _LADDER,
    token: str | None = "fake-encrypted-token",
) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=git_url,
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        environments=environments,
        git_token_encrypted=token,
    )
    db.add(project)
    await db.flush()
    return project


@pytest.fixture(autouse=True)
async def _enabled(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "env_sync_enabled", True)
    monkeypatch.setattr(settings, "env_sync_max_per_cycle", 5)
    monkeypatch.setattr(settings, "env_sync_max_open_tasks", 5)
    await _get_or_create_agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _get_or_create_agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


def _patch_git(monkeypatch: pytest.MonkeyPatch, fake: _FakeGit) -> None:
    monkeypatch.setattr(env_sync_module, "get_git_service", lambda _session: fake)


@pytest.mark.asyncio
async def test_clean_cascade_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = await _seed_project(db_session, "clean-a", "https://github.com/x/a.git")
    fake = _FakeGit(["merged"])
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([proj])
    assert created == []
    # prod(master) merged into head(dev) — one cascade step for the 2-rung ladder.
    assert fake.sync_calls == [("clean-a", "dev", "master")]
    assert fake.pr_calls == []


@pytest.mark.asyncio
async def test_already_ancestor_is_clean(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = await _seed_project(db_session, "anc-a", "https://github.com/x/anc.git")
    fake = _FakeGit(["already_ancestor"])
    _patch_git(monkeypatch, fake)
    assert await get_env_sync_engine(db_session).run_cycle([proj]) == []
    assert fake.sync_calls == [("anc-a", "dev", "master")]


@pytest.mark.asyncio
async def test_conflict_opens_pr_and_task_then_stops(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 4-rung ladder so a conflict on the FIRST (topmost) pair stops the cascade
    # before reaching head — proving it does not cascade a dirty merge downward.
    proj = await _seed_project(
        db_session,
        "conf-a",
        "https://github.com/x/conf.git",
        environments=[
            {"name": "head", "branch": "dev"},
            {"name": "qa", "branch": "qa"},
            {"name": "stag", "branch": "stag"},
            {"name": "prod", "branch": "master"},
        ],
    )
    fake = _FakeGit(["conflict", "merged"])  # only the first is consumed
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([proj])

    assert len(created) == 1
    task = created[0]
    assert task.source == ENV_SYNC_SOURCE
    assert task.status == TaskStatus.PENDING
    assert task.project_id == proj.id
    # The cascade stopped at the first conflict: only one sync_env_branch call.
    assert len(fake.sync_calls) == 1
    # The sync PR targets the lower (non-prod) rung of the conflicted pair.
    assert len(fake.pr_calls) == 1
    _slug, _source_branch, target_branch, _body = fake.pr_calls[0]
    assert target_branch != "master"  # never prod


@pytest.mark.asyncio
async def test_missing_ref_skips_without_pr(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = await _seed_project(db_session, "miss-a", "https://github.com/x/miss.git")
    fake = _FakeGit(["missing_ref"])
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([proj])
    assert created == []
    assert fake.pr_calls == []


@pytest.mark.asyncio
async def test_tokenless_project_skipped(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = await _seed_project(
        db_session, "notok", "https://github.com/x/notok.git", token=None
    )
    fake = _FakeGit(["merged"])
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([proj])
    assert created == []
    assert fake.sync_calls == []  # never attempted


@pytest.mark.asyncio
async def test_degenerate_ladder_skipped(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Single-rung ladder (head==prod) has no pairs to cascade.
    proj = await _seed_project(
        db_session,
        "single",
        "https://github.com/x/single.git",
        environments=[{"name": "prod", "branch": "master"}],
    )
    fake = _FakeGit(["merged"])
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([proj])
    assert created == []
    assert fake.sync_calls == []


@pytest.mark.asyncio
async def test_per_cycle_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "env_sync_max_per_cycle", 1)
    p1 = await _seed_project(db_session, "cap-1", "https://github.com/x/c1.git")
    p2 = await _seed_project(db_session, "cap-2", "https://github.com/x/c2.git")
    fake = _FakeGit(["conflict", "conflict"])
    _patch_git(monkeypatch, fake)
    created = await get_env_sync_engine(db_session).run_cycle([p1, p2])
    assert len(created) == 1  # capped at one per cycle


@pytest.mark.asyncio
async def test_deduped_per_repo(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo with an open env_sync task is skipped until the PR resolves."""
    proj = await _seed_project(db_session, "dedup", "https://github.com/x/d.git")
    fake = _FakeGit(["conflict"])
    _patch_git(monkeypatch, fake)
    first = await get_env_sync_engine(db_session).run_cycle([proj])
    assert len(first) == 1
    # Second cycle, same repo still has the open task -> deduped (no new PR).
    fake2 = _FakeGit(["conflict"])
    _patch_git(monkeypatch, fake2)
    second = await get_env_sync_engine(db_session).run_cycle([proj])
    assert second == []
    assert fake2.sync_calls == []  # cascade paused at the conflicted rung
    assert len(await get_task_service(db_session).list_open_env_sync_tasks()) == 1


@pytest.mark.asyncio
async def test_disabled_is_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "env_sync_enabled", False)
    proj = await _seed_project(db_session, "off", "https://github.com/x/off.git")
    fake = _FakeGit(["merged"])
    _patch_git(monkeypatch, fake)
    assert await get_env_sync_engine(db_session).run_cycle([proj]) == []
    assert fake.sync_calls == []
