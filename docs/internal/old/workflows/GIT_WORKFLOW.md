# Git Workflow

> **Status:** Implemented
>
> This document describes the git workflow for RoboCo agents working on code tasks.

---

## Multi-Agent Workspace Structure

Each agent gets their own isolated workspace (git clone) for a project. This allows multiple agents to work on the same project in parallel, each on their own branch, without file conflicts.

```
{workspaces_root}/
└── {project-slug}/
    └── {team}/
        └── {agent-slug}/
            └── [git repo files]
```

### Example Structure

```
/data/workspaces/
└── roboco/
    ├── backend/
    │   ├── be-dev-1/    # Backend Developer 1's workspace
    │   ├── be-dev-2/    # Backend Developer 2's workspace
    │   ├── be-qa/       # Backend QA's workspace
    │   ├── be-pm/       # Backend PM's workspace
    │   └── be-doc/      # Backend Documenter's workspace
    ├── frontend/
    │   ├── fe-dev-1/
    │   ├── fe-dev-2/
    │   └── ...
    └── ux_ui/
        ├── ux-dev-1/
        └── ...
```

### Workspace Features

- **Auto-clone**: When `workspace_auto_clone` is enabled, workspaces are automatically cloned when first accessed
- **Isolation**: Each agent has their own working tree - no file locking conflicts
- **Branch independence**: Agents can be on different branches simultaneously
- **Project-scoped**: Workspaces are organized by project slug

---

## Branch Naming

Branches are created by PMs and include team context:

```
{type}/{team}/{task-id-prefix}
```

### Types

| Type | Use |
|------|-----|
| `feature/` | New functionality |
| `fix/` | Bug fixes |
| `refactor/` | Code restructuring |
| `docs/` | Documentation |
| `test/` | Test additions |
| `chore/` | Maintenance |

### Examples

```
feature/backend/a1b2c3d4
fix/frontend/e5f6g7h8
refactor/backend/i9j0k1l2
```

---

## Commit Messages

Commits are automatically linked to tasks with a task ID prefix:

```
[{task-id-prefix}] {message}
```

### Automatic Linking

When you use `roboco_git_commit()`, the commit:
1. Is prefixed with the task ID (first 8 chars)
2. Is recorded in the task's commit history
3. Is added to the work session if one exists

### Manual Format

```
{type}({scope}): {description}

{body}

Task: {task-id}
Co-authored-by: {agent-name}
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `style` | Formatting |
| `refactor` | Code restructure |
| `test` | Tests |
| `chore` | Maintenance |
| `perf` | Performance |

### Example

```
feat(auth): add Redis-based rate limiting

Implements sliding window rate limiter using Redis.
- Configurable limits per endpoint
- Lua script for atomic operations
- Returns rate limit headers

Task: a1b2c3d4-e5f6-7890-abcd-ef1234567890
Co-authored-by: be-dev-1
```

---

## Workflow

### PM Setup Phase

```
1. PM CREATES TASK (status: backlog)
   │
   ▼
2. PM CREATES SESSION
   │  roboco_session_start(channel, "collaborative", task_id)
   │
   ▼
3. PM ACTIVATES TASK (status: pending)
   │  roboco_task_activate(task_id)
   │
   ▼
4. PM CREATES BRANCH
   │  roboco_git_create_branch(project_slug, task_id, "feature")
   │  → Creates: feature/{team}/{task-id-prefix}
   │  → Auto-pushes to remote with tracking
   │
   ▼
5. PM ASSIGNS DEVELOPER
   │  roboco_task_claim(task_id, agent_id="be-dev-1")
```

### Developer Flow

```
1. CLAIM TASK
   │  roboco_task_claim(task_id)
   │
   ▼
2. START WORK (requires branch for git tasks)
   │  roboco_task_start(task_id)
   │
   ▼
3. CHECKOUT BRANCH
   │  roboco_git_checkout(project_slug, branch_name)
   │
   ▼
4. WORK & COMMIT
   │  # Multiple commits linked to task
   │  roboco_git_commit(project_slug, task_id, "add rate limiter")
   │  roboco_git_commit(project_slug, task_id, "add tests")
   │
   ▼
5. PUSH BRANCH
   │  roboco_git_push(project_slug)
   │
   ▼
6. SUBMIT FOR QA
   │  roboco_task_submit_qa(task_id, notes)
   │
   ▼
7. QA REVIEWS (on branch)
   │
   ├── PASS → Continue to Documentation
   └── FAIL → Task returns to needs_revision
```

### QA Flow

QA reviews the code on the branch:

```
1. QA CLAIMS TASK
   │  roboco_task_claim(task_id)
   │
   ▼
2. QA CHECKS OUT BRANCH
   │  roboco_git_checkout(project_slug, branch_name)
   │
   ▼
