# Git Tools

There is **no** "roboco_git_commit / _push / _create_pr / _merge_pr / _checkout" MCP tool. Anything mutating the repo goes through one of two role-scoped verbs and the choreographer handles git for you.

## Read Operations (any role) — `roboco-git-readonly`

| Tool | Purpose |
|------|---------|
| `roboco_git_status` | View working tree status |
| `roboco_git_log` | View commit history |
| `roboco_git_branch_list` | List branches |
| `roboco_git_diff` | View changes |

```python
status = roboco_git_status(project_slug="roboco")
diff = roboco_git_diff(project_slug="roboco")
log = roboco_git_log(project_slug="roboco", branch="feature/backend/a1b2c3d4")
branches = roboco_git_branch_list(project_slug="roboco")
```

## Branch Lifecycle — automatic

Branches are auto-created when an agent transitions a task to `in_progress`:

- Root task → `feature/team/ROOT_ID`
- Subtask → `feature/team/ROOT_ID--SUB_ID`
- Sub-subtask → `feature/team/ROOT_ID--SUB_ID--SUBSUB_ID`

You never run `git checkout` or `git branch` yourself; calling `i_will_work_on(task_id)` (developers) or `i_will_plan(task_id, plan)` (PMs) creates the branch and switches your workspace to it.

## Write Path — by role

### Developers and Documenters → `commit` (roboco-do)

```python
# Commit on your active task's branch. The choreographer:
#  - prefixes the message with [task-id]
#  - validates against commit_validator
#  - pushes to the remote branch
#  - opens a PR when the task transitions out of in_progress
commit(message="Add rate limiting endpoint", files=["roboco/api/routes/rate.py"])
```

There is no separate `push` step and no separate `create_pr` step. Both are side-effects of the lifecycle transitions the verbs already drive.

### PMs → `complete` (roboco-flow)

```python
# Cell PM completing a leaf task: merges the leaf PR.
# Main PM completing a parent task: opens the master PR + escalates to CEO.
complete(task_id="a1b2c3d4-...", notes="QA passed; docs complete; ready to ship.")
```

PMs never run `git` directly and have no commit/push tools. PMs `delegate` code work to devs, then `complete` to merge once QA + docs sign off.

## Branch Naming Convention

`{type}/{team}/{task-hierarchy}`

| Type | Use |
|------|-----|
| `feature/` | New functionality |
| `bug/` | Bug fixes |
| `chore/` | Maintenance |
| `docs/` | Documentation |
| `hotfix/` | Urgent fixes |

Hierarchy uses `--` (two hyphens) as the separator, not `/`, so a hierarchy slug like `ABC12345--DEF67890--GHI11111` is one git branch segment, not three nested directories.
