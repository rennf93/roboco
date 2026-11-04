"""Guard the hard-wrap reflow gate: wiring into `make quality` + the check exit code.

Nothing previously pinned that `make quality` actually runs
`scripts/reflow_md.py --check` — a `Makefile` edit could silently drop the
line and no test would notice the regression. These tests pin the wiring and
the script's own pass/fail exit codes behind the I/O shell.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts" / "reflow_md.py"
MAKEFILE = ROOT / "Makefile"


def test_quality_target_wires_in_reflow_check() -> None:
    text = MAKEFILE.read_text()
    quality_block = text.split("\n.PHONY: quality\n", 1)[1].split("\n.PHONY: ", 1)[0]
    assert "scripts/reflow_md.py --check" in quality_block, (
        "make quality must run scripts/reflow_md.py --check "
        "(the hard-wrap reflow gate) — wiring was removed"
    )


def test_check_fails_on_a_hard_wrapped_file(tmp_path: Path) -> None:
    wrapped = tmp_path / "sample.md"
    wrapped.write_text(
        "This is a paragraph that has been\n"
        "hard-wrapped mid-sentence across two\n"
        "separate lines for no good reason.\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "hard-wrapped prose" in result.stdout


def test_apply_leaves_a_tool_managed_block_verbatim(tmp_path: Path) -> None:
    sample = tmp_path / "sample.md"
    sample.write_text(
        "A paragraph outside the block that\n"
        "is hard-wrapped and must be joined.\n"
        "\n"
        "<!-- some-tool:begin -->\n"
        "Line the tool owns.\n"
        "@another/line/the/tool/owns.md\n"
        "<!-- some-tool:end -->\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--apply"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sample.read_text() == (
        "A paragraph outside the block that is hard-wrapped and must be joined.\n"
        "\n"
        "<!-- some-tool:begin -->\n"
        "Line the tool owns.\n"
        "@another/line/the/tool/owns.md\n"
        "<!-- some-tool:end -->\n"
    )


def test_check_skips_the_tool_owned_entire_directory(tmp_path: Path) -> None:
    (tmp_path / ".entire").mkdir()
    (tmp_path / ".entire" / "agent-guide.md").write_text(
        "A tool-generated guide that has been\nhard-wrapped by the tool that owns it.\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


WRAPPED = (
    "This is a paragraph that has been\n"
    "hard-wrapped mid-sentence across two\n"
    "separate lines for no good reason.\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo_with_base_debt(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "debt.md").write_text(WRAPPED)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "checkout", "-b", "feature")
    return tmp_path


def _run_check(cwd: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check", *flags],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _repo_with_debt_on_slave(tmp_path: Path) -> Path:
    """master is clean; slave (ahead of master) carries debt; feature is off slave."""
    _git(tmp_path, "init", "-b", "master")
    (tmp_path / "ok.md").write_text("One clean line.\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    _git(tmp_path, "checkout", "-b", "slave")
    (tmp_path / "slave_debt.md").write_text(WRAPPED)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "slave debt")
    _git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "clean.md").write_text("Another clean line.\n")
    return tmp_path


def test_changed_mode_prefers_slave_over_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    repo = _repo_with_debt_on_slave(tmp_path)
    assert _run_check(repo, "--changed").returncode == 0


def test_changed_mode_uses_github_base_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_debt_on_slave(tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "master")
    result = _run_check(repo, "--changed")
    assert result.returncode == 1
    assert "slave_debt.md" in result.stdout
    monkeypatch.setenv("GITHUB_BASE_REF", "slave")
    assert _run_check(repo, "--changed").returncode == 0


def test_changed_mode_ignores_untouched_base_debt(tmp_path: Path) -> None:
    repo = _repo_with_base_debt(tmp_path)
    (repo / "clean.md").write_text("One clean line.\n")
    assert _run_check(repo, "--changed", "--base", "main").returncode == 0
    assert _run_check(repo).returncode == 1  # full-repo check still sees it


def test_changed_mode_fails_on_changed_wrapped_file(tmp_path: Path) -> None:
    repo = _repo_with_base_debt(tmp_path)
    (repo / "new.md").write_text(WRAPPED)
    result = _run_check(repo, "--changed", "--base", "main")
    assert result.returncode == 1
    assert "new.md" in result.stdout
    assert "debt.md" not in result.stdout


def test_changed_mode_fails_on_edited_tracked_file(tmp_path: Path) -> None:
    repo = _repo_with_base_debt(tmp_path)
    (repo / "debt.md").write_text(WRAPPED + "\nmore\n")
    assert _run_check(repo, "--changed", "--base", "main").returncode == 1


def test_changed_mode_respects_exclude_prefixes(tmp_path: Path) -> None:
    repo = _repo_with_base_debt(tmp_path)
    (repo / "docs" / "internal").mkdir(parents=True)
    (repo / "docs" / "internal" / "n.md").write_text(WRAPPED)
    assert _run_check(repo, "--changed", "--base", "main").returncode == 0


def test_reflow_is_idempotent() -> None:
    spec = importlib.util.spec_from_file_location("reflow_md", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    once = mod.reflow(WRAPPED)
    assert once != WRAPPED
    assert mod.reflow(once) == once


def test_check_skips_the_vendored_hyperframes_skill(tmp_path: Path) -> None:
    references = tmp_path / "motion" / "skills" / "hyperframes" / "references"
    references.mkdir(parents=True)
    (references / "runtimes.md").write_text(
        "Upstream prose that its authors have\nhard-wrapped, kept byte for byte here.\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
