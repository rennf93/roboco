"""Tests for _ensure_agent_owned: the agent's whole workspace must be writable.

The orchestrator clones as root, so the working tree lands root-owned. The
agent runs as uid 1000 and must be able to WRITE working-tree files (source,
design docs) and .git internals — so the whole workspace is chowned, EXCEPT the
large gitignored/agent-regenerated trees (node_modules, .venv, ...) which are
pruned to keep the walk fast. Restricting the walk to .git only (the previous
approach) left the working tree root-owned and broke every agent file write.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from roboco.services import workspace as workspace_module
from roboco.services.workspace import (
    _AGENT_GID,
    _AGENT_UID,
    _ensure_agent_owned,
    _own_and_grant_rw,
)


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

    def fake_chown_entry(entry: str, _st: os.stat_result) -> bool:
        touched.append(entry)
        return True

    def fake_make_rw(entry: str, _st: os.stat_result) -> None:
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


def test_chown_failure_falls_back_to_chmod_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rootless/userns hosts reject chown. `_own_and_grant_rw` must still run
    the chmod fallback (belt-and-suspenders for ACL-inheriting NAS volumes)
    and `_ensure_agent_owned` must warn rather than swallow the failure
    silently. Unchanged by the scoped-repair split — this exercises the real
    (non-git-scoped) chown/chmod primitives via `_chown_entry`, forced to
    fail regardless of the test process's actual uid/gid."""
    (tmp_path / "file.py").write_text("x = 1\n")

    chmod_calls: list[str] = []
    monkeypatch.setattr(workspace_module, "_chown_entry", lambda _entry, _st: False)
    monkeypatch.setattr(
        workspace_module,
        "_make_owner_and_group_rw",
        lambda entry, _st: chmod_calls.append(entry),
    )
    warning_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        workspace_module.logger,
        "warning",
        lambda msg, **kw: warning_calls.append((msg, kw)),
    )

    _ensure_agent_owned(tmp_path)

    # chmod fallback still ran for every entry despite the chown failure.
    assert str(tmp_path / "file.py") in chmod_calls
    # The failure is surfaced, not swallowed.
    assert warning_calls
    assert warning_calls[0][1]["failures"]


# ---------------------------------------------------------------------------
# `_own_and_grant_rw`: one shared stat now backs both the chown-needed and
# chmod-needed checks (previously two separate `Path.stat()` calls, one per
# helper) — the fix for chown_ms: 39502 on the production NAS, where every
# chown/chmod is a copy-on-write metadata write and the tree is almost always
# ALREADY correctly owned on a re-claim. These exercise the real (unmocked)
# `_own_and_grant_rw` / `_chown_entry` / `_make_owner_and_group_rw` at the
# os-syscall boundary.
# ---------------------------------------------------------------------------


def _stat_result(mode: int, uid: int = 0, gid: int = 0) -> os.stat_result:
    """A real ``os.stat_result`` exposing only the fields the ownership
    helpers read (st_mode/st_uid/st_gid) — no filesystem entry needed."""
    return os.stat_result((mode, 0, 0, 0, uid, gid, 0, 0, 0, 0))


def _fake_stat(result: os.stat_result) -> object:
    """A stand-in for ``os.stat`` accepting the ``(path, *, follow_symlinks)``
    signature ``Path(entry).stat()`` actually calls it with underneath."""

    def _stat(_path: object, **_kwargs: object) -> os.stat_result:
        return result

    return _stat


_ALREADY_RW_MODE = (
    stat_module.S_IFREG
    | stat_module.S_IRUSR
    | stat_module.S_IWUSR
    | stat_module.S_IRGRP
    | stat_module.S_IWGRP
)
_ROOT_NARROW_MODE = stat_module.S_IFREG | stat_module.S_IRUSR | stat_module.S_IWUSR


def test_own_and_grant_rw_skips_syscalls_when_already_correct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already agent-owned, already rw entry costs one stat and ZERO
    metadata-write syscalls — the no-op-write cost that stacked to
    chown_ms: 39502 across tens of thousands of files when the tree was
    already correctly owned, as it almost always is on a re-claim."""
    chown_mock = MagicMock()
    chmod_mock = MagicMock()
    monkeypatch.setattr(
        workspace_module.os,
        "stat",
        _fake_stat(_stat_result(_ALREADY_RW_MODE, _AGENT_UID, _AGENT_GID)),
    )
    monkeypatch.setattr(workspace_module.os, "chown", chown_mock)
    monkeypatch.setattr(workspace_module.os, "chmod", chmod_mock)

    failed = _own_and_grant_rw("/fake/already-owned")

    assert failed == 0
    chown_mock.assert_not_called()
    chmod_mock.assert_not_called()


def test_own_and_grant_rw_still_repairs_a_wrong_owned_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely wrong-owned entry (fresh root-side clone/fetch/checkout
    output — root uid/gid, no group-write bit) still gets chowned AND
    chmodded exactly as before the single-stat merge."""
    chown_mock = MagicMock()
    chmod_mock = MagicMock()
    monkeypatch.setattr(
        workspace_module.os, "stat", _fake_stat(_stat_result(_ROOT_NARROW_MODE))
    )
    monkeypatch.setattr(workspace_module.os, "chown", chown_mock)
    monkeypatch.setattr(workspace_module.os, "chmod", chmod_mock)

    failed = _own_and_grant_rw("/fake/root-owned")

    assert failed == 0
    chown_mock.assert_called_once_with("/fake/root-owned", _AGENT_UID, _AGENT_GID)
    expected_mode = _ROOT_NARROW_MODE | stat_module.S_IRGRP | stat_module.S_IWGRP
    # chmod runs via Path(entry).chmod(...), which calls os.chmod with a
    # Path-wrapped first arg + follow_symlinks=True — not the bare string.
    chmod_mock.assert_called_once_with(
        Path("/fake/root-owned"), expected_mode, follow_symlinks=True
    )


