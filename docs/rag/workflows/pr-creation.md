# Pull Request Creation

## When PRs Are Created

PRs are opened **before** QA review, not during `awaiting_documentation`. The choreographer creates the PR as a side-effect of the developer's `open_pr(task_id)` transition (`verifying → awaiting_qa`).

This is by design: QA reviews the real PR diff on the project's forge, and the downstream PM/CEO approval chain operates on a PR that already exists.

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

The push and the PR head always target **the task's own branch by name**, independent of whatever the shared clone happens to be checked out on. So a `No commits between` or wrong-branch worry at `open_pr` is the verb's job to resolve — never switch branches by hand to "fix" it.

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

An assembled parent reaches `awaiting_pm_review` only after the in-path gate: the Cell PM's `submit_up` opens the cell→root PR and enters `awaiting_pr_review`, where the cell PR reviewer `pr_pass`es it. The Cell PM then calls `complete(task_id, notes)`. The choreographer:

1. Verifies all subtasks are in a terminal state
2. Verifies the PR is reviewable
3. Merges the leaf PR into the parent branch (squash by default)
4. Transitions the task to `completed`

For the root parent, **Main PM**'s `submit_root` opens the root→master PR and enters the same gate; after the main reviewer `pr_pass`es it, the Main PM's `complete` escalates to the CEO (it does **not** merge). Only the CEO merges the root→master PR.

There is no `roboco_git_merge_pr` MCP tool.

## Prerequisites

- **Git token:** the project must have an encrypted token set on `projects.git_token_encrypted`. Without it, the workspace clone — and therefore everything downstream — fails with `WorkspaceError`. The field is historically named for GitHub PATs but works for any forge a project is registered against (GitHub, Gitea, GitLab — `projects.git_provider`); see "Forge-agnostic git" below.
- **Token scope:** `repo` on GitHub/Gitea; an equivalent `api`/`write_repository` scope on GitLab (for branch push, PR/MR create, PR/MR merge).
- **Merge target:** every dev/cell/root PR — never just the root→master one — targets the project's env-ladder **head** rung (`roboco.models.env_branches.head_branch`, typically `master`) — a project with no declared environment ladder resolves this straight from `projects.default_branch` via the read-time shim, so this is unchanged for every project that hasn't opted into a multi-rung ladder. A middle ladder rung (e.g. `qa`/`stag`) is never a PR target for dev/cell/root work — the only thing that ever lands a PR on a middle or prod rung is the `EnvSyncEngine` cascade (see `CLAUDE.md` "Env-branches ladder"), which is a platform-authored sync, not something you open. `sync_branch` and `submit_up` resolve their target from the task's own parent hierarchy (`resolve_parent_branch`), not the ladder directly — the ladder only surfaces at the two edge cases where a task has no branched ancestor: a project-root task's branch cut, and `submit_root`'s PR target.

## Forge-agnostic git

The PR/CI/review surface is provider-routed (`roboco/services/forge/`) — GitHub, Gitea, and GitLab are all supported per-project (`projects.git_provider`), and `GitService` never branches on which one a project uses. From your side, `pr_number` and `pr_url` are always real values regardless of forge — `pr_url` is the actual forge URL (a Gitea/GitLab instance host, never assumed to be `github.com`). One real asymmetry: GitLab has no "request changes" review primitive, so a `pr_fail`/change-request verdict on a GitLab-backed project posts as a plain MR note rather than a blocking review — the task still moves to `needs_revision` normally either way, only the PR-visible signal differs. Don't hardcode `github.com` in any URL you construct or reason about.

## Troubleshooting

- `NO_COMMITS` on `open_pr` → call `commit(...)` first; nothing to open a PR over.
- `NO_PR` on `pass`/`fail` → the choreographer didn't open a PR; check the workspace state with `roboco_git_status` and re-call `open_pr` once the workspace is clean.
- `FORCE_PUSH_FORBIDDEN` → only the CEO may force-push. If your branch diverged, `unclaim` and re-`claim` the task; the choreographer rebuilds the branch.
