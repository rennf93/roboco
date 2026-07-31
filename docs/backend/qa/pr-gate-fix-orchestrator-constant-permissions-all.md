# PR Gate Fix: orchestrator.py Constant Hoist + permissions.py `__all__`

**PR #747 revision findings — Resolved (PR #750)**

## Root Cause
Two `pr_gate` revision findings landed on unrelated files in the same review pass:

1. **`roboco/runtime/orchestrator.py:10943` (nit, PLR2004)** — `_MINUTES_PER_HOUR = 60` was declared inside the function body that formats an elapsed duration as `"Xh Ym"`, and was only used in the `total_minutes < _MINUTES_PER_HOUR` comparison. Two lines below, `total_minutes // 60` and `total_minutes % 60` still used the bare literal `60`, so ruff's magic-value rule kept flagging it.
2. **`roboco/services/permissions.py:48` (minor, F401)** — `__all__ = ["PM_ROLES"]` was added to silence an unused-re-export warning, but it narrowed the module's declared public surface below what callers actually import: `PermissionService`, `AgentContext`, and `TaskAction` are imported by name from `api/deps.py:44` and `routes/tasks.py:76`, alongside `is_pm_role`.

## Solution Applied
- **orchestrator.py**: hoisted `_MINUTES_PER_HOUR = 60` to module scope (next to the other Docker-config constants, e.g. `AGENT_NETWORK`, `AGENT_BASE_IMAGE`), and replaced both remaining bare-`60` literals (`//` and `%`) with the constant, so the comparison, floor-division, and modulo all reference the same named value.
- **permissions.py**: widened `__all__` to list the module's real public surface — `PM_ROLES`, `AgentContext`, `PermissionService`, `TaskAction`, `is_pm_role` — instead of dropping it, since the narrower alternative (removing `__all__` entirely) would have reintroduced the F401 warning on the `PM_ROLES` re-export.

## Impact
- **Scope:** Two files only (`roboco/runtime/orchestrator.py`, `roboco/services/permissions.py`); no changes to `.roboco/conventions.yml` or any other file, per the task's explicit scope boundary.
- **Risk:** Minimal — constant hoist and export-list widening, no behavior change to duration formatting or permission checks.
- **Behavior:** No change to the formatted duration string or to what names are importable from either module (both were already importable pre-fix; `__all__` only affects `from module import *` and static-analysis re-export checks).
- **Verification:** `make gate` (ruff format/check + mypy) clean on both files; no new lint suppressions introduced.

## Pattern
- When a "magic value" constant is hoisted to silence a lint rule, grep the surrounding block for every other bare use of the same literal — hoisting fixes only the flagged line, not sibling literals a few lines away.
- When `__all__` is added to fix a single re-export warning, check callers' import sites first (e.g. via `api/deps.py`, `routes/*.py`) — a narrow `__all__` can under-declare a module's real public surface even though the names remain importable directly.
