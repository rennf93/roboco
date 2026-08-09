# Round-3 pr_gate fix: CI reflow blocker + waived-path next_hint lie

PR #858 (branch `feature/backend/c2673793--486cd44f--297a1b4d`, the zero-diff PR-waiver feature) bounced round 3 of `pr_gate` review with two open findings, both fixed on the same branch by this task and shipped in PR #872 (commit `de9d3356`).

## F-93bc0414 (blocker): CI red on head commit a04f69f3

The round-3 finding speculated the Python quality gate was failing on ruff/mypy/xenon over the new `_verb_runner.py`/`_impl.py`/`test_pr_waiver.py` code from the round-1/round-2 fixes. Reproducing locally via `make gate`/`make quality` showed those checks were already fully clean — the real failure was `scripts/reflow_md.py --check` flagging hard-wrapped prose in `docs/backend/qa/pr-waiver-marker-latch-duplicate-fetch-fix.md`, the round-2 QA doc committed in PR #865.

**Fix:** `make reflow-docs`, a whitespace-only reflow of that doc (no content change).

## F-f72f238e (minor): next_hint lied on the PR-waived path

`submit_up`'s `next_hint` and `_next_hint_submit_root` (`roboco/foundation/policy/lifecycle.py`) were static strings claiming a PR had been opened and a reviewer would review it — true on the normal path, false on the PR-waived (zero-diff report-only) path, where no PR exists and the reviewer gate never fires. A waived PM following the envelope's `next` field would be pointed at a gate that never triggers.

**Fix:** both hint functions now branch on `markers.is_pr_waived(t)`:
- `submit_up`'s hint (moved from an inline lambda to a named `_next_hint_submit_up`, for parity with `_next_hint_submit_root`'s existing pattern) returns a waived-specific string naming `complete(task_id)` directly instead of `pr_pass`.
- `_next_hint_submit_root` does the same, naming `complete(task_id)` as the CEO-escalation path instead of the main reviewer.

## What shipped (commit `de9d3356`)

- `docs/backend/qa/pr-waiver-marker-latch-duplicate-fetch-fix.md`: reflow only.
- `roboco/foundation/policy/lifecycle.py`: new `_next_hint_submit_up`; `_next_hint_submit_root` gains the `markers.is_pr_waived(t)` branch; both wired into `_INTENT_VERBS`.
- Tests: `tests/unit/gateway/test_verb_runner.py`'s 4 existing waiver tests (`test_submit_up_waives_pr_on_zero_commit_branch`, `test_submit_root_waives_pr_on_zero_commit_branch`, `test_submit_up_still_creates_pr_when_branch_has_commits`, `test_submit_root_still_creates_pr_when_branch_has_commits`) each gained an assertion on the envelope's `next` field for both the waived and non-waived case.

## Full picture

See the "Zero-diff PR-waiver (report-only work)" and "PR-waiver marker un-latch + duplicate-fetch fix" sections in this repo's own `CLAUDE.md` for the base mechanism and round-2 hardening this task follows; `CLAUDE.md` now also names this round-3 `next_hint` fix.

## Scope note

Per the task's explicit instruction, this fix touched only the reflow of the pre-existing QA doc, `lifecycle.py`'s two `next_hint` functions, and their existing tests — no other pr_gate-flagged file was in scope for this round.
