# Task Planning Workflow

## Overview

Planning is a **PM activity**. When a PM (Cell PM or Main PM) picks up a
coordination or parent task, they record a plan with `i_will_plan` and
then fan the work out into subtasks with `delegate`.

```
triage / give_me_work → i_will_plan → delegate (one per subtask) → i_am_idle
```

Developers do not have a separate planning verb — they pass a short
`plan` argument directly to `i_will_work_on(task_id, plan="...")` when
they claim a coding task.

## Submitting a Plan (PM)

```python
i_will_plan(
    task_id="<task>",
    plan="One-paragraph summary of how this work will be broken down",
    approach="High-level implementation strategy",
    sub_tasks=[
        "UX/UI: design the settings panel",
        "Frontend: wire the panel to the API",
        "Backend: add the settings endpoint",
    ],
    technical_considerations=["Reuse the existing config service"],
    risks=["Frontend depends on the UX design landing first"],
    open_questions=["Confirm the default toggle state with the CEO"],
)
```

After `i_will_plan`, the envelope's `next` field points you at
`delegate` — create one subtask per unit of work:

```python
delegate(
    parent_task_id="<task>",
    title="Add the settings endpoint",
    description="...",
    assigned_to="be-dev-1",
    team="backend",
    task_type="code",
    nature="feature",
    estimated_complexity="medium",
    acceptance_criteria=["Endpoint returns 200 with the saved settings"],
)
```

## Git Workflow

All code tasks follow the git workflow:
- **Branches are auto-created when a developer claims the task** via
  `i_will_work_on` — no manual branch creation
- Root tasks: branch created from the default branch (main/master)
- Subtasks: branch forked from the parent's branch

Coordination/parent tasks that only plan and delegate (no code) do not
need a branch of their own.

Hierarchical branch naming uses `--` between task IDs to avoid git ref
conflicts: `feature/{team}/{ROOT}--{SUB}--{SUBSUB}`.
