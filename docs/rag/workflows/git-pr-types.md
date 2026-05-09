# Git PR Types

| `is_root_pr` | Target | Reviewer / Merger | Content |
|--------------|--------|-------------------|---------|
| `True`       | `master` | CEO approves; Main PM opens + merges | Full task tree, all commits, all agent links |
| `False`      | parent branch | Cell PM merges | Task commits only, scoped to the cell |

## How PRs Are Created

There is **no** `roboco_git_create_pr` MCP tool. PRs are side-effects of
lifecycle transitions, driven by the choreographer:

- **Leaf PR (cell-scoped, `is_root_pr=False`)**:
  Opened automatically when the assigned developer calls
  `open_pr(task_id)` after their `commit(...)` calls. Merged when
  the Cell PM calls `complete(task_id, notes)` after QA + docs sign off.

- **Master PR (`is_root_pr=True`)**:
  Opened by the choreographer when the **Main PM** calls
  `complete(task_id, notes)` on the root parent task. Merged by the CEO
  via the dashboard once all cell-scoped PRs have been merged into it.

Title and body are generated from the task templates in
`roboco/templates/git/pr_*.py`. Don't hand-write PR descriptions in the
agent prompts — they'll be overridden.

## Auto-Checkout

Branches and checkout are handled automatically:

- `i_will_work_on(task_id)` (devs) creates the task's branch and checks it
  out in the agent's workspace.
- `i_will_plan(task_id, plan)` (PMs) does the same for parent tasks.
- Workspace dirty? The verb returns an error envelope; clean up first
  with `commit(...)` or escalate via `i_am_blocked(task_id, reason)`.
