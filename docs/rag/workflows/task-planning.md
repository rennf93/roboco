# Task Planning Workflow

## Overview

Planning is **required** before starting work. The workflow enforces:
```
CLAIM → PLAN → START → EXECUTE
```

## Workflow States

| State | Meaning | Next Step |
|-------|---------|-----------|
| `NEEDS_PLAN` | Task claimed, no plan yet | Call `roboco_task_plan()` |
| `READY_TO_START` | Plan approved, ready to work | Call `roboco_task_start()` |
| `EXECUTING` | Work in progress | Continue development |
| `REVISION_REQUIRED` | QA/PM requested changes | Reclaim and fix |

## Submitting a Plan

```
roboco_task_plan(task_id, {
    "approach": "High-level implementation strategy",
    "sub_tasks": [
        {"title": "Step 1", "description": "First action"},
        {"title": "Step 2", "description": "Second action"}
    ],
    "risks": ["Potential blockers or issues"],
    "open_questions": ["Clarifications needed from PM"]
})
```

## Cannot Start Without Plan

Calling `roboco_task_start()` without a plan returns:
- Error code: `NO_PLAN`
- Message: "Cannot start without a plan"
- Hint: Submit plan first

## Git Tasks

For tasks with `requires_git=True`:
- **Branches are auto-created when you claim the task**
- Root tasks: branch created from default branch (main/master)
- Subtasks: branch forked from parent's branch
- No manual `roboco_git_create_branch()` needed

Hierarchical branch naming: `feature/team/ROOT_ID/SUB_ID/SUBSUB_ID`
