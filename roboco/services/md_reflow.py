"""Auto-reflow of changed markdown at commit time.

Reuses the engine behind ``make reflow-docs`` (``scripts/reflow_md.py``) —
``reflow`` and ``in_scope`` are loaded from that file, never reimplemented, so
the commit path and the merge-gate ``--check`` can't drift apart. The engine
stays pure text; every workspace read/write goes through ``workspace_git``
(the orchestrator never touches agent-controlled paths itself).
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.services.workspace_git import (
    read_workspace_file,
    run_as_workspace_user,
    write_workspace_file,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

_IO_TIMEOUT = 10.0

_ENGINE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "reflow_md.py"


@lru_cache(maxsize=1)
def _engine() -> ModuleType | None:
    """Load scripts/reflow_md.py by path; None when the script isn't shipped."""
    if not _ENGINE_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_roboco_reflow_md", _ENGINE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reflow_changed_markdown(workspace: Path, changed: list[str]) -> list[str]:
    """Reflow the in-scope ``*.md`` files among ``changed`` (repo-relative paths).

    ``workspace`` is the worktree root (the engine's scope rules are relative to
    it). Files that are missing, out of scope, already clean, or that would break
    the engine's token invariant are left untouched. Idempotent. Returns the
    repo-relative paths actually rewritten.
    """
    engine = _engine()
    if engine is None:
        return []
    return [
        rel
        for rel in changed
        if Path(rel).suffix == ".md"
        and engine.in_scope(Path(rel))
        and _reflow_file(engine, workspace, workspace / rel)
    ]


def _realpath(path: Path) -> str | None:
    """``path`` fully resolved, asked of the git user; None when unresolvable."""
    res = run_as_workspace_user("realpath", "--", str(path), timeout=_IO_TIMEOUT)
    return res.stdout.strip() if res.returncode == 0 else None


def _stays_inside(workspace: Path, target: Path) -> bool:
    """True when ``target`` resolves (symlinks followed) under ``workspace``."""
    root, real = _realpath(workspace), _realpath(target)
    return root is not None and real is not None and real.startswith(root + "/")


def _reflow_file(engine: ModuleType, workspace: Path, target: Path) -> bool:
    """Rewrite ``target`` in place; True only when its content changed."""
    if not _stays_inside(workspace, target):
        return False
    orig = read_workspace_file(target)
    if orig is None:
        return False
    new = engine.reflow(orig)
    if new == orig or orig.split() != new.split():
        return False
    write_workspace_file(target, new)
    return True


async def reflow_worktree_markdown(
    run_git: Callable[..., Awaitable[Any]],
    workspace: Path,
    files: list[str] | None,
    timeout: int,
    log: Any,
) -> list[str]:
    """Reflow the worktree's modified/untracked markdown before staging.

    ``run_git(workspace, args, check=, timeout=)`` is the caller's git runner.
    Best-effort: a failure is logged and returns ``[]``, never losing a commit.
    """
    try:
        result = await run_git(
            workspace,
            ["ls-files", "-m", "-o", "--exclude-standard", "--", "*.md"],
            check=False,
            timeout=timeout,
        )
        changed = [p for p in result.stdout.splitlines() if p]
        if files:
            changed = [p for p in changed if p in files]
        return reflow_changed_markdown(workspace, changed)
    except Exception as exc:
        log.warning("markdown reflow skipped", error=str(exc))
        return []
