"""The env-sync local-git merge fallback (forge Phase 2.1): a provider's
shaped 501 routes sync_env_branch through a throwaway clone→merge→push, with
the same status vocabulary the merges-API path produces.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.services.git as git_module
from roboco.services.git import GitService

if TYPE_CHECKING:
    from pathlib import Path


def _bind(svc: GitService, name: str, value: object) -> None:
    """Stub without tripping mypy's method-assign check (test_git.py idiom)."""
    setattr(svc, name, value)


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    _bind(svc, "log", MagicMock())
    return svc


def _result(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _scripted_run_git(
    outcomes: dict[str, SimpleNamespace],
) -> tuple[AsyncMock, list[str]]:
    """Route each _run_git call by its first meaningful arg; record verbs."""
    verbs: list[str] = []

    async def _run(_workspace: Path, args: list[str], **_kw: Any) -> SimpleNamespace:
        verb = args[0] if args[0] != "merge-base" else "merge-base"
        verbs.append(verb)
        return outcomes.get(verb, _result())

    return AsyncMock(side_effect=_run), verbs


@pytest.mark.asyncio
async def test_clean_merge_pushes_and_reports_sha() -> None:
    svc = _service()
    # merge-base non-zero = not an ancestor → real merge happens.
    outcomes = {
        "merge-base": _result(1),
        "rev-parse": _result(0, "abc123\n"),
    }
    run, verbs = _scripted_run_git(outcomes)
    _bind(svc, "_run_git", run)

    status = await svc._local_merge_branch("https://g/x/y.git", "tok", "stag", "main")

    assert status == {"status": "merged", "sha": "abc123"}
    assert verbs[0] == "clone"
    assert "merge" in verbs
    assert "push" in verbs


@pytest.mark.asyncio
async def test_already_ancestor_short_circuits() -> None:
    svc = _service()
    run, verbs = _scripted_run_git({"merge-base": _result(0)})
    _bind(svc, "_run_git", run)

    status = await svc._local_merge_branch("https://g/x/y.git", "tok", "stag", "main")

    assert status == {"status": "already_ancestor"}
    assert "merge" not in verbs
    assert "push" not in verbs


@pytest.mark.asyncio
async def test_merge_conflict_never_pushes() -> None:
    svc = _service()
    run, verbs = _scripted_run_git(
        {"merge-base": _result(1), "merge": _result(1, "CONFLICT")}
    )
    _bind(svc, "_run_git", run)

    status = await svc._local_merge_branch("https://g/x/y.git", "tok", "stag", "main")

    assert status == {"status": "conflict"}
    assert "push" not in verbs


@pytest.mark.asyncio
async def test_missing_branch_maps_to_missing_ref() -> None:
    svc = _service()
    run, _ = _scripted_run_git({"fetch": _result(128)})
    _bind(svc, "_run_git", run)

    status = await svc._local_merge_branch("https://g/x/y.git", "tok", "stag", "main")

    assert status == {"status": "missing_ref"}


@pytest.mark.asyncio
async def test_sync_env_branch_routes_shaped_501_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _service()
    _bind(svc, "session", MagicMock())

    project = SimpleNamespace(git_url="https://gitea.example.com/a/b.git")
    project_svc = MagicMock(get_by_slug=AsyncMock(return_value=project))
    monkeypatch.setattr(git_module, "get_project_service", lambda _s: project_svc)
    _bind(svc, "_token_for_project", AsyncMock(return_value="tok"))
    _bind(svc, "_parse_git_url", MagicMock(return_value=MagicMock()))

    forge = MagicMock(
        merge_branch=AsyncMock(
            return_value=SimpleNamespace(status_code=501, text="", json=dict)
        )
    )
    monkeypatch.setattr(GitService, "_forge", property(lambda _self: forge))
    fallback = AsyncMock(return_value={"status": "merged", "sha": "abc"})
    _bind(svc, "_local_merge_branch", fallback)

    status = await svc.sync_env_branch("proj", "stag", "main")

    assert status == {"status": "merged", "sha": "abc"}
    fallback.assert_awaited_once_with(
        "https://gitea.example.com/a/b.git", "tok", "stag", "main"
    )