3. QA REVIEWS
   │  roboco_git_status(project_slug)
   │  roboco_git_diff(project_slug)
   │  roboco_git_log(project_slug)
   │
   ├── PASS: roboco_task_pass_qa(task_id, notes)
   │         → Status: awaiting_documentation
   │
   └── FAIL: roboco_task_fail_qa(task_id, notes)
             → Status: needs_revision
```

### Documentation Phase (Parallel Execution)

When a task reaches `awaiting_documentation`, two things happen in parallel:

```
                 awaiting_documentation
                          │
          ┌───────────────┴───────────────┐
          │                               │
     DOCUMENTER                      DEVELOPER
          │                               │
    writes docs                     creates PR
          │                               │
 roboco_task_docs_complete()    roboco_git_create_pr()
          │                               │
          │    sets docs_complete=True    │
          │                               │
          │      sets pr_created=True     │
          │                               │
          └───────────────┬───────────────┘
                          │
              BOTH must be true
                          │
                          ▼
                awaiting_pm_review
```

### PR Creation

Developer creates PR after QA passes:

```python
roboco_git_create_pr(
    project_slug="roboco",
    task_id="a1b2c3d4-...",
    title="[TASK-a1b2c3d4] Add rate limiting",
    body="## Summary\n- Implemented sliding window...\n\n## Test Plan\n..."
)
```

This:
- Creates PR via GitHub CLI (`gh pr create`)
- Targets the project's default branch
- Sets `pr_created=True` on the task
- Records PR number and URL on the task

---

## PM Review and Completion

### Standard Completion

```
1. TASK IN awaiting_pm_review
   │
   ▼
2. PM REVIEWS PR
   │  - Check commits: roboco_git_log(project_slug, branch)
   │  - Check changes: roboco_git_diff(project_slug)
   │
   ▼
3. PM COMPLETES TASK
   │  roboco_task_complete(task_id)
   │
   ▼
4. PM MERGES PR (Optional)
   │  roboco_git_merge_pr(project_slug, pr_number, "squash")
```

### CEO Approval (Major Tasks)

For significant changes, PM escalates to CEO:

```
1. TASK IN awaiting_pm_review
   │
   ▼
2. PM ESCALATES TO CEO
   │  roboco_task_escalate_to_ceo(task_id, notes)
   │  → Status: awaiting_ceo_approval
   │  → Requires PR number to exist
   │
   ▼
3. CEO REVIEWS
   │
   ├── APPROVE: roboco_task_ceo_approve(task_id, notes)
   │            → Status: completed
   │
   └── REJECT: roboco_task_ceo_reject(task_id, notes)
               → Status: needs_revision
               → Assigned back to developer
```

---

## Git API Endpoints

### Read-Only Operations

| Endpoint | Tool | Description |
|----------|------|-------------|
| `GET /git/status` | `roboco_git_status` | Get git status for project |
| `GET /git/log` | `roboco_git_log` | Get commit history |
| `GET /git/branches` | `roboco_git_branches` | List branches |
| `GET /git/diff` | `roboco_git_diff` | View changes |

### Write Operations

| Endpoint | Tool | Description |
|----------|------|-------------|
| `POST /git/commit` | `roboco_git_commit` | Create commit linked to task |
| `POST /git/push` | `roboco_git_push` | Push to remote |
| `POST /git/branch/create` | `roboco_git_create_branch` | Create task branch (PM only) |
| `POST /git/checkout` | `roboco_git_checkout` | Checkout branch |
| `POST /git/pr/create` | `roboco_git_create_pr` | Create pull request |
| `POST /git/pr/merge` | `roboco_git_merge_pr` | Merge PR (PM only) |

---

## Git Requirements for Transitions

Tasks with `requires_git=True` have additional validation:

### claimed -> in_progress
- **Requirement**: `branch_name` must be set
- **Why**: PM must create branch before developer can start

### awaiting_documentation -> awaiting_pm_review
- **Requirements**: BOTH `docs_complete=True` AND `pr_created=True`
- **Why**: Parallel workflow - documenter and developer must both finish

### awaiting_pm_review -> awaiting_ceo_approval
- **Requirement**: `pr_number` must be set
- **Why**: CEO needs to review the PR before final approval

---

## Branch Protection (Main)

- No direct pushes
- PR required
- QA must pass
- PM approval required
- CI must pass (when configured)

---

## Handling QA Failures

```
QA finds issues
      │
      ▼
Developer gets task back (needs_revision)
      │
      ▼
Developer claims, continues on SAME branch
      │
      ▼
Fix commits:
  roboco_git_commit(project_slug, task_id, "fix edge case X")
      │
      ▼
Push to same branch
  roboco_git_push(project_slug)
      │
      ▼
Re-submit for QA
  roboco_task_submit_qa(task_id, "Fixed issues noted in QA")
```

---

## Commit Linking

Every commit made through `roboco_git_commit` is:

1. **Prefixed** with task ID (first 8 chars)
2. **Recorded** in `task.commits` array
3. **Linked** to work session if active
4. **Attributed** to the committing agent

This creates full traceability from commit back to task.
