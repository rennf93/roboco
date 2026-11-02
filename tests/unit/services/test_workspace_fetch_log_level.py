"""The healthy-clone refresh fetch is authenticated, so an auth failure warns.

Wave A4 (2026-05-12) downgraded the refresh fetch's auth failure to DEBUG
because that fetch ran credential-less on purpose and failed on every private
repository. The refresh is now served through the project's relay, which
carries the project token: a private repository refreshes, and an auth
failure means the stored token is wrong or expired — an operator problem that
must surface at WARNING like any other failed fetch.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import WorkspaceService

_WORKSPACE = Path("/data/workspaces/roboco-api/backend/be-dev-1")


def _service(origin_stderr: str, returncode: int) -> tuple[WorkspaceService, AsyncMock]:
    svc = WorkspaceService(MagicMock())
    origin = AsyncMock(
        return_value=CompletedProcess(["git", "fetch"], returncode, "", origin_stderr)
    )
    object.__setattr__(svc, "_origin_git", origin)
    object.__setattr__(
        svc,
        "_worktree_git",
        MagicMock(
            side_effect=lambda _ws, args, **_kw: CompletedProcess(
                args, 0, "origin/main\n" if args[0] == "symbolic-ref" else "feat\n", ""
            )
        ),
    )
    return svc, origin


async def _refresh_capturing_logs(
    origin_stderr: str, returncode: int
) -> tuple[list[tuple[str, str]], AsyncMock]:
    svc, origin = _service(origin_stderr, returncode)
    captured: list[tuple[str, str]] = []

    def _capture(level: str) -> Any:
        return lambda event, **_kw: captured.append((level, event))

    with (
        patch(
            "roboco.services.workspace.logger.warning", side_effect=_capture("warning")
        ),
        patch("roboco.services.workspace.logger.debug", side_effect=_capture("debug")),
    ):
        await svc._fetch_origin_best_effort(_WORKSPACE, "roboco-api")
    return captured, origin


@pytest.mark.asyncio
async def test_the_refresh_is_scoped_and_served_through_the_relay() -> None:
    captured, origin = await _refresh_capturing_logs("", 0)

    origin.assert_awaited_once()
    assert origin.await_args is not None
    assert origin.await_args.args[2] == [
        "fetch",
        "--no-tags",
        "--prune",
        "origin",
        "feat",
        "main",
    ]
    assert not captured


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: Authentication failed for 'https://github.com/o/r.git/'\n",
        "remote: Repository not found.\nfatal: repository not found\n",
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Could not resolve host: github.com\n",
    ],
)
@pytest.mark.asyncio
async def test_every_failed_refresh_warns(stderr: str) -> None:
    captured, _ = await _refresh_capturing_logs(stderr, 128)

    assert [level for level, _ in captured] == ["warning"]
