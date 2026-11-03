"""Commit-time markdown auto-reflow reuses scripts/reflow_md.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.md_reflow import (
    reflow_changed_markdown,
    reflow_worktree_markdown,
)

WRAPPED = "first line of a\nhard wrapped paragraph\n"
FLAT = "first line of a hard wrapped paragraph\n"


def _check_passes(root: Path) -> bool:
    """Mirror `reflow_md.py --check` over `root` using the real engine."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "reflow_md.py"
    spec = importlib.util.spec_from_file_location("_chk", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return all(
        mod.reflow(p.read_text()) == p.read_text()
        for p in root.rglob("*.md")
        if mod.in_scope(p.relative_to(root))
    )


def test_reflows_changed_wrapped_file(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(WRAPPED)
    assert reflow_changed_markdown(tmp_path, ["a.md"]) == ["a.md"]
    assert (tmp_path / "a.md").read_text() == FLAT
    assert _check_passes(tmp_path)


def test_idempotent(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(WRAPPED)
    reflow_changed_markdown(tmp_path, ["a.md"])
    assert reflow_changed_markdown(tmp_path, ["a.md"]) == []
    assert (tmp_path / "a.md").read_text() == FLAT


def test_noop_when_nothing_changed(tmp_path: Path) -> None:
    assert reflow_changed_markdown(tmp_path, []) == []
    assert reflow_changed_markdown(tmp_path, ["gone.md", "x.py"]) == []


def test_ignores_unlisted_and_out_of_scope(tmp_path: Path) -> None:
    (tmp_path / "unchanged.md").write_text(WRAPPED)
    (tmp_path / "docs" / "internal").mkdir(parents=True)
    (tmp_path / "docs" / "internal" / "n.md").write_text(WRAPPED)
    out = reflow_changed_markdown(tmp_path, ["docs/internal/n.md"])
    assert out == []
    assert (tmp_path / "unchanged.md").read_text() == WRAPPED
    assert (tmp_path / "docs" / "internal" / "n.md").read_text() == WRAPPED


@pytest.mark.asyncio
async def test_git_hook_filters_to_requested_files_and_never_raises(
    tmp_path: Path,
) -> None:
    log = MagicMock()
    (tmp_path / "a.md").write_text(WRAPPED)
    (tmp_path / "b.md").write_text(WRAPPED)
    run = AsyncMock(return_value=MagicMock(stdout="a.md\nb.md\n"))
    assert await reflow_worktree_markdown(run, tmp_path, ["a.md"], 5, log) == ["a.md"]
    assert (tmp_path / "a.md").read_text() == FLAT
    assert (tmp_path / "b.md").read_text() == WRAPPED

    run.side_effect = RuntimeError("boom")
    assert await reflow_worktree_markdown(run, tmp_path, None, 5, log) == []
    log.warning.assert_called_once()


def test_symlinked_markdown_pointing_outside_the_clone_is_not_written(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text(WRAPPED)
    (clone / "link.md").symlink_to(outside)
    assert reflow_changed_markdown(clone, ["link.md"]) == []
    assert outside.read_text() == WRAPPED
    assert (clone / "link.md").is_symlink()
