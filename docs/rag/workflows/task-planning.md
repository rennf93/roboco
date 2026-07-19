# Task Planning Workflow

## Overview

Planning is a **PM activity**. When a PM (Cell PM or Main PM) picks up a coordination or parent task, they record a plan with `i_will_plan` and then fan the work out into subtasks with `delegate`.

```
triage / give_me_work → i_will_plan → delegate (one per subtask) → i_am_idle
```

Developers do not have a separate planning verb — they pass a short `plan` argument directly to `i_will_work_on(task_id, plan="...")` when they claim a coding task.

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

After `i_will_plan`, the envelope's `next` field points you at `delegate` — create one subtask per unit of work. **A "unit of work" is the LARGEST coherent piece one dev can own end-to-end, not the smallest step you can name (CEO doctrine: never over-separate).** Single-concern work — a dependency bump, a config change, one component — is one subtask covering the change, its verification, and the PR. Sequenced same-branch steps are one subtask, not siblings; split only for genuine parallelism across devs or different owners. The `sub_tasks` checklist above may hold more steps than you delegate — several checklist steps usually collapse into one delegated task:

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

When you decompose a parent task, declare which parent criteria each subtask is responsible for with **`covers_parent_criteria`** (a list of the parent's `acceptance_criteria_ids`, or the criterion's exact text). This is what lets the org prove a decomposition covers the parent's full intent — and it drives two gates and your coverage briefing.

**`covers_parent_criteria` is required, not optional, whenever the parent has any acceptance criteria.** `delegate` refuses a child with no `covers_parent_criteria` declared — "'<title>' declares no covers_parent_criteria, but the parent has acceptance criteria to decompose" — and a ref that resolves to neither an AC id nor exact text is rejected too, naming the valid criteria so you don't have to guess. It's only optional when the parent itself carries zero acceptance criteria. You don't have to cover everything in one `delegate` call — a wave may deliberately leave criteria for a later delegate — but every subtask you DO create must name what it covers.

After `i_will_plan` and after each `delegate`, your envelope carries a coverage view of the parent so you can see what is still unmapped:

- **`parent_ac_coverage`** — one entry per parent criterion: its `id`, `text`, whether a live subtask `claimed` it, and whether a completed subtask `verified` it.
- **`unclaimed_parent_acs`** — the parent criterion ids that no live subtask covers yet. Keep delegating until this is empty.

Two gates build on the coverage link:

- **Decomposition floor** — you cannot go `i_am_idle` on a parent while a criterion is still unclaimed. Delegate (or `reassign`) subtasks until every criterion is covered.
- **Roll-up gate** — a parent cannot `complete`, `submit_up`, or `escalate_to_ceo` unless every criterion traces to a child that **passed QA** on it.

Since `delegate` now requires `covers_parent_criteria` on every child of a parent with acceptance criteria, both gates are live from the first subtask on for any such parent — there's no longer a way to decompose without opting in. A parent with zero acceptance criteria is exempt from both, since there's nothing to trace coverage to.

The `verified` half of `parent_ac_coverage` isn't automatic — QA's `pass_review` (called via the `pass` tool) requires its own `criteria_verified` on the SUBTASK's own acceptance criteria before it can move a child to `awaiting_documentation`. Two distinct requirements on the same coverage chain: `covers_parent_criteria` (down, at delegate time — this subtask maps to those parent criteria) and `criteria_verified` (up, at QA pass time — each of THIS task's own criteria has concrete evidence). See `docs/rag/roles/qa.md` for the QA-side requirement.

## Delegating Code Work: Per-Dev Queues

For code subtasks, delegate each developer their **full queue up front** rather than one task at a time. Both of a cell's developers build in parallel, and each works its own queue one task at a time, in order:

- A per-lane dispatch barrier holds a developer's later subtasks until their current one is in flight — so each dev's lane stays sequenced while the two devs run concurrently.
- Leaf PRs are still merged into the shared cell branch **in sequence**, not in parallel.
- Order the queue by dependency: the subtask others build on goes first.

Caps still apply: at most 12 subtasks per parent, and same-title duplicate subtasks are rejected.

## PMs Do Not Own Code Tasks

A PM (Cell PM or Main PM) is a coordinator — it plans and delegates, it does not write code, and it has no code verb. The role×task_type rule is enforced at **creation**, not just at delegate:

- A `code`-typed task **cannot be assigned to a PM** — `delegate`, `TaskService.create`, batch activation, reassign, and the claim/escalation diversion all consult the same `pm_cannot_own_code` / `main_pm_cannot_own_code` guard and reject it.
- A Main-PM coordination **root** that is `code`-typed is rejected by `submit_root`'s `PRECONDITION_ROOT_NOT_CODE` — a Main PM can never assemble+merge a code root, because it can't have written one.
- The one exception — "a PM may take a code task **only to resolve review issues**" (`is_issue_resolution`) — is a server-side signal the platform sets when routing a `needs_revision` code task back to its owning PM to act on concrete review issues; it is **not** something you pass from a verb. In practice no live path exercises it yet; the structural rule is: if you're a PM and you're looking at a `code` task, delegate it to a developer instead.

This is structural, not a hint. Before this guard, a PM assigned a code task would claim it and deadlock into a respawn loop — a coordinator with no code verb holding a code task it can neither do nor hand back. The guard makes that loop unrepresentable.

## Delegation Depth

The task hierarchy is capped at `MAX_TASK_DEPTH = 4` levels (depths 0–3). The normal 3-layer flow (Main-PM root → cell task → dev subtask) fits in 3; **MegaTask** adds one Main-PM layer on top — umbrella (depth 0) → root-subtask (1) → cell task (2) → dev subtask (3) — which is why the cap is 4, not 3. A `delegate` that would create a node at depth 4 is rejected with a clean `invalid_state` and a "create as a sibling" remediation. Don't over-nest; if you're hitting the cap, the work belongs as a sibling, not a child.

## Git Workflow

All code tasks follow the git workflow:
- **Branches are auto-created when a developer claims the task** via `i_will_work_on` — no manual branch creation
- Root tasks: branch created from the project's env-ladder **head rung** (typically `master`/`main`) — a project with no declared environment ladder resolves this from `projects.default_branch` via the read-time shim, so nothing changes for a project that hasn't opted into a multi-rung ladder
- Subtasks: branch forked from the parent's branch (unaffected by the env ladder — this is pure task-hierarchy resolution)

Coordination/parent tasks that only plan and delegate (no code) do not need a branch of their own.

Hierarchical branch naming uses `--` between task IDs to avoid git ref conflicts: `feature/{team}/{ROOT}--{SUB}--{SUBSUB}`.
