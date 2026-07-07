"""ConventionsService root/HEAD resolution + backfill persistence (no DB)."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest
from roboco.services.conventions import ConventionsService

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.db.tables import ProjectTable


def _git_repo(root: Path) -> str:
    (root / "roboco" / "services").mkdir(parents=True)
    (root / "roboco" / "services" / "x.py").write_text("def f():\n    return 1\n")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def _svc() -> ConventionsService:
    return ConventionsService(session=cast("Any", None))


def test_resolve_reads_clone_head_returns_raw_sha_no_mutation(tmp_path: Path) -> None:
    sha = _git_repo(tmp_path)
    project = SimpleNamespace(workspace_path=None, head_commit=None, slug="p")
    root, sha_raw = _svc()._resolve(cast("ProjectTable", project), tmp_path)
    assert root == tmp_path
    assert sha_raw == sha
    # _resolve no longer mutates the ORM; callers mutate on the event loop.
    assert project.workspace_path is None
    assert project.head_commit is None


def test_resolve_non_git_path_returns_raw_sha_none(tmp_path: Path) -> None:
    project = SimpleNamespace(
        workspace_path=str(tmp_path), head_commit="deadbeef", slug="p"
    )
    _root, sha = _svc()._resolve(cast("ProjectTable", project), None)
    # Raw rev-parse returned None; the caller's `if sha is not None` guard
    # preserves the persisted head_commit (no mutation inside _resolve).
    assert sha is None
    assert project.head_commit == "deadbeef"


def test_resolve_no_workspace_returns_none_root() -> None:
    project = SimpleNamespace(workspace_path=None, head_commit=None, slug="p")
    root, sha = _svc()._resolve(cast("ProjectTable", project), None)
    assert root is None
    assert sha is None


def test_head_sha_at_non_git_returns_none(tmp_path: Path) -> None:
    assert ConventionsService._head_sha_at(tmp_path) is None


@pytest.mark.asyncio
async def test_resolve_workspace_force_refetches() -> None:
    """resolve_workspace must pass force=True so conventions reads always see
    the current default-branch HEAD (no 30s stale-map window after a merge)."""
    captured: dict[str, object] = {}

    class _FakeWS:
        async def ensure_read_clone(self, slug: str, *, force: bool = False) -> Path:
            captured["slug"] = slug
            captured["force"] = force
            return cast("Path", "/fake-clone")

    project = SimpleNamespace(slug="p", workspace_path=None, head_commit=None)
    with patch(
        "roboco.services.workspace.get_workspace_service",
        return_value=_FakeWS(),
    ):
        root = await _svc().resolve_workspace(cast("ProjectTable", project))

    assert root == "/fake-clone"
    assert captured["slug"] == "p"
    assert captured["force"] is True


@pytest.mark.asyncio
async def test_resolve_does_not_mutate_orm(monkeypatch, tmp_path: Path) -> None:
    # _resolve must return (root, sha_raw) and NOT touch project.workspace_path
    # or project.head_commit. The caller mutates on the event loop, not the
    # worker thread.
    svc = _svc()
    project = SimpleNamespace(
        workspace_path=None, head_commit="preexisting-sha", slug="p"
    )
    monkeypatch.setattr(svc, "_head_sha_at", lambda _root: "raw-abc123")
    monkeypatch.setattr(svc, "_workspace_root", lambda _p: tmp_path)

    root, sha = await asyncio.to_thread(
        svc._resolve, cast("ProjectTable", project), None
    )

    assert root == tmp_path
    assert sha == "raw-abc123"
    # the worker-thread call did NOT mutate the ORM:
    assert project.workspace_path is None
    assert project.head_commit == "preexisting-sha"
    # the caller then mutates on the event loop (the production contract):
    if root is not None:
        project.workspace_path = str(root)
        if sha is not None:
            project.head_commit = sha
    assert project.workspace_path == str(tmp_path)
    assert project.head_commit == "raw-abc123"


@pytest.mark.asyncio
async def test_resolve_raw_sha_none_for_non_git_path(
    monkeypatch, tmp_path: Path
) -> None:
    # _head_sha_at returns None (non-git path) -> _resolve returns (root, None),
    # and the caller's guard leaves project.head_commit untouched.
    svc = _svc()
    project = SimpleNamespace(
        workspace_path=None, head_commit="persisted-keep-me", slug="p"
    )
    monkeypatch.setattr(svc, "_head_sha_at", lambda _root: None)
    monkeypatch.setattr(svc, "_workspace_root", lambda _p: tmp_path)

    root, sha = await asyncio.to_thread(
        svc._resolve, cast("ProjectTable", project), None
    )
    assert sha is None
    # caller guard: sha is None -> do NOT touch head_commit
    if root is not None:
        project.workspace_path = str(root)
        if sha is not None:
            project.head_commit = sha
    assert project.head_commit == "persisted-keep-me"  # guard preserved
    # and head (cache key) falls back:
    head = sha or svc._head_sha(cast("ProjectTable", project))
    assert head == "persisted-keep-me"
