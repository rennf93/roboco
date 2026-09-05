# Real-DB lifecycle test: merge/complete + CEO-approval coverage

## What changed

`tests/integration/test_full_lifecycle_real_db.py` used to stop at `i_documented -> awaiting_pm_review` (the file's own TODO named the gap). It now carries two more tests that drive the REAL `Choreographer` + `TaskService` against a real Postgres DB all the way through `cell_pm` merge-complete, the in-path PR-review gate, `main_pm` merge-complete, and `TaskService.ceo_approve`, reaching `completed`:

- `test_cell_and_root_reach_completed_via_gate_and_ceo_approval` — the happy path: `submit_up` -> `claim_gate_review`/`pr_pass` (cell->root gate) -> cell PM `complete` -> `submit_root` -> `claim_gate_review`/`pr_pass` (root->master gate) -> main PM `complete` -> `ceo_approve`, asserting `completed` with a real `WorkSessionTable` row's `pr_status == "merged"`.
- `test_pr_fail_on_submit_up_then_resubmit_reaches_completed` — the reject path: `submit_up` -> `pr_fail` -> `needs_revision` -> `i_will_plan` re-claim -> resubmit with `resolved_findings` -> `pr_pass` -> `complete`, reaching `completed`.

The TODO comment that used to sit at line 574 is gone.

## Why the old `_StubGit` wasn't enough

The file's existing `_StubGit` binds to exactly ONE `TaskTable` row (set at construction). Driving the cell->root and root->master gate stages needs two independently-tracked PRs — one per assembled task (the cell task and a newly-seeded root task) — so a single-task stub can't represent both at once.

`_MultiTaskStubGit` generalizes the pattern: it's constructed with a `{branch_name: TaskTable}` map and every method resolves which task to mutate from the `branch_name` (or `pr_number`, for `pr_merge`) the real `submit_up`/`submit_root`/`pr_merge` call already carries. It also stubs `is_behind_base` (fixed `(0, 1)` — never behind, one commit ahead, so the PR-waiver zero-diff check and the freshen-rebase guard never fire) and `unmerged_child_commits` (empty list), both of which `submit_up`/`submit_root` check before opening a PR and which the original single-task `_StubGit` gained in this same change.

## The gotcha: `pr_merge` must keep the WorkSessionTable in sync

`TaskService.ceo_approve` guards on the merged-PR state via the task's `WorkSessionTable` row (created when a PM claims/re-claims a task — see `TaskService._finalize_claim`), not just the task's own `pr_number`/`pr_url` fields. A stub `pr_merge` that only flips fields on the `TaskTable` leaves a stale `WorkSessionTable.pr_status` (still `"open"`), and `ceo_approve`'s real guard then refuses the approval — the test would fail for a reason that has nothing to do with the code path under test.

`_MultiTaskStubGit.pr_merge` mirrors `GitService.pr_merge`'s real side effect: when the merged task carries a `work_session_id`, it loads that `WorkSessionTable` row in the SAME session and stamps `pr_status = "merged"` / `status = WorkSessionStatus.COMPLETED` before returning. Any future change to how `GitService.pr_merge` updates work-session state should keep this stub in sync, or this test starts asserting against stale state again.

## Seeding helpers added

- `_seed_main_pm` — returns the fixed `main-pm` agent id (`AGENT_UUIDS["main-pm"]`), inserting the row only if it isn't already present in the shared cross-test DB (avoids a slug unique-index collision).
- `_seed_pr_reviewer` — adds and flushes a `PR_REVIEWER`-role backend agent for the in-path gate's `claim_gate_review`/`pr_pass`/`pr_fail`.
- `_seed_root_task` — seeds a branchless-parent-free Main-PM coordination root (`parent_task_id=None`, its own `branch_name`) that the cell task is then parented under.
- `_RootCellHarness` (a dataclass) + `_seed_root_cell_harness` — wires the seeded cell/root/reviewer/main_pm into a real `Choreographer` over `_MultiTaskStubGit`, and exposes `submit_up_and_pass` / `submit_up_and_pass_root` / `cell_merge_complete` helpers so both new tests can drive the gate stages without duplicating the assertion sequence.

## Where to look for the next extension

If a future test needs to exercise a THIRD independently-tracked PR (e.g. a MegaTask root-subtask layer on top of this root/cell pair), extend `_MultiTaskStubGit`'s `tasks_by_branch` map rather than adding a new stub class — it was written branch/task-id-keyed specifically so it generalizes past two tasks.
