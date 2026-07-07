"""A failed ``_configure_git`` must not leave the project PAT on disk.

The clone-failure except clauses ``rmtree`` the workspace before raising, so a
half-configured clone (PAT still in ``.git/config``) is destroyed and the next
``ensure_workspace`` re-clones from scratch instead of short-circuiting past the
leak on a valid ``.git`` health check.
"""

from __future__ import annotations

import base64
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from roboco.services.workspace import WorkspaceError, WorkspaceService

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _service() -> WorkspaceService:
    session = MagicMock()
    return WorkspaceService(session)


def _completed(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")


def _leak_side_effect(
    workspace: Path, token: str
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """``subprocess.run`` side effect: a real ``git clone`` writes the
    tokenized auth URL into ``.git/config``; a failing ``_configure_git``
    raises before the scrub. Simulate both so the leak is on disk when the
    except clause fires."""

    def _impl(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "git" and "clone" in argv:
            # git clone writes the auth URL into .git/config.
            cfg = workspace / ".git" / "config"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                f'[remote "origin"]\n\turl = https://{token}@github.com/o/r\n'
            )
            return _completed(argv)
        if argv and argv[0] == "git" and len(argv) > 1 and argv[1] == "config":
            # _configure_git's first `git config` fails before the scrub.
            raise subprocess.CalledProcessError(
                returncode=128, cmd=argv, stderr="disk error mid-configure"
            )
        return _completed(argv)

    return _impl


@pytest.mark.asyncio
async def test_configure_git_failure_rmtrees_leaked_workspace(tmp_path: Path) -> None:
    """A ``_configure_git`` failure after the clone wrote the PAT into
    ``.git/config`` must destroy the workspace — no PAT left on disk."""
    token = "ghp_LEAKEDTOKEN"
    workspace = tmp_path / "ws"
    svc = _service()

    with (
        patch(
            "roboco.services.workspace.subprocess.run",
            side_effect=_leak_side_effect(workspace, token),
        ),
        pytest.raises(WorkspaceError),
    ):
        await svc._clone_repo(
            workspace,
            git_url="https://github.com/o/r",
            default_branch="master",
            git_token=token,
        )

    # The half-configured clone (with the PAT in .git/config) must be gone,
    # so the next ensure_workspace re-clones instead of short-circuiting past
    # the leak.
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_clone_timeout_rmtrees_partial_workspace(tmp_path: Path) -> None:
    """A clone timeout must likewise not leave a partial workspace behind —
    the same rmtree-on-failure covers the TimeoutExpired branch."""
    workspace = tmp_path / "ws-timeout"
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("partial")
    svc = _service()

    def _impl(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_impl),
        pytest.raises(WorkspaceError),
    ):
        await svc._clone_repo(
            workspace,
            git_url="https://github.com/o/r",
            default_branch="master",
            git_token="ghp_X",
        )

    assert not workspace.exists()


@pytest.mark.asyncio
async def test_clone_argv_carries_token_via_extraheader_not_url(tmp_path: Path) -> None:
    """H11: the decrypted PAT must NOT appear in the clone argv (where
    /proc/<pid>/cmdline would expose it). Instead the token rides a per-call
    ``-c http.extraheader=Authorization: Basic <base64(x-access-token:token)>``
    config and the clone URL stays bare."""
    token = "ghp_SECRETARGV"
    workspace = tmp_path / "ws-argv"
    svc = _service()
    captured: list[list[str]] = []

    def _impl(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(argv))
        # Pretend the clone succeeds and writes a bare-url .git/config.
        if len(argv) > 1 and argv[1] == "clone":
            cfg = workspace / ".git" / "config"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text('[remote "origin"]\n\turl = https://github.com/o/r\n')
        return _completed(argv)

    with patch("roboco.services.workspace.subprocess.run", side_effect=_impl):
        await svc._clone_repo(
            workspace,
            git_url="https://github.com/o/r",
            default_branch="master",
            git_token=token,
        )

    clone_argv = next(a for a in captured if "clone" in a)
    # The raw token must never appear anywhere in the clone argv.
    assert token not in clone_argv
    # The clone URL passed to git must be the bare URL (no embedded token).
    assert "https://github.com/o/r" in clone_argv
    assert f"https://{token}@github.com/o/r" not in clone_argv
    # The per-call http.extraheader carries the base64 basic-auth value.
    expected = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    assert f"http.extraheader=Authorization: Basic {expected}" in clone_argv
    # The -c flag precedes the clone subcommand.
    assert "-c" in clone_argv
    c_idx = clone_argv.index("-c")
    assert clone_argv[c_idx + 1] == f"http.extraheader=Authorization: Basic {expected}"
    assert "clone" in clone_argv[clone_argv.index("-c") + 2 :]
