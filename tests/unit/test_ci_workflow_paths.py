"""``.github/workflows/ci.yml`` runs on every file ``make quality`` reads.

Both CI triggers are path-filtered, and a commit that touches only files
outside the filters runs no CI at all: whatever gate it breaks stays red but
unseen until a later commit happens to fire a run. ``AGENTS.md``, for one,
carries the same rules as ``CLAUDE.md`` and is read by the same prose gate,
but sat outside the filters. These tests take the gate's inputs from the
tracked files (every Python file, which ruff, mypy and vulture read, and the
Markdown that ``scripts/reflow_md.py`` checks) and fail when one falls outside
the filters, or when the two triggers' filters drift apart.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

# tests/unit/test_ci_workflow_paths.py
#   parents[0] = unit
#   parents[1] = tests
#   parents[2] = <repo root>
ROOT = Path(__file__).parents[2]
_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
_REFLOW_MD = ROOT / "scripts" / "reflow_md.py"


@cache
def _triggers() -> dict[str, Any]:
    doc = cast("dict[Any, Any]", yaml.safe_load(_WORKFLOW.read_text()))
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1 rules.
    return cast("dict[str, Any]", doc[True])


def _paths(trigger: str) -> list[str]:
    return cast("list[str]", _triggers()[trigger]["paths"])


def _github_glob(pattern: str) -> re.Pattern[str]:
    """Compile a paths-filter pattern with GitHub's meaning of ``*`` and ``**``.

    ``*`` matches within one path segment and ``**`` across segments. The
    filters use no other glob syntax; refuse it rather than misread it.
    """
    assert not set(pattern) & set("?+[]!"), f"unsupported glob in {pattern!r}"
    parts = re.split(r"(\*\*|\*)", pattern)
    regex = "".join(
        ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
        for part in parts
    )
    return re.compile(rf"{regex}\Z")


def _uncovered(files: list[str]) -> list[str]:
    filters = [_github_glob(pattern) for pattern in _paths("push")]
    return [f for f in files if not any(p.match(f) for p in filters)]


@cache
def _tracked_files() -> list[str]:
    # A commit can only touch tracked files, so they are what a trigger must
    # cover; untracked scratch in a checkout never reaches CI.
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.splitlines()


def _prose_gate_in_scope() -> Callable[[Path], bool]:
    spec = importlib.util.spec_from_file_location("reflow_md", _REFLOW_MD)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Callable[[Path], bool]", module.in_scope)


def test_push_and_pull_request_filter_the_same_paths() -> None:
    # A fork PR only fires `pull_request`, a fleet branch push only `push`:
    # a path missing from either one leaves that route with no run.
    assert _paths("push") == _paths("pull_request")


def test_every_markdown_file_the_prose_gate_checks_fires_ci() -> None:
    in_scope = _prose_gate_in_scope()
    markdown = [f for f in _tracked_files() if f.endswith(".md") and in_scope(Path(f))]
    assert markdown, "found no Markdown in the prose gate's scope"
    assert _uncovered(markdown) == []


def test_every_python_file_fires_ci() -> None:
    python = [f for f in _tracked_files() if f.endswith(".py")]
    assert python, "found no tracked Python files"
    assert _uncovered(python) == []
