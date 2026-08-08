# Zero-Diff PR-Waiver

Gives Cell PMs (`submit_up`) and the Main PM (`submit_root`) a legitimate no-diff completion path for report-only work — a project-bound task assembled entirely from children that produced no code diff (an audit/findings subtree, e.g. work the Board Program catalog's Sentinel/Periscope/Coroner cycles generate). Before this, both verbs ran PR creation unconditionally, so a zero-commit branch 422'd against GitHub's "No commits between" and the task could only reach `completed` via manual status surgery.

## Overview

`submit_up` and `submit_root` open an assembled PR (`create_pr` / `create_root_pr`) as a `pre_side_effect` before composing `submit_for_review`. A **PR waiver** detects, ahead of that call, that the task's branch carries zero commits relative to its resolved parent branch, and reroutes the whole verb around PR creation and the PR-review gate instead of letting the GitHub call fail.

This is **distinct from `is_branchless_coordination`** (no branch at all — a MegaTask umbrella or a branchless coordination root). A PR-waived task has a real branch; it's just empty relative to its parent. The existing developer-only empty-diff steer at `open_pr` (`_impl.py:2103`, which tells a leaf dev to `i_am_blocked` so a PM cleans up) is a different actor hitting a related condition and is untouched by this feature.

## Detection: `VerbRunner._maybe_waive_pr_creation`

**Location:** `roboco/services/gateway/choreographer/_verb_runner.py`

Runs inside `VerbRunner._run_pre_side_effects`, intercepted for exactly two pre-side-effect names — `create_pr` and `create_root_pr` (`_PR_CREATION_SIDE_EFFECTS`):

1. Resolve the task's parent branch via `resolve_parent_branch` (`merge_chain.py`).
2. Call `GitService.is_behind_base(task, base_branch=parent, ...)` and read its `ahead` count.
3. `ahead == 0` → waive: stamp the marker, write the transition note + a progress entry, and skip the `create_pr`/`create_root_pr` call entirely.
4. `ahead > 0` → proceed normally; no waiver, no behavior change.
5. Any exception (network blip, missing workspace, unresolvable parent) → **fail open**: treat as not-waived and let the normal `create_pr`/`create_root_pr` attempt run and surface its own error. A flaky check must never silently waive a PR that a retry would have created fine. This path logs a `structlog` warning (task id, branch, exception) before returning, so a persistently broken workspace/git no longer degrades back to the GitHub 422 path with no trace of the skipped waiver check.

The detection happens **before** the GitHub call is attempted — not as post-hoc handling of the 422 — so a waived task never makes the doomed API call at all.

## Threading the waiver through the verb

`VerbRunner.run_intent` now splits into `_run_pre_side_effects` (returns whether PR creation was waived) and `_run_composed_actions` (consumes that flag), both extracted from the original single method to keep it within the xenon complexity gate.

When `pr_waived=True`, the composed `submit_for_review` action — which would otherwise transition `in_progress → awaiting_pr_review`, the in-path gate a reviewer diffs — reroutes to `submit_pm_review` (`in_progress → awaiting_pm_review`) instead: the same gate-skip a branchless coordination root already uses via `main_pm_complete`.

## The `pr_waived` marker

**Location:** `roboco/foundation/policy/content/markers.py`

- `PR_WAIVED = "pr_waived"` — the marker key.
- `mark_pr_waived(task)` — sets the marker on waiver.
- `is_pr_waived(task) -> bool` — read helper, consulted by every downstream gate below.
- `PR_WAIVED_TRANSITION_EVENT = "pr_waived"` — the structured transition-note event name; the note text names the branch and its resolved parent so a reviewer/PM sees why no PR exists, e.g.:

  > `PR creation waived: branch 'feature/backend/...' has zero commits relative to its parent branch '...' — report-only work with no diff to review.`

This rides the existing `TRANSITION_NOTES` marker infrastructure, and a matching entry is also written via `TaskService.add_progress` so it shows on the task's Progress tab, not just its transition history.

## Downstream gate exemptions

Every PR-required gate a normal task would hit on its way to `completed` now also accepts `is_pr_waived(task)` as an alternative to having a real `pr_number`, mirroring how each of them already treats a MegaTask umbrella:

| Gate | File | What changed |
|---|---|---|
| `submit_pm_review` PR-created check | `roboco/services/task.py` | `(not pr_created or not pr_number) and not (is_umbrella or pr_waived)` — a waived task no longer needs `pr_created`/`pr_number` to advance. |
| Cell-complete merge guard | `roboco/services/gateway/choreographer/_impl.py` | `t.pr_number is None and not markers.is_pr_waived(t)` — a waived task is allowed through with no PR to merge. |
| `TaskService.complete`'s work-session-merged check | `roboco/services/task.py` | Short-circuits `True` for a `pr_waived` task before checking `work_session_id` at all — there is no PR to have merged. |
| CEO-escalation `pr_number` gate | `roboco/enforcement/task_lifecycle.py`, `GitContext.is_pr_waived` | `_check_ceo_escalation_gate` no longer raises for `awaiting_pm_review → awaiting_ceo_approval` when `is_pr_waived` is set, alongside the pre-existing `is_umbrella` exemption. |

`TaskService._git_context_for` populates `GitContext.is_pr_waived` from `markers.is_pr_waived(task)` on every transition-gate check, so all of the above read one consistent, marker-derived source of truth.

## Bug fix that unblocked this: `GitService.is_behind_base`

**Location:** `roboco/services/git.py`

`git rev-list --left-right --count` separates its two counts with a **TAB**, not a space. The pre-existing parse used `.partition(" ")`, which never found a match and silently fell through to `behind=0, ahead=0` on every real branch. This was harmless for the method's only pre-existing callers (which only read `behind`, where a false "0 behind" just skipped an unneeded freshen) but would have made the zero-diff detection above **false-positive on every non-empty branch** — waiving PR creation for branches that genuinely had commits. Fixed to `.split()` (whitespace-agnostic), matching the already-correct sibling parse in `_ahead_behind`.

**This is a bigger behavior change than the PR-waiver acceptance criteria describe.** Two pre-existing `is_behind_base` callers were previously reading a permanently-false "0 behind" against real git output and are now genuinely activated by this fix: the assembled-PM-submit freshen check (`_impl.py:2807`, `_freshen_assembled_branch_if_behind`, the docstring right above it) and the `i_am_done` behind-base submit gate (`_impl.py:2912`). Both existed before this PR and were silently inert — this fix makes them actually fire on a genuinely-behind branch for the first time, which is a correctness fix, not new functionality.

## Tests

- `tests/unit/gateway/test_verb_runner.py` — unit coverage for the waiver detection, the `submit_for_review` → `submit_pm_review` reroute, the fail-open-on-git-error path, and a **regression test proving the existing non-empty-diff behavior of `submit_up`/`submit_root` is unchanged** (a real `ahead > 0` branch still calls `create_pr`/`create_root_pr` and lands on `awaiting_pr_review` as before).
- `tests/e2e_smoke/test_pr_waiver.py` — reproduces the live incident end to end: a project-bound cell task with a real branch and zero commits, assembled from report-only children, completes fully to `completed` with no PR and no human status surgery.

## Related Files

- **Detection + routing:** `roboco/services/gateway/choreographer/_verb_runner.py`
- **Marker:** `roboco/foundation/policy/content/markers.py`
- **Gate exemptions:** `roboco/services/task.py`, `roboco/services/gateway/choreographer/_impl.py`, `roboco/enforcement/task_lifecycle.py`
- **Parsing fix:** `roboco/services/git.py` (`GitService.is_behind_base`)
- **Tests:** `tests/unit/gateway/test_verb_runner.py`, `tests/e2e_smoke/test_pr_waiver.py`
