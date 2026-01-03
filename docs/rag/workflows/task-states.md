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

### Special States
- `quarantined` - Problematic task, can return to pending

### Setup State
- `backlog` - PM setup phase, not ready for work

## State Transitions

### Developer Flow
```
pending → claimed → in_progress → verifying → awaiting_qa
                         ↑                         ↓
                         └── needs_revision ←──────┘
```

### QA Flow
```
awaiting_qa → claimed → in_progress → pass/fail
                                         ↓
                        pass: awaiting_documentation
                        fail: needs_revision
```

### Documenter Flow
```
awaiting_documentation → claimed → in_progress → awaiting_pm_review
```

### PM Activation
```
backlog → pending (via roboco_task_activate)
```

## Role-Restricted Transitions

| Transition | Allowed Roles |
|------------|---------------|
| `backlog → pending` | cell_pm, main_pm |
| `awaiting_qa → awaiting_documentation` | qa only |
| `awaiting_qa → needs_revision` | qa only |
| `awaiting_documentation → awaiting_pm_review` | documenter only |
| `awaiting_pm_review → completed` | cell_pm, main_pm |
| `any → cancelled` | cell_pm, main_pm |

## Checking State

```python
task = roboco_task_get(task_id)
# task.status contains current state
```
