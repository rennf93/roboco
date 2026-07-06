"""Git-workflow smoke scenarios for the Phase 4 hardening batch.

Cross-layer wiring for three findings that the brief deferred here:

- H11 — the clone/fetch PAT rides a per-call ``-c http.extraheader=
  Authorization: Basic <base64>`` config, never URL-embedded into argv
  (``/proc/<pid>/cmdline`` exposure class). The smoke env uses local-protocol
  origins (tokenless), so a true HTTPS exercise isn't feasible; this mirrors
  ``tests/unit/services/test_workspace_read_clone.py::
  test_sync_read_clone_with_token_uses_extraheader_not_url`` — patching
  ``subprocess.run`` and asserting the argv shape against a fake HTTPS URL +
  token. The real production path is exercised through the same
  ``WorkspaceService._sync_read_clone`` static method driven here.
- M37 — two concurrent ``WorkSessionService.merge_pr`` calls on the same
  ACTIVE work_session serialize via ``FOR UPDATE`` so only one writes
  ``merged_by`` / ``pr_merged_at``. Drives the real service against the e2e
  Postgres (separate sessions on ``asyncio.gather``), seeded with project +
  agents + task + ACTIVE work_session.
- M38 — ``GitService._pr_is_merged`` returns ``None`` on ``httpx.HTTPError``
  (indeterminate, not False), and the caller (``_merge_with_retry``) treats
  ``None`` as "assume merged" — falling through instead of raising
  ``MergeConflictError`` and respawning the PM against an already-merged PR.
  Drives ``_pr_is_merged`` with a mocked ``httpx.AsyncClient`` that raises,
  then drives ``_merge_with_retry`` with ``_pr_is_merged`` stubbed to ``None``.

Deviations from a true end-to-end exercise (noted): the smoke harness's git
origin is local-protocol (tokenless), so H11 cannot exercise a real
HTTPS-authenticated clone/fetch — the argv-shape assertion against a fake
HTTPS URL + token is the strongest feasible check and matches the unit test.
M38's ``_pr_is_merged`` network call is mocked because the smoke harness's
fake GitHub router does not model the merged-state GET deterministically
across transient failure; the unit-covered caller path (``_merge_with_retry``
fall-through) is exercised directly. The full-suite session-scoped workspace
contamination across e2e_smoke files is a pre-existing harness limitation
(documented in the module README) and is out of scope — each scenario here
passes in isolation.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from roboco.db.tables import (
    AgentTable,
    ProjectTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.work_session import WorkSessionStatus
from roboco.services.git import GitService
from roboco.services.work_session import get_work_session_service
from roboco.services.workspace import WorkspaceService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack


# ---------------------------------------------------------------------------
# H11 — clone/fetch PAT injected via http.extraheader, not URL-embedded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h11_pat_not_in_argv(e2e_stack: E2EStack) -> None:
    """H11: a private-repo refresh injects the PAT via
    ``-c http.extraheader=Authorization: Basic <base64>``, never URL-embedded
    into the fetch argv. The smoke origin is local-protocol (tokenless), so
    this drives ``WorkspaceService._sync_read_clone`` with a fake HTTPS URL +
    token and a ``subprocess.run`` capture — the same argv-shape contract the
    production path honors (mirrors the unit test)."""
    clone = e2e_stack.root / "h11-clone"
    clone.mkdir()
    token = "ghp_SMOKEARGV"
    git_url = "https://github.com/o/r"
    captured: list[list[str]] = []

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run):
        WorkspaceService._sync_read_clone(clone, git_url, "master", token)

    fetch_argv = next(a for a in captured if "fetch" in a)
    # Token never appears in argv (no /proc/<pid>/cmdline leak).
    assert token not in fetch_argv
    assert f"https://{token}@" not in fetch_argv
    # The bare URL is the fetch ref (no token embedded in the URL).
    assert git_url in fetch_argv
    # The basic-auth extraheader carries the encoded token.
    expected = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    assert f"http.extraheader=Authorization: Basic {expected}" in fetch_argv


# ---------------------------------------------------------------------------
# M37 — concurrent merge_pr calls serialize: exactly one writes merged_by
# ---------------------------------------------------------------------------


async def _seed_active_session(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """Seed project + 3 agents + task + ACTIVE work_session; return
    (ws_id, merger_a_id, merger_b_id). The two merger agents exist so the
    merged_by FK is satisfied."""
    worker = AgentTable(
        id=uuid4(),
        name="m37-worker",
        slug=f"m37-worker-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    merger_a = AgentTable(
        id=uuid4(),
        name="m37-merger-a",
        slug=f"m37-merger-a-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    merger_b = AgentTable(
        id=uuid4(),
        name="m37-merger-b",
        slug=f"m37-merger-b-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    for agent in (worker, merger_a, merger_b):
        session.add(agent)
    await session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="m37-proj",
        slug=f"m37-proj-{uuid4().hex[:6]}",
        git_url="git@x:y/z.git",
        assigned_cell=Team.BACKEND,
        created_by=worker.id,
    )
    session.add(project)
    await session.flush()
    tid = uuid4()
    session.add(
        TaskTable(
            id=tid,
            title="m37 task",
            description="d",
            acceptance_criteria=["done"],
            status=TaskStatus.IN_PROGRESS,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            team=Team.BACKEND,
            confirmed_by_human=True,
            project_id=project.id,
            created_by=worker.id,
            branch_name="feature/x",
        )
    )
    await session.flush()
    ws_id = uuid4()
    session.add(
        WorkSessionTable(
            id=ws_id,
            project_id=project.id,
            task_id=tid,
            agent_id=worker.id,
            branch_name="feature/x",
            base_branch="master",
            target_branch="master",
            status=WorkSessionStatus.ACTIVE,
        )
    )
    await session.flush()
    await session.commit()
    return ws_id, UUID(str(merger_a.id)), UUID(str(merger_b.id))


@pytest.mark.asyncio
async def test_m37_concurrent_merge_pr_single_write(e2e_stack: E2EStack) -> None:
    """M37: two concurrent ``merge_pr`` calls on the same ACTIVE work_session
    serialize via ``FOR UPDATE`` — only one writes ``merged_by`` /
    ``pr_merged_at``; the other blocks on the row lock, then sees COMPLETED
    and no-ops. Drives the real ``WorkSessionService`` against the e2e
    Postgres (two separate sessions on ``asyncio.gather``)."""
    engine = create_async_engine(e2e_stack.db_url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as seed_sess:
            ws_id, merger_a_id, merger_b_id = await _seed_active_session(seed_sess)

        async def _call(merger: UUID) -> Any:
            async with factory() as sess:
                svc = get_work_session_service(sess)
                result = await svc.merge_pr(ws_id, merger)
                await sess.commit()
                return result, merger

        # Both fire together: without FOR UPDATE each session's SELECT sees
        # ACTIVE and both write their own merger. With FOR UPDATE B's SELECT
        # blocks on A's row lock until A commits, then reads COMPLETED and
        # no-ops — only A's merger is recorded.
        res_a, res_b = await asyncio.gather(_call(merger_a_id), _call(merger_b_id))
    finally:
        await engine.dispose()

    a_row, a_merger = res_a
    b_row, b_merger = res_b
    assert a_row is not None
    assert b_row is not None
    assert a_row.status == WorkSessionStatus.COMPLETED
    assert b_row.status == WorkSessionStatus.COMPLETED
    # Exactly one caller wrote — A recorded itself; B did not overwrite.
    assert a_row.merged_by == a_merger
    assert b_row.merged_by == a_merger
    assert b_row.merged_by != b_merger


# ---------------------------------------------------------------------------
# M38 — _pr_is_merged returns None on HTTPError; caller assumes merged
# ---------------------------------------------------------------------------


def _httpx_raising_client() -> MagicMock:
    """An AsyncClient whose GET raises httpx.HTTPError (network indeterminate)."""
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(side_effect=httpx.HTTPError("network indeterminate"))
    return fake_client


def _git_service() -> GitService:
    """A GitService with a MagicMock session — _pr_is_merged / _merge_with_retry
    don't touch the DB in the paths exercised here."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return GitService(session)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


