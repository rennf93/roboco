"""ConventionsService root/HEAD resolution + backfill persistence (no DB)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

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
    return ConventionsService(session=None)  # type: ignore[arg-type]


def test_resolve_reads_clone_head_and_backfills(tmp_path: Path) -> None:
    sha = _git_repo(tmp_path)
    project = SimpleNamespace(workspace_path=None, head_commit=None, slug="p")
    root, head = _svc()._resolve(cast("ProjectTable", project), tmp_path)
    assert root == tmp_path
    assert head == sha
    # The backfill: the resolved path + real HEAD are persisted on the project.
    assert project.workspace_path == str(tmp_path)
    assert project.head_commit == sha


def test_resolve_non_git_path_keeps_persisted_head(tmp_path: Path) -> None:
    project = SimpleNamespace(
        workspace_path=str(tmp_path), head_commit="deadbeef", slug="p"
    )
    _root, head = _svc()._resolve(cast("ProjectTable", project), None)
    # A non-git legacy path must not clobber the persisted head_commit.
    assert head == "deadbeef"
    assert project.head_commit == "deadbeef"


def test_resolve_no_workspace_returns_none_root() -> None:
    project = SimpleNamespace(workspace_path=None, head_commit=None, slug="p")
    root, head = _svc()._resolve(cast("ProjectTable", project), None)
    assert root is None
    assert head == "HEAD"


def test_head_sha_at_non_git_returns_none(tmp_path: Path) -> None:
    assert ConventionsService._head_sha_at(tmp_path) is None
