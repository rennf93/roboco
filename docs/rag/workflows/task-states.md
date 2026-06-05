# Task States Reference

## State Categories

### Active States (Work happening)
- `claimed` - Agent has ownership, about to start
- `in_progress` - Active work
- `verifying` - Self-verification
- `needs_revision` - Fixing QA issues

### Waiting States (On hold)
- `blocked` - Waiting on dependency
- `paused` - Temporarily stopped
- `awaiting_qa` - Ready for QA review
- `awaiting_documentation` - Ready for docs
- `awaiting_pm_review` - Ready for PM approval
- `awaiting_ceo_approval` - Major task, CEO review

### Terminal States (Done)
- `completed` - Work finished
- `cancelled` - Work cancelled

### Setup State
- `backlog` - PM setup phase, not ready for work

## State Transitions

### Developer Flow
```
pending → claimed → in_progress → verifying → awaiting_qa
     ↑        ↓           ↑                         ↓
     └─ unclaim           └── needs_revision ←──────┘
```

### QA Flow
```
awaiting_qa → claimed (claim_review) → pass/fail
                                          ↓
                         pass: awaiting_documentation
                         fail: needs_revision
```

### Documenter Flow
```
awaiting_documentation → claimed (claim_doc_task) → awaiting_pm_review
```

### PM Activation
```
backlog → pending (a PM activates the task during `triage`)
```

## Role-Restricted Transitions

| Transition | Allowed Roles |
|------------|---------------|
| `backlog → pending` | cell_pm, main_pm |
| `claimed → pending` (unclaim) | assignee or PM |
| `awaiting_qa → awaiting_documentation` | qa only |
| `awaiting_qa → needs_revision` | qa only |
| `awaiting_documentation → awaiting_pm_review` | documenter, developer (parallel) |
| `awaiting_pm_review → completed` | cell_pm, main_pm |
| `awaiting_pm_review → awaiting_ceo_approval` | cell_pm, main_pm (parent tasks only) |
| `awaiting_ceo_approval → completed` | ceo only |
| `awaiting_ceo_approval → needs_revision` | ceo only |
| `any → cancelled` | cell_pm, main_pm, ceo |

## CEO Approval Notes

- Only **parent tasks** (no `parent_task_id`) can be escalated to CEO
- Subtasks are completed by their Cell PM, not the CEO
- The CEO reviews the complete feature via the parent task

## Checking State

You don't poll task state directly — every flow verb returns a
standardized envelope whose `status` and `next` fields tell you the
task's current state and what to call next. Trust the envelope rather
than guessing. To pull the full task context (criteria, prior notes,
handoff), call `evidence(task_id)`.