@pytest.mark.asyncio
async def test_m38_pr_is_merged_returns_none_on_httperror() -> None:
    """M38(a): on ``httpx.HTTPError`` the lookup is indeterminate -> ``None``,
    not ``False``. A clean ``False`` would make the caller raise
    ``MergeConflictError`` and respawn the PM against an already-merged PR."""
    svc = _git_service()
    with patch(
        "roboco.services.git.httpx.AsyncClient",
        return_value=_httpx_raising_client(),
    ):
        out = await svc._pr_is_merged("acme", "repo", 11, "tok")
    assert out is None


@pytest.mark.asyncio
async def test_m38_merge_with_retry_none_does_not_raise_conflict() -> None:
    """M38(b): the caller (``_merge_with_retry``) treats ``None`` as "assume
    merged" — it falls through (returns the failed resp) instead of raising
    ``MergeConflictError``, so an indeterminate gh call doesn't respawn the
    PM against an already-merged PR. A real ``False`` still raises."""
    svc = _git_service()
    _bind(svc, "log", MagicMock())
    _bind(svc, "_first_allowed_merge_method", AsyncMock(return_value=None))
    # 405 -> disambiguation path -> _pr_is_merged returns None -> fall through.
    resp_405 = MagicMock(is_success=False, status_code=405, text="not allowed")
    _bind(svc, "_call_merge_api", AsyncMock(return_value=resp_405))
    _bind(svc, "_pr_is_merged", AsyncMock(return_value=None))
    _bind(svc, "_sync_target_branch", AsyncMock())

    ctx = GitService._MergeContext(
        owner="acme",
        repo="repo",
        pr_number=11,
        git_token="tok",
        workspace=Path("/tmp/ws"),
        target="feature/main_pm/root1",
    )
    # None must NOT raise — indeterminate falls through, not conflict.
    out = await svc._merge_with_retry(ctx)
    assert out is resp_405
