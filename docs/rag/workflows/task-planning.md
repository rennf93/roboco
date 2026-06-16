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
    covers_parent_criteria=["<parent-ac-id>"],
)
```

## Acceptance-Criteria Coverage

When you decompose a parent task, declare which parent criteria each subtask is
responsible for with **`covers_parent_criteria`** (a list of the parent's
`acceptance_criteria_ids`). This is what lets the org prove a decomposition
covers the parent's full intent — and it drives two gates and your coverage
briefing.

After `i_will_plan` and after each `delegate`, your envelope carries a coverage
view of the parent so you can see what is still unmapped:

- **`parent_ac_coverage`** — one entry per parent criterion: its `id`, `text`,
  whether a live subtask `claimed` it, and whether a completed subtask
  `verified` it.
- **`unclaimed_parent_acs`** — the parent criterion ids that no live subtask
  covers yet. Keep delegating until this is empty.

Two gates build on the coverage link:

- **Decomposition floor** — you cannot go `i_am_idle` on a parent while a
  criterion is still unclaimed. Delegate (or `reassign`) subtasks until every
  criterion is covered.
- **Roll-up gate** — a parent cannot `complete`, `submit_up`, or
  `escalate_to_ceo` unless every criterion traces to a child that **passed QA**
  on it.

Both gates are **safe-by-construction**: they stay inert until you start
declaring `covers_parent_criteria`, so a decomposition that never declares
coverage is never blocked. Declaring coverage is how you opt your parent into
the guarantee.

## Delegating Code Work: Per-Dev Queues

For code subtasks, delegate each developer their **full queue up front** rather
than one task at a time. Both of a cell's developers build in parallel, and each
works its own queue one task at a time, in order:

- A per-lane dispatch barrier holds a developer's later subtasks until their
  current one is in flight — so each dev's lane stays sequenced while the two
  devs run concurrently.
- Leaf PRs are still merged into the shared cell branch **in sequence**, not
  in parallel.
- Order the queue by dependency: the subtask others build on goes first.

Caps still apply: at most 12 subtasks per parent, and same-title duplicate
subtasks are rejected.

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
