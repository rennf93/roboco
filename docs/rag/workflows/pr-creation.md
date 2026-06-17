# Pull Request Creation

## When PRs Are Created

PRs are opened **before** QA review, not during `awaiting_documentation`. The choreographer creates the PR as a side-effect of the developer's `open_pr(task_id)` transition (`verifying → awaiting_qa`).

This is by design: QA reviews the real PR diff on GitHub, and the downstream PM/CEO approval chain operates on a PR that already exists.

You do **not** call any tool to create a PR. There is no `roboco_git_create_pr` MCP tool.

## How the dev triggers it

```python
# 1. Make commits as you work (auto-pushes, no separate push step)
commit(message="feat(api): add Redis rate limiter",
       files=["roboco/api/routes/rate.py", "tests/integration/test_rate.py"])

# 2. Once acceptance criteria are implemented + tested, hand off to QA.
#    The choreographer opens the PR here, sets pr_number/pr_url on the
#    task, and transitions verifying → awaiting_qa.
open_pr(task_id="<task>")
```

The transition enforces (`enforcement/task_lifecycle.py`):

- `self_verified=True` — set when you call `i_am_done()` or `verify(task_id)` first
- `commits` non-empty — at least one commit on the task
- `progress_updates` non-empty — at least one note on what changed
- `pr_number` is set automatically by the choreographer; you don't pass it

If any precondition is missing, the verb returns an envelope explaining what's missing and how to remediate.

## PR Title and Body

Generated from templates in `roboco/templates/git/pr_internal.py` and `roboco/templates/git/pr_root.py`. You don't write the body by hand — it's filled with task title, acceptance criteria, the dev's notes, and the standard traceability links.

Title format: `[TASK-{root-id:8}:{task-id:8}] {task-title}`.

## Parallel Documenter Phase

After QA passes, the task transitions to `awaiting_documentation` and runs documenter + dev in parallel:

| Agent | Action | Flag set |
|-------|--------|----------|
| Documenter | Writes docs files, then `i_documented(task_id, notes, files)` | `docs_complete=True` |
| Developer | (already done by the time we get here) | `pr_created=True` |

Task transitions to `awaiting_pm_review` when both are true.

## PM Merges via `complete`

After `awaiting_pm_review`, the Cell PM calls `complete(task_id, notes)`. The choreographer:

1. Verifies all subtasks are in a terminal state
2. Verifies the PR is reviewable
3. Merges the leaf PR into the parent branch (squash by default)
4. Transitions the task to `completed`

For the root parent, **Main PM**'s `complete` opens the master PR and escalates to CEO via `escalate_to_ceo` semantics.

There is no `roboco_git_merge_pr` MCP tool.

## Prerequisites

- **Git token:** the project must have an encrypted GitHub PAT set on `projects.git_token_encrypted`. Without it, the workspace clone — and therefore everything downstream — fails with `WorkspaceError`.
- **Token scope:** `repo` (for branch push, PR create, PR merge).
- **Default branch:** `projects.default_branch` is the merge target for the master PR (typically `master`).

## Troubleshooting

- `NO_COMMITS` on `open_pr` → call `commit(...)` first; nothing to open a PR over.
- `NO_PR` on `pass`/`fail` → the choreographer didn't open a PR; check the workspace state with `roboco_git_status` and re-call `open_pr` once the workspace is clean.
- `FORCE_PUSH_FORBIDDEN` → only the CEO may force-push. If your branch diverged, `unclaim` and re-`claim` the task; the choreographer rebuilds the branch.
