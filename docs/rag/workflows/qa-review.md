# QA Review Workflow

## When QA Starts

Task must be in `awaiting_qa` status.

## QA Review Steps

```python
# 1. Claim the task
roboco_task_claim(task_id)

# 2. Start review
roboco_task_start(task_id)

# 3. Announce to cell
roboco_message_send({
    channel: "backend-cell",
    content: "Starting QA review of [task title]",
    task_id: task_id
})

# 4. Read developer's journey (REQUIRED)
roboco_journal_read_team(original_developer, task_id=task_id)

# 5. Checkout branch and review
roboco_git_checkout(project_slug, branch_name)
roboco_git_diff(project_slug)

# 6. Run tests
# Backend: uv run pytest
# Frontend: pnpm test
```

## Review Checklist

Before making decision:
- [ ] Read developer's handoff notes
- [ ] Check all acceptance criteria
- [ ] Run tests (must pass)
- [ ] Verify functionality
- [ ] Check code quality
- [ ] Review against standards

## Passing QA

```python
roboco_task_qa_pass(task_id, {
    notes: "All acceptance criteria met. Tests pass. Code follows standards."
})
```

Result: Task advances to `awaiting_documentation`

## Failing QA

```python
roboco_task_qa_fail(task_id, {
    notes: "Issues found during review",
    issues: [
        "Bug: X doesn't work",
        "Missing: Y not implemented"
    ]
})
```

Result:
- Task returns to `needs_revision`
- Assigned back to original developer
- Developer receives notification

## Before Decision

Write reflection (REQUIRED):
```python
roboco_journal_reflect({
    task_id: task_id,
    what_done: "Reviewed X, Y, Z",
    what_learned: "Discovered patterns...",
    what_struggled: "Edge cases unclear"
})
```

## Self-Review Prevention

QA CANNOT review tasks they originally developed.

System tracks `original_developer` in `quick_context`. If QA == original_developer, claim is FORBIDDEN.
