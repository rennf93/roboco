"""The assembly-integrity check survives children whose branches origin deleted.

``unmerged_child_commits`` fetched the parent branch and every completed
child's branch in one ``git fetch origin <parent> <child>...``. A child
merged into the parent usually has its branch deleted on origin, and git
aborts the whole fetch on one missing ref, so ``origin/<parent>`` stayed at
whatever this clone last saw. The ``git cherry`` against that stale parent
then reported the merged child's commits as missing: a false "unmerged
child" on the PM's assembled branch. Real git end to end: a bare origin
through a real relay, a PM clone under the workspaces root.
"""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.git import GitService
from tests.unit.local_origin_relay import serve_origin_through_relay

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_PARENT = "feature/backend/root1234"
_MERGED_CHILD = "feature/backend/root1234--aaaa1111"
_OPEN_CHILD = "feature/backend/root1234--bbbb2222"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _child(branch: str) -> SimpleNamespace:
    short = branch.rsplit("--", 1)[1]
    return SimpleNamespace(
        id=f"{short}-0000-0000-0000-000000000000",
        title=f"child {short}",
        status="completed",
        branch_name=branch,
        commits=[{"sha": "x"}],
    )


@pytest.fixture
def pm_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The PM's clone saw the parent and both children before the merged
    child landed on the parent and its branch was deleted on origin."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    _git(dev, "commit", "-q", "--allow-empty", "-m", "base")
    _git(dev, "push", "-q", "origin", "main")
    _git(dev, "checkout", "-q", "-b", _PARENT)
    _git(dev, "push", "-q", "origin", _PARENT)
    for branch in (_MERGED_CHILD, _OPEN_CHILD):
        _git(dev, "checkout", "-q", "-b", branch, _PARENT)
        (dev / f"{branch.rsplit('--', 1)[1]}.txt").write_text("work\n")
        _git(dev, "add", ".")
        _git(dev, "commit", "-q", "-m", f"[{branch.rsplit('--', 1)[1]}] work")
        _git(dev, "push", "-q", "origin", branch)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-pm"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(dev, "checkout", "-q", _PARENT)
    _git(dev, "merge", "-q", "--no-ff", "-m", "merge child", _MERGED_CHILD)
    _git(dev, "push", "-q", "origin", _PARENT)
    _git(dev, "push", "-q", "origin", "--delete", _MERGED_CHILD)
    serve_origin_through_relay(monkeypatch, tmp_path, origin)
    return clone


def test_git_aborts_the_combined_fetch_on_the_deleted_child(pm_clone: Path) -> None:
    """The premise, on real git, in the PM's clone."""
    before = _git(pm_clone, "rev-parse", f"origin/{_PARENT}")
    fetched = subprocess.run(
        ["git", "fetch", "origin", _PARENT, _MERGED_CHILD, _OPEN_CHILD],
        cwd=pm_clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert fetched.returncode != 0
    assert "couldn't find remote ref" in fetched.stderr
    assert _git(pm_clone, "rev-parse", f"origin/{_PARENT}") == before


async def test_a_merged_child_whose_branch_was_deleted_is_not_unmerged(
    pm_clone: Path,
) -> None:
    task = SimpleNamespace(id=uuid4(), branch_name=_PARENT, assigned_to=None)
    svc = GitService(MagicMock())
    object.__setattr__(
        svc, "_project_for_task", AsyncMock(return_value=SimpleNamespace(slug="proj"))
    )
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=pm_clone))
    object.__setattr__(svc, "_get_project_token_or_raise", AsyncMock())
    tasks = MagicMock(
        get_subtasks=AsyncMock(
            return_value=[_child(_MERGED_CHILD), _child(_OPEN_CHILD)]
        )
    )

    with patch("roboco.services.git.get_task_service", return_value=tasks):
        missing = await svc.unmerged_child_commits(task, actor_agent_id=uuid4())

    assert [entry["title"] for entry in missing] == ["child bbbb2222"]


async def test_every_branch_origin_still_has_is_fetched(pm_clone: Path) -> None:
    """The helper create_branch also uses (base + default): a branch gone
    from origin no longer keeps the others from refreshing."""
    svc = GitService(MagicMock())
    origin_parent = _git(pm_clone, "ls-remote", "origin", _PARENT).split()[0]

    await svc._fetch_branches_origin_has(
        pm_clone, [_MERGED_CHILD, _PARENT, _OPEN_CHILD]
    )

    assert _git(pm_clone, "rev-parse", f"origin/{_PARENT}") == origin_parent
