"""Tests that _ensure_agent_owned only touches the .git subtree.

The agent only needs write ownership on .git/ (index.lock, refs, packed-refs,
objects) during git ops. Walking the entire working tree — including a large
node_modules/ — chown+chmod'ing every entry made every git op take seconds.
The walk must be scoped to .git only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from roboco.services import workspace as workspace_module
from roboco.services.workspace import _ensure_agent_owned


def _build_workspace(root: Path) -> None:
    """Create a workspace with a .git dir and a large node_modules tree."""
    git_dir = root / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "refs" / "heads" / "main").write_text("abc123\n")
    (git_dir / "packed-refs").write_text("# pack-refs\n")

    # A large working tree with a deep node_modules/ that must NOT be walked.
    src = root / "src"
    src.mkdir(parents=True)
    (src / "main.py").write_text("print('hi')\n")
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


def test_ensure_agent_owned_scopes_to_git_only(
    tmp_path: Path, _record_touched: list[str]
) -> None:
    _build_workspace(tmp_path)

    _ensure_agent_owned(tmp_path)

    git_dir = tmp_path / ".git"
    assert _record_touched, "expected .git entries to be touched"

    # Every touched path must live inside .git/.
    for entry in _record_touched:
        resolved = Path(entry).resolve()
        assert git_dir.resolve() in (resolved, *resolved.parents), (
            f"{entry} is outside the .git subtree"
        )

    # No node_modules path may be touched.
    assert not any("node_modules" in entry for entry in _record_touched)

    # The git internals that need agent ownership were in fact visited.
    expected = {
        str(git_dir / "config"),
        str(git_dir / "HEAD"),
        str(git_dir / "packed-refs"),
        str(git_dir / "refs" / "heads" / "main"),
    }
    assert expected.issubset(set(_record_touched))


def test_ensure_agent_owned_noop_when_git_absent(
    tmp_path: Path, _record_touched: list[str]
) -> None:
    # Working tree with no .git/ — nothing to own.
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x\n")

    _ensure_agent_owned(tmp_path)

    assert _record_touched == []
