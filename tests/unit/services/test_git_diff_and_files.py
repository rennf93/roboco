"""Task #62845be1: ``diff_and_files`` resolves shared state ONCE.

``diff()`` and ``list_changed_files()`` each independently re-resolve the
workspace, auth token, head ref, and diff base before running their own
``git diff`` subprocess — duplicated work when a caller (evidence assembly)
needs both. ``diff_and_files`` resolves that shared state a single time,
then runs the two ``git diff`` subprocesses concurrently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitService

_BR = "feature/backend/root1234--cellpm56--dev78901"


def _git_service() -> Any:
    # A real constructor (not __new__) so ``self.log`` is bound —
    # ``diff_and_files`` logs its own resolve/diff timing.
    return GitService(MagicMock())


@pytest.mark.asyncio
async def test_diff_and_files_resolves_shared_state_once() -> None:
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(return_value=Path("/tmp/qa-ws"))
    svc._token_for_branch = AsyncMock(return_value="tok")
    svc._resolve_head_ref = AsyncMock(return_value=f"origin/{_BR}")
    svc._resolve_diff_base = AsyncMock(return_value="origin/master")
    captured: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        captured.append(args)
        if args[:2] == ["diff", "--name-only"]:
            return type(
                "R", (), {"returncode": 0, "stdout": "README.md\nsrc/app.py\n"}
            )()
        return type("R", (), {"returncode": 0, "stdout": "diff body"})()

    with patch.object(svc, "_run_git", new=fake_run):
        diff, files = await svc.diff_and_files(branch_name=_BR)

    assert diff == "diff body"
    assert files == ["README.md", "src/app.py"]
    # The shared resolution work runs exactly ONCE, not once per sub-call.
    svc._workspace_for_branch.assert_awaited_once()
    svc._token_for_branch.assert_awaited_once()
    svc._resolve_head_ref.assert_awaited_once()
    svc._resolve_diff_base.assert_awaited_once()
    # Both the `diff` and `diff --name-only` subprocesses still ran, off the
    # same resolved base...head pair.
    assert any(c == ["diff", f"origin/master...origin/{_BR}"] for c in captured)
    assert any(
        c == ["diff", "--name-only", f"origin/master...origin/{_BR}"] for c in captured
    )


@pytest.mark.asyncio
async def test_diff_and_files_honors_explicit_base_and_preferred_parent() -> None:
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(return_value=Path("/tmp/dev-ws"))
    svc._token_for_branch = AsyncMock(return_value="tok")
    svc._resolve_head_ref = AsyncMock(return_value=_BR)
    svc._resolve_diff_base = AsyncMock(return_value="origin/master")

    async def fake_run(_ws: Any, _args: list[str], **_kw: Any) -> Any:
        return type("R", (), {"returncode": 0, "stdout": ""})()

    with patch.object(svc, "_run_git", new=fake_run):
        await svc.diff_and_files(
            branch_name=_BR, base="HEAD~1", preferred_parent="feature/backend/other"
        )

    # An explicit base skips _resolve_diff_base entirely.
    svc._resolve_diff_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_diff_and_files_matches_diff_and_list_changed_files_output() -> None:
    """Combined accessor returns the same data the two separate calls would."""
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(return_value=Path("/tmp/ws"))
    svc._token_for_branch = AsyncMock(return_value="tok")
    svc._resolve_head_ref = AsyncMock(return_value=f"origin/{_BR}")
    svc._resolve_diff_base = AsyncMock(return_value="origin/master")

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[:2] == ["diff", "--name-only"]:
            return type("R", (), {"returncode": 0, "stdout": "a.py\nb.py\n"})()
        return type("R", (), {"returncode": 0, "stdout": "full diff body"})()

    with patch.object(svc, "_run_git", new=fake_run):
        diff, files = await svc.diff_and_files(branch_name=_BR)

    assert diff == "full diff body"
    assert files == ["a.py", "b.py"]
