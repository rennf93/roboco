"""Tests for _ensure_agent_owned: the agent's whole workspace must be writable.

The orchestrator clones as root, so the working tree lands root-owned. The
agent runs as uid 1000 and must be able to WRITE working-tree files (source,
design docs) and .git internals — so the whole workspace is chowned, EXCEPT the
large gitignored/agent-regenerated trees (node_modules, .venv, ...) which are
pruned to keep the walk fast. Restricting the walk to .git only (the previous
approach) left the working tree root-owned and broke every agent file write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.services import workspace as workspace_module
from roboco.services.workspace import _ensure_agent_owned

if TYPE_CHECKING:
    from pathlib import Path


def _build_workspace(root: Path) -> None:
    """Create a workspace: .git dir, working-tree source, and a heavy node_modules."""
    git_dir = root / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/master\n")
    (git_dir / "refs" / "heads" / "master").write_text("abc123\n")

    # Working tree the agent must be able to write.
    src = root / "roboco" / "services"
    src.mkdir(parents=True)
    (src / "thing.py").write_text("x = 1\n")
    (root / "README.md").write_text("# hi\n")

    # Heavy gitignored tree that must be pruned (not walked/chowned).
    node_modules = root / "node_modules"
    for pkg in range(20):
        pkg_dir = node_modules / f"pkg-{pkg}" / "dist"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "index.js").write_text("module.exports = {}\n")


@pytest.fixture
def _record_touched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every path _ensure_agent_owned tries to chown/chmod."""
    touched: list[str] = []

    def fake_chown_entry(entry: str) -> bool:
        touched.append(entry)
        return True

    def fake_make_rw(entry: str) -> None:
        touched.append(entry)

    monkeypatch.setattr(workspace_module, "_chown_entry", fake_chown_entry)
    monkeypatch.setattr(workspace_module, "_make_owner_and_group_rw", fake_make_rw)
    return touched


def test_chowns_working_tree_and_git_but_prunes_node_modules(
    tmp_path: Path, _record_touched: list[str]
) -> None:
    _build_workspace(tmp_path)

    _ensure_agent_owned(tmp_path)

    touched = set(_record_touched)

    # The workspace root must be chowned so the agent can create top-level files
    # (the EACCES that killed the run was the agent unable to mkdir/open here).
    assert str(tmp_path) in touched

    # Working-tree files the agent edits must be chowned — this is the exact
    # contract the .git-only regression broke.
    assert str(tmp_path / "roboco" / "services" / "thing.py") in touched
    assert str(tmp_path / "README.md") in touched

    # .git internals must still be chowned so git ops work.
    assert str(tmp_path / ".git" / "config") in touched
    assert str(tmp_path / ".git" / "refs" / "heads" / "master") in touched

    # node_modules must be PRUNED — not a single entry under it is touched
    # (walking it was the 2.7-15.5s/op cost the .git-only walk tried to avoid).
    assert not any("node_modules" in entry for entry in _record_touched)


def test_noop_when_workspace_absent(tmp_path: Path, _record_touched: list[str]) -> None:
    missing = tmp_path / "never_cloned"
    _ensure_agent_owned(missing)
    assert _record_touched == []
