# PR #840 Revision: Stale mock.patch Targets + CodeQL Investigation Trail

**PR #840 (route-helper extraction retry) pr_gate round-2 findings — task 069b3b67, shipped on PR #876**

## Root Cause

PR #840 bounced from `pr_gate` round 2 with two open blocker findings, both downstream of the same prior extraction round that moved `resolve_session_user` and `_require_ceo` out of `roboco/api/routes/orchestrator.py` into `roboco/api/utils/orchestrator.py`:

1. **F-401342e6 (mechanical)** — `tests/unit/api/test_orchestrator_auth.py` lines 265 and 291 still called `unittest.mock.patch("roboco.api.routes.orchestrator.resolve_session_user")`. That symbol no longer exists at the routes path, so both tests errored at patch time with `AttributeError` before ever reaching their assertions. `tests/unit/api/test_orchestrator_manual_spawn.py` had already been repointed to the real (`utils`) location in an earlier round on this same PR; `test_orchestrator_auth.py` was missed.
2. **F-ee25fd41 (CodeQL)** — the `Analyze` check was failing on PR #840's head commit. The reviewer flagging it had no GitHub API access to read the actual alert body, so the finding named two plausible causes to rule out: a stale merge-base inflating the diff (a known repo issue — CodeQL scans the whole language, not just the diff, so a stale base can make it attribute pre-existing code to the PR), or the new deferred/in-function imports the extraction introduced in `roboco/api/utils/board_programs.py`, `tasks.py`, `telegram.py`, and `v1_role_dep.py`.

## Solution Applied

- **`test_orchestrator_auth.py`**: repointed both `mock.patch` targets from `roboco.api.routes.orchestrator.resolve_session_user` to `roboco.api.utils.orchestrator.resolve_session_user`, matching the already-fixed `test_orchestrator_manual_spawn.py`. A full-file sweep for any other `roboco.api.routes.orchestrator`-scoped patch target found none — the module-level `from roboco.api.routes.orchestrator import router` import at line 22 is still valid (that symbol never moved) and was left untouched.
- **CodeQL (F-ee25fd41)**: `sync_branch(task_id)` was run first per the task instructions and returned `"superseded"` — the branch was already fully up to date with its base, ruling out a stale merge-base as the cause. With no GitHub API/CodeQL-alert access available in this environment, the dev fell back to exhaustive local static analysis instead of a direct alert read: `make gate` (ruff format/check, mypy, xenon, lint-imports) was clean, `bandit -r roboco/ -ll` reported 0 High-severity findings, and `pyproject.toml` was checked directly — all four files the finding named (`board_programs.py`, `tasks.py`, `telegram.py`, `v1_role_dep.py`) already carry legitimate `PLC0415` (deferred-import) per-file-ignores, meaning the deferred-import pattern the finding speculated about is pre-existing and already suppressed at the linter level, not new uncovered code this extraction introduced. No code change was made for F-ee25fd41 because nothing actionable was found locally.

## Impact

- **Scope:** One test file only (`tests/unit/api/test_orchestrator_auth.py`, 2 patch-target lines). No `@router` handlers, path constants, or response schemas were touched, per the task's explicit boundary.
- **Behavior:** No production behavior change — this is a test-only fix. The two affected tests now actually exercise their assertions instead of erroring at patch setup.
- **Risk / open item:** F-ee25fd41's real GitHub CodeQL check-run status was not directly verified — that access wasn't available to the dev in this environment. The investigation trail above (sync_branch superseded, `make gate` clean, bandit clean, PLC0415 waivers already in place) is the diligence this task's environment allows; QA's pass on the task explicitly named this as "as complete as this environment allows, with the real gate downstream" — the in-path PR-review gate's own CI-status check is the next real verification point for the CodeQL check-run itself.

## Round-3 re-verification (task cd3fbc67)

Both findings were re-verified after the round-2 fix subtask (069b3b67/PR #876) merged into the parent branch:

- **F-401342e6**: `test_orchestrator_auth.py` lines 265 and 291 both point at `roboco.api.utils.orchestrator.resolve_session_user` (confirmed by reading the file). `uv run pytest tests/unit/api/test_orchestrator_auth.py -q --no-cov` passes 10/10. A grep across `tests/` for `roboco.api.routes.*.(_require_ceo|resolve_session_user|_validated_agent_id|_build_manual_spawn_prompt|_resolve_manual_spawn_prompt|_to_response|_require_board)` found zero matches — the sweep is clean.
- **F-ee25fd41**: `sync_branch(task_id, stash=True)` returned `"superseded"` (branch already at base — no stale merge-base). The CodeQL workflow (`.github/workflows/codeql-js-ts.yml`) scans **only JavaScript/TypeScript** (`panel/**` paths), not Python — so the deferred/in-function imports in `roboco/api/utils/board_programs.py`, `tasks.py`, `telegram.py`, and `v1_role_dep.py` that the finding speculated about are **not what CodeQL is scanning**. The Python deferred-import pattern is already suppressed via `PLC0415` per-file-ignores in `pyproject.toml`. `bandit -r roboco/api/utils/ -ll` reports 0 issues. Individual gate components (`ruff format --check`, `ruff check`, `mypy`, `xenon`, `lint-imports`) all pass clean. The real CodeQL failure (if still present on PR #840's head) is in the JS/TS analysis of `panel/**` code changes, outside this backend extraction task's scope and file set.

## Pattern

- When a helper/symbol relocation lands across a multi-round PR, grep the **whole** test suite for the old dotted path, not just the file most recently touched — `test_orchestrator_manual_spawn.py` was fixed in an earlier round while a sibling file (`test_orchestrator_auth.py`) referencing the same moved symbol was missed.
- When a CodeQL finding can't be read directly (no GitHub API access), `sync_branch` first to rule out the stale-merge-base false-positive class this repo has hit before, then treat `make gate` + `bandit -r roboco/ -ll` + an existing-suppression check (`pyproject.toml` per-file-ignores) as the environment's best-effort diligence — and say so explicitly in the handoff rather than implying the alert was verified resolved.
- Check which CodeQL language the workflow actually analyzes before speculating about Python-side causes — this repo's only CodeQL workflow scans `panel/**` (JS/TS), so Python deferred imports are never the root cause.
