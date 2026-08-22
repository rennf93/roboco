# PR Gate Fix - ruff format bounce on test_learning.py

**Finding F-9c13a5dd (blocker) - Resolved (PR #910)**

## Root Cause
`ruff format --check` failed on `tests/unit/services/test_learning.py` — 1 file would be reformatted. Three test-code spots added in the parent CELL-scope team-filter work were written multi-line where ruff collapses them to a single line (each fits within the 88-char line limit):

1. `_FakeAgentRow.__init__` signature (~line 349) — split across 3 lines; ruff wants `def __init__(self, *, id: UUID, role: AgentRole, team: str | None = None) -> None:`.
2. `backend_agent = _FakeAgentRow(...)` (~line 455) — split across 3 lines; ruff collapses to one.
3. `ux_agent = _FakeAgentRow(...)` — same multi-line split; ruff collapses to one.

The `frontend_agent` call site in the same test was correctly left multi-line: its single-line form would exceed the 88-char limit, so ruff keeps it expanded (no magic trailing comma).

## Solution Applied
Ran `ruff format tests/unit/services/test_learning.py`, auto-collapsing the three spots to single lines. No manual edits — the formatter is the source of truth for line-wrapping decisions. The diff is exactly three line-collapse hunks and nothing else.

## Impact
- **Scope:** Test-only, single file (`tests/unit/services/test_learning.py`). No production code touched.
- **Risk:** None — formatting-only change, no logic or assertion edits. The `_FakeAgentRow` helper, the `_CellScopeFakeDb` fake, and the `test_fetch_notify_agents_cell_scope_filters_by_team` test body are byte-for-byte identical apart from the three line collapses.
- **Behavior:** No production behavior change. No test-coverage change — the same calls construct the same objects with the same arguments.
- **Verification:** `make gate` green (`ruff format --check` + `ruff check` + `mypy` + `xenon` + `lint-imports`). `ruff format --check` reports 0 files would be reformatted on the file.

## Context
This is a revision bounce — the original CELL-scope team-filter fix and its unit test (parent task 3192e06f) are correct. This subtask only addresses the formatting gate failure that blocked the PR-gate finding, not the feature logic.