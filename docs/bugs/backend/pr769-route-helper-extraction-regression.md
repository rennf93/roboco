# Route-Helper-Extraction Refactor Silently Dropped Product Code (PR #769/#772)

## Description

An earlier route-helper-extraction refactor (Batch A task `4baffaa3`, Batch B task
`f8480831`) moved helper functions out of route files into their proper service-layer
modules per the Architectural Conventions Standard. During that extraction, the branch
silently **dropped**, rather than relocated, a substantial slice of pre-existing product
behavior. The follow-up PR (#769, later reopened as #772) bounced `needs_revision` twice
on `pr_gate` findings before the cause was correctly diagnosed: `sync_branch` on the
subtask returned `head==base tip` (no drift) both times, ruling out branch staleness and
confirming the content was genuinely missing from the branch, not just out of sync with
`main`.

## Root Cause

The extraction commit(s) removed the following without a corresponding relocation, each
verified against real call sites that would `NameError`/`AttributeError` at runtime:

- **`roboco/services/task.py`**: ~22 public `TaskService` methods — all 12
  `list_open_*_cycles` board-program queries, `list_sentinel_reports`,
  `list_periscope_briefs`, `sequence_hold_reason`, `task_spend_usd`,
  `project_month_spend_usd`, `terminal_children_count`, `self_heal_ac_ids`,
  `resolve_scales_task_ref`, `list_open_env_sync_tasks`, `_close_task_pr_best_effort`,
  `_inherit_upstream_base`, `list_completed_coroner_postmortems`, and the module-level
  `_fire_coroner_bounce_hook` plus its sibling `_fire_coroner_cancel_hook_if_work_started`.
  Callers spanned `coroner.py`, `sentinel.py`, `periscope.py`, `project.py`,
  `coroner_engine.py`, `content_actions.py`, `choreographer/_impl.py`, `orchestrator.py`,
  and every `*_engine.py` site calling a `list_open_*_cycles` method.
- **`roboco/services/video_engine.py`**: `reauthor_from_rejection`,
  `_open_video_task_locked`, `_resolve_reauthor_project`, plus supporting occasion-lock /
  AC scene-criterion / product-name-resolution code `test_video_engine.py` depends on.
- **`roboco/services/video_post_service.py`**: `_platform_configured`,
  `_reauthor_after_reject`, plus a CANCELLED-draft `approve()` guard and the
  unconfigured-platform-skip behavior `test_video_post_service.py` requires.
- **`roboco/services/a2a.py`**: `_maybe_wake_ceo_recipient`,
  `_ack_pending_wake_notifications` (the CEO-DM-wake path wired into `send_chat_message` /
  `interject_as_ceo` / `get_unread_messages`).
- **25 tests** deleted with zero replacements in `test_video_routes.py`,
  `test_tasks_routes.py`, `test_orchestrator_manual_spawn.py`, including
  security-relevant coverage: symlink-traversal confinement, non-CEO-forbidden checks,
  agent-id-traversal rejection, `budget_usd` validation.

## Solution Implemented

Every named method, hook, and test was reconstructed against its real callers and
pre-existing test expectations (not guessed) and restored in the same modules the
extraction had emptied them from:

- All ~22 `task.py` methods restored, including wiring `self_heal_ac_ids` into
  `_parent_ac_ref_sets` so the AC-coverage digest self-heals a legacy/drifted parent
  instead of staying permanently inert, and wiring the Coroner bounce hook
  (`CORONER_BOUNCE_THRESHOLD = 3`) into `_emit_status_transition_audit`'s
  revision-count-bump branch via a new `_schedule_coroner_bounce_hook` helper (kept
  separate so the scheduling try/except doesn't inflate that chokepoint's own
  complexity), plus the sibling `_fire_coroner_cancel_hook_if_work_started` into
  `cancel()`.
- `video_engine.py` / `video_post_service.py` / `a2a.py` methods restored with their
  supporting code, verified directly against each file's existing test suite.
- The 25 deleted tests restored, including the named security-relevant cases
  (`test_validated_agent_id_still_rejects_traversal`, symlink confinement,
  non-CEO-forbidden, `budget_usd` validation); pause/resume coverage in
  `test_tasks_routes.py` was confirmed already intact and not actually missing.
- `_finalize_claim`'s upstream-base-inherit condition was extracted into a pure
  `_should_inherit_upstream_base` helper: restoring the Coroner hooks pushed
  `_emit_status_transition_audit` / `cancel()` / `_finalize_claim` over the xenon
  complexity budget, so all three were brought back under threshold with identical
  behavior.

**Explicitly out of scope, left untouched:** `board_programs.py`, `coroner.py`,
`dogfood.py`, `github_app.py`, `mirror.py`, `periscope.py`, `pest_control.py`,
`scales.py`, `sentinel.py`, `spackle.py`, `telegram.py` route files — none are named in
the parent task's (`d41bbbf5`) file-enumeration scope. Their own unrelated
helper-placement findings were confirmed as a separate, later architectural-drift
surface and were not pulled into this diff.

## Impact

- **Scope:** Restoration only — no new product behavior beyond re-wiring the Coroner
  bounce/cancel hooks the extraction had silently dropped (that wiring was already
  documented at the architecture level in `CLAUDE.md`'s Board Program registry as "wired
  at TaskService's bounce/cancel chokepoints"; this PR is what makes that description
  true again on disk).
- **Risk:** The 27+ real call sites named above would have raised
  `NameError`/`AttributeError` at runtime had this shipped un-restored, and 3
  security-relevant test cases would have stayed uncovered.
- **Verification:** `mypy roboco/ tests/` clean (0 errors across 1438 files); `ruff
  format`/`check` clean; xenon complexity clean; the 3 originally-flagged test files
  plus `test_coroner_hooks.py` pass against a live sandbox Postgres (281+ tests); the
  full 656-test targeted regression suite passes with no new failures.

## Prevention Measures

A **placement-only** refactor (moving a definition to satisfy the Architectural
Conventions Standard) must be diffed against its source branch for **deletions**, not
just additions, before it is called done — `git diff --stat` on the refactor branch vs.
its base, or a simple count of `def ` occurrences per touched file pre/post, would have
caught this class of silent drop immediately. When a `pr_gate` finding claims code is
"missing" post-refactor, verify with `sync_branch` (or an equivalent stale-vs-real
check) before assuming the finding is a false-positive staleness artifact — in this
case the branch was never stale; the code was actually gone.
