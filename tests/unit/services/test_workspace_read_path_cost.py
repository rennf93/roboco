"""A read-only git call no longer pays a network fetch and an install each time.

Live 2026-10-28: ``/api/git/status`` p50 25 s and ``/api/git/log`` p50 13.5 s
against the agents' 15 s MCP timeout; 41 + 2 ``evidence_leg_timeout``. On
2026-11-01 one ``/api/v1/do/evidence`` call ran fe-pm's whole ``uv sync``
(38 s, failed) before its "branch fetch" leg timed out at 45 s, and the next
evidence call started the same install again. Every git route and evidence
leg resolves the clone through ``ensure_workspace``, which on a healthy clone
ran a refresh fetch through the project's relay (a network round trip,
serialized per project) and the dev-deps install check:

- the refresh's 30 s TTL lived on the ``WorkspaceService`` instance, and every
  request builds a new one (``get_workspace_service(session)``), so it never
  skipped a single fetch;
- a failed install wrote no marker, so every later call ran it again;
- reads that fetch the one ref they need themselves, or need no origin ref at
  all, refreshed anyway.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.routes import git as git_routes
from roboco.services import workspace as workspace_module
from roboco.services.workspace import WorkspaceService
from roboco.services.workspace_container import WorkspaceContainerResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_INSTALL_FAILED = 2
_TWO_ATTEMPTS = 2


def _request_scoped_service(workspace: Path) -> WorkspaceService:
    """A WorkspaceService as one request builds it (a fresh instance)."""
    svc = WorkspaceService(MagicMock())
    agent = MagicMock(id=uuid4(), slug="fe-pm", team=None)
    object.__setattr__(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    object.__setattr__(svc, "get_workspace_path", MagicMock(return_value=workspace))
    object.__setattr__(svc, "_prune_broken_refs", MagicMock())
    return svc


@pytest.fixture
def healthy_clone(tmp_path: Path) -> Path:
    """A clone that passes the health check, with a Python lockfile."""
    workspace = tmp_path / "roboco-api" / "frontend" / "fe-pm"
    (workspace / ".git" / "objects").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (workspace / "uv.lock").write_text("version = 1\n")
    return workspace


async def test_requests_within_the_ttl_share_one_refresh(healthy_clone: Path) -> None:
    fetch = AsyncMock()
    with (
        patch.object(WorkspaceService, "_fetch_origin_best_effort", fetch),
        patch.object(WorkspaceService, "install_dev_deps", AsyncMock()),
    ):
        for _request in range(3):
            await _request_scoped_service(healthy_clone).ensure_workspace(
                project_slug="roboco-api", agent_id=uuid4()
            )

    fetch.assert_awaited_once()


async def test_a_failed_install_is_not_rerun_by_every_request(
    healthy_clone: Path,
) -> None:
    install = AsyncMock(
        return_value=WorkspaceContainerResult(_INSTALL_FAILED, "", "error: boom\n")
    )
    with (
        patch.object(WorkspaceService, "_fetch_origin_best_effort", AsyncMock()),
        patch.object(workspace_module, "run_in_workspace_container", install),
    ):
        for _request in range(3):
            await _request_scoped_service(healthy_clone).ensure_workspace(
                project_slug="roboco-api", agent_id=uuid4()
            )

    install.assert_awaited_once()


async def test_a_failed_install_is_retried_once_its_window_passed(
    healthy_clone: Path,
) -> None:
    install = AsyncMock(
        return_value=WorkspaceContainerResult(_INSTALL_FAILED, "", "error: boom\n")
    )
    svc = _request_scoped_service(healthy_clone)
    with patch.object(workspace_module, "run_in_workspace_container", install):
        await svc.install_dev_deps(healthy_clone)
        later = workspace_module._monotonic() + workspace_module._DEV_DEPS_RETRY_SECONDS
        with patch.object(workspace_module, "_monotonic", return_value=later + 1):
            await svc.install_dev_deps(healthy_clone)

    assert install.await_count == _TWO_ATTEMPTS


async def test_skip_refresh_neither_fetches_nor_installs(healthy_clone: Path) -> None:
    """``skip_refresh`` hands back the healthy clone as it is."""
    fetch = AsyncMock()
    install = AsyncMock()
    with (
        patch.object(WorkspaceService, "_fetch_origin_best_effort", fetch),
        patch.object(WorkspaceService, "install_dev_deps", install),
    ):
        await _request_scoped_service(healthy_clone).ensure_workspace(
            project_slug="roboco-api", agent_id=uuid4(), skip_refresh=True
        )

    fetch.assert_not_awaited()
    install.assert_not_awaited()


async def _call_log() -> object:
    return await git_routes.get_git_log(
        db=MagicMock(),
        agent=MagicMock(agent_id=uuid4()),
        project_slug="roboco-api",
        limit=5,
        branch="feature/frontend/c3a63a00",
    )


async def _call_diff() -> object:
    return await git_routes.get_git_diff(
        db=MagicMock(),
        agent=MagicMock(agent_id=uuid4()),
        project_slug="roboco-api",
        staged=False,
        file_path=None,
    )


@pytest.mark.parametrize(
    ("call", "resolver"),
    [(_call_log, "get_workspace"), (_call_diff, "tree_for_agent_read")],
    ids=["log", "diff"],
)
async def test_reads_that_need_no_refresh_ask_for_none(
    call: Callable[[], Awaitable[object]], resolver: str
) -> None:
    """``/api/git/log`` resolves its branch with its own fetch
    (``_resolve_head_ref``); ``/api/git/diff`` reads the working tree."""
    tree = Path("/data/workspaces/p/t/a")
    git = MagicMock()
    git.get_workspace = AsyncMock(return_value=tree)
    git.tree_for_agent_read = AsyncMock(return_value=(tree, None))
    git._resolve_head_ref = AsyncMock(return_value="origin/feature/frontend/c3a63a00")

    async def _run_git(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git"], 0, "", "")

    git._run_git = AsyncMock(side_effect=_run_git)
    with (
        patch.object(git_routes, "get_git_service", return_value=git),
        patch.object(
            git_routes, "_resolve_project_slug", AsyncMock(return_value="roboco-api")
        ),
    ):
        await call()

    resolved = getattr(git, resolver).await_args
    assert resolved is not None
    assert resolved.kwargs == {"skip_refresh": True}


async def test_evidence_inspection_fetches_only_its_branch(tmp_path: Path) -> None:
    """The evidence "branch fetch" leg fetches the branch it inspects; the
    clone's own refresh (and an install) never rides on it."""
    svc = WorkspaceService(MagicMock())
    ensure = AsyncMock(return_value=tmp_path)
    object.__setattr__(svc, "ensure_workspace", ensure)
    object.__setattr__(
        svc, "_resolve_branch_to_project_slug", AsyncMock(return_value="roboco-api")
    )
    object.__setattr__(svc, "_relay_for", AsyncMock(return_value=None))

    with pytest.raises(workspace_module.WorkspaceError):
        await svc.fetch_branch_for_inspection(
            agent_id=uuid4(), branch_name="feature/frontend/c3a63a00"
        )

    assert ensure.await_args is not None
    assert ensure.await_args.kwargs.get("skip_refresh") is True
