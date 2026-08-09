# Round-2 pr_gate fix: PR_WAIVED one-way latch + duplicate is_behind_base fetch

PR #858 (branch `feature/backend/c2673793--486cd44f--297a1b4d`, the zero-diff PR-waiver feature) bounced round 2 of `pr_gate` review with two open findings, both fixed on the same branch by this task and shipped in PR #865.

## F-7ba05e51 (major): PR_WAIVED never cleared

`mark_pr_waived`/`is_pr_waived` (`roboco/foundation/policy/content/markers.py`) existed with no clear path anywhere in `roboco/`, so the marker was a one-way latch. Reachable sequence: a report-only task is waived (`ahead == 0`, no PR opened) and sits in `awaiting_pm_review`; the PM `request_changes`s it back to `needs_revision`; the cell lands real commits; `submit_up`/`submit_root` re-runs with `ahead > 0` and `create_pr`/ `create_root_pr` DOES open a real PR this round. The stale `pr_waived` marker then kept short-circuiting `TaskService.complete`'s work-session-merged check and the CEO-escalation `pr_number` check even though a real PR now existed.

**Fix:** `markers.clear_pr_waived(task)` (`clear_marker(task, PR_WAIVED)`), called from `VerbRunner._run_pre_side_effects` (`roboco/services/gateway/choreographer/_verb_runner.py`) the moment a `_PR_CREATION_SIDE_EFFECTS` item is NOT waived — i.e. whenever `create_pr`/`create_root_pr` is about to actually run because `ahead > 0`.

## F-bdfdce5d (minor): duplicate git fetch per submit

`is_behind_base` ran twice per `submit_up`/`submit_root` against the same resolved parent branch, each a full `git fetch origin`: once in `Choreographer._freshen_assembled_branch` (`_impl.py`, behind-base auto-sync, only reads `behind`) and again in `VerbRunner._maybe_waive_pr_creation` (only reads `ahead`).

**Fix:** `_assembled_submit_guards`/`_freshen_assembled_branch` now return `tuple[Envelope | None, int | None]` instead of just the envelope. The `ahead` half threads through `submit_up`/`submit_root` into `VerbRunner.run_intent`'s new `precomputed_ahead` kwarg, which `_maybe_waive_pr_creation` reuses instead of a second fetch — except when the probe returned `None` (no branch/base, probe error, or a rebase just ran), in which case the waiver check re-fetches fresh. A rebase can drop now-empty commits, so a pre-rebase `ahead` count is not guaranteed to still be correct against the post-rebase branch; that one re-fetch is deliberate and documented inline so a future reader doesn't "simplify" it away as redundant duplication.

## What shipped (commit `6aa685d8`)

- `roboco/foundation/policy/content/markers.py`: new `clear_pr_waived`.
- `roboco/services/gateway/choreographer/_verb_runner.py`: `run_intent` / `_run_pre_side_effects` / `_maybe_waive_pr_creation` all gain an optional `precomputed_ahead: int | None` parameter; `_run_pre_side_effects` calls `markers.clear_pr_waived` on the un-waived branch.
- `roboco/services/gateway/choreographer/_impl.py`: `_assembled_submit_guards` and `_freshen_assembled_branch` return `(Envelope | None, int | None)`; `_submit_up_run_intent`/the `submit_root` verb path thread the probed `ahead` through as `precomputed_ahead`.
- Tests: `tests/unit/gateway/test_verb_runner.py` gained 5 new cases — round-trip marker clear for `submit_up` and `submit_root`, an untouched-when-still-waived case, and two `precomputed_ahead` reuse cases. `tests/unit/gateway/test_assembled_branch_freshen.py` updated its assertions to the new tuple return shape.

## Full picture

For the base zero-diff PR-waiver mechanism this task hardens (detection, the `pr_waived` marker, the downstream gate exemptions), see the "Zero-diff PR-waiver (report-only work)" section in the repo's own `CLAUDE.md` — updated by this task with a new "PR-waiver marker un-latch + duplicate-fetch fix" paragraph immediately below it.

## Scope note

Per the task's explicit instruction, this fix touched only `markers.py`, `_verb_runner.py`, `_impl.py`, and their existing tests. The `is_behind_base` TAB-parse fix (a prior round's fix, covered by `tests/unit/services/test_git_is_behind_base.py`) was left untouched.