def test_own_and_grant_rw_chown_failure_still_counted_and_chmod_still_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged failure-counting contract: a rejected chown (rootless /
    userns host) still counts as one failure AND the chmod best-effort
    fallback still runs (belt + suspenders for ACL-inheriting NAS volumes)."""
    monkeypatch.setattr(
        workspace_module.os, "stat", _fake_stat(_stat_result(_ROOT_NARROW_MODE))
    )

    def _raise_chown(*_args: object, **_kwargs: object) -> None:
        raise OSError("Operation not permitted")

    chmod_mock = MagicMock()
    monkeypatch.setattr(workspace_module.os, "chown", _raise_chown)
    monkeypatch.setattr(workspace_module.os, "chmod", chmod_mock)

    failed = _own_and_grant_rw("/fake/rootless-host")

    assert failed == 1
    chmod_mock.assert_called_once()


def test_own_and_grant_rw_broken_symlink_counts_as_failure_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symlink decision: the shared stat call follows symlinks — matching
    os.chown/os.chmod's own default follow behavior, unchanged from before
    the merge (the old code's two separate `Path(entry).stat()` calls also
    followed). A dangling symlink's stat raises OSError exactly as it did
    from inside the old `_chown_entry`'s own stat call: counted as one
    chown failure, and chmod is never attempted — the old chmod path hit
    the identical OSError from its own separate stat call and silently
    swallowed it, so the net effect (one counted failure, no chmod) is
    unchanged."""
    broken_link = tmp_path / "dangling"
    broken_link.symlink_to(tmp_path / "does-not-exist")
    chown_mock = MagicMock()
    chmod_mock = MagicMock()
    monkeypatch.setattr(workspace_module.os, "chown", chown_mock)
    monkeypatch.setattr(workspace_module.os, "chmod", chmod_mock)

    failed = _own_and_grant_rw(str(broken_link))

    assert failed == 1
    chown_mock.assert_not_called()
    chmod_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Root-sentinel short-circuit: `_ensure_agent_owned` skips the ENTIRE walk
# when the workspace root is already agent-owned with the right bits AND a
# `.git/roboco-owned` marker from the last zero-failure pass exists. The
# marker is invalidated by `GitService._run_git`
# (tests/unit/services/test_git_ownership_scope.py) before any root-side git
# write, so a live marker is trustworthy — this only removes the remaining
# per-entry-stat cost the earlier collapse (above) couldn't, the walk itself.
# ---------------------------------------------------------------------------

_OWNED_DIR_MODE = (
    stat_module.S_IFDIR
    | stat_module.S_IRUSR
    | stat_module.S_IWUSR
    | stat_module.S_IXUSR
    | stat_module.S_IRGRP
    | stat_module.S_IWGRP
    | stat_module.S_IXGRP
)


def _fake_root_owned_stat(root: Path) -> object:
    """Real ``os.stat`` for every path except ``root``, which reports as
    agent-owned with the required rw+x bits. Lets the marker file's own
    existence check (``Path.is_file()``, which also routes through
    ``os.stat``) reflect the real filesystem instead of a blanket fake."""
    real_stat = os.stat

    def _stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == root:
            return _stat_result(_OWNED_DIR_MODE, _AGENT_UID, _AGENT_GID)
        return real_stat(path, follow_symlinks=follow_symlinks)

    return _stat


def test_skips_walk_when_root_owned_and_marker_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _record_touched: list[str]
) -> None:
    _build_workspace(tmp_path)
    (tmp_path / ".git" / "roboco-owned").touch()
    monkeypatch.setattr(workspace_module.os, "stat", _fake_root_owned_stat(tmp_path))
    walk_mock = MagicMock(return_value=iter(()))
    monkeypatch.setattr(workspace_module.os, "walk", walk_mock)

    _ensure_agent_owned(tmp_path)

    walk_mock.assert_not_called()
    assert _record_touched == []


def test_full_walk_when_marker_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _record_touched: list[str]
) -> None:
    """Root already agent-owned, but no marker: the last pass over this
    clone is unattested, so the real walk still runs."""
    _build_workspace(tmp_path)
    monkeypatch.setattr(workspace_module.os, "stat", _fake_root_owned_stat(tmp_path))

    _ensure_agent_owned(tmp_path)

    assert str(tmp_path) in _record_touched
    assert str(tmp_path / "README.md") in _record_touched


def test_marker_written_only_on_zero_failure_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_workspace(tmp_path)
    marker = tmp_path / ".git" / "roboco-owned"
    assert not marker.exists()

    # Simulate a rootless/userns host where chown is rejected — the marker
    # must NOT land when any entry's chown fails. Mocking _chown_entry avoids
    # relying on the process uid (the test runs as uid 1000 and files are
    # already owned by uid 1000, so a real chown would be a no-op success).
    monkeypatch.setattr(workspace_module, "_chown_entry", lambda _entry, _st: False)
    _ensure_agent_owned(tmp_path)
    assert not marker.exists()


def test_marker_written_after_successful_pass(
    tmp_path: Path, _record_touched: list[str]
) -> None:
    """`_record_touched`'s fakes report every chown/chmod as succeeding, so
    this exercises the zero-failure branch without needing real root."""
    _build_workspace(tmp_path)
    marker = tmp_path / ".git" / "roboco-owned"

    _ensure_agent_owned(tmp_path)

    assert marker.is_file()
    assert _record_touched  # the walk actually ran (no marker existed yet)
