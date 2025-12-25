# Git Workflow (Future)

> **Status:** Planned - Not yet implemented
>
> This document describes the intended git workflow for when code tools are added.

---

## Branch Naming

```
{type}/{task-id}-{short-description}
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
feature/TASK-042-rate-limiter
fix/TASK-055-auth-token-expiry
refactor/TASK-067-extract-service
docs/TASK-089-api-documentation
```

---

## Commit Messages

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

Task: TASK-042
Co-authored-by: be-dev-1
```

---

## Workflow

### Developer Flow

```
1. CLAIM TASK
   │
   ▼
2. CREATE BRANCH
   │
   │  git checkout -b feature/TASK-042-rate-limiter
   │
   ▼
3. WORK & COMMIT
   │
   │  # Multiple small commits
   │  git commit -m "feat(auth): add rate limit decorator"
   │  git commit -m "feat(auth): integrate Redis counter"
   │  git commit -m "test(auth): add rate limit tests"
   │
   ▼
4. PUSH BRANCH
   │
   │  git push -u origin feature/TASK-042-rate-limiter
   │
   ▼
5. SUBMIT FOR QA
   │
   │  roboco_task_submit_qa(task_id, notes)
   │
   ▼
6. QA REVIEWS (on branch)
   │
   ├── PASS → Continue
   └── FAIL → Fix on same branch, re-push
   │
   ▼
7. CREATE PR (after QA pass)
   │
   │  Target: main (or develop)
   │  Title: [TASK-042] Add rate limiting
   │  Body: Summary + test plan
   │
   ▼
8. PM REVIEWS PR
   │
   ▼
9. MERGE
   │
   │  Squash merge preferred
   │
   ▼
10. CLEANUP
    │
    │  Delete feature branch
```

---

## Branch Protection (Main)

- No direct pushes
- PR required
- QA must pass
- PM approval required
- CI must pass

---

## Commit Frequency

| Stage | Commit Frequency |
|-------|-----------------|
| During development | Frequently (logical chunks) |
| Before QA | Ensure all changes committed |
| After QA feedback | Fix commits |
| Before merge | Squash if messy |

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
  git commit -m "fix(auth): handle edge case X"
      │
      ▼
Push to same branch
      │
      ▼
Re-submit for QA
```

---

## Planned Git Tools

| Tool | Purpose |
|------|---------|
| `roboco_git_branch` | Create task branch |
| `roboco_git_commit` | Create commit with task link |
| `roboco_git_push` | Push to remote |
| `roboco_git_pr` | Create pull request |
| `roboco_git_status` | Check branch state |

---

## Integration with Task System

When implemented:
- Branch creation linked to task claim
- Commits linked to task in metadata
- PR creation triggers PM review
- Merge triggers completion flow
