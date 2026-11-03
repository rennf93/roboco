"""Auto-reflow of changed markdown at commit time.

Reuses the engine behind ``make reflow-docs`` (``scripts/reflow_md.py``) —
``reflow`` and ``in_scope`` are loaded from that file, never reimplemented, so
the commit path and the merge-gate ``--check`` can't drift apart.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

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
        and _reflow_file(engine, workspace / rel)
    ]


def _reflow_file(engine: ModuleType, target: Path) -> bool:
    """Rewrite ``target`` in place; True only when its content changed."""
    if not target.is_file():
        return False
    orig = target.read_text()
    new = engine.reflow(orig)
    if new == orig or orig.split() != new.split():
        return False
    target.write_text(new)
    return True
