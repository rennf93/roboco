# Task Model

## Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `title` | String | Task title |
| `description` | Text | Detailed description |
| `acceptance_criteria` | Array | How we know it's done |
| `acceptance_criteria_ids` | Array | Stable per-criterion id (1:1 with `acceptance_criteria`) |
| `parent_ac_refs` | Array | Parent AC ids this subtask is responsible for |
| `status` | Enum | Lifecycle state |
| `priority` | Int | 0=P0 (highest) to 3=P3 |
| `team` | Enum | backend, frontend, ux_ui |

## Acceptance-Criteria Tracking

Every task's `acceptance_criteria` get a parallel list of stable `acceptance_criteria_ids` — one id per criterion, generated automatically when a task is created. The ids are stable across edits, so other tasks can reference a specific criterion.

When a parent task is decomposed, each subtask declares which parent criteria it covers in `parent_ac_refs` (set from the `covers_parent_criteria` argument to `delegate`). That child→parent link is what lets the org guarantee a decomposition actually covers the parent's full intent. See `docs/rag/workflows/task-planning.md` for the coverage gates and the PM's coverage briefing.

## Task Types

| Type | Description |
|------|-------------|
| `code` | Development work |
| `documentation` | Writing docs |
| `research` | Investigation |
| `planning` | Task breakdown |
| `design` | UX/UI design |
| `administrative` | Admin work |

## Git Fields

| Field | Description |
|-------|-------------|
| `project_id` | Associated project (required) |
| `branch_name` | Git branch for task |
| `work_session_id` | Active work session |
| `pr_number` | PR number |
| `pr_url` | Full PR URL |
| `docs_complete` | Documenter finished |
| `pr_created` | Developer created PR |
| `commits` | Linked commits |

## Assignment Fields

| Field | Description |
|-------|-------------|
| `created_by` | Agent who created |
| `assigned_to` | Currently assigned |
| `parent_task_id` | Parent for subtasks |
| `dependency_ids` | Blocking tasks |

## Context Fields

| Field | Description |
|-------|-------------|
| `plan` | Implementation plan |
| `quick_context` | 2-3 sentence summary |
| `proactive_context` | RAG context at claim |
| `dev_notes` | Developer notes |
| `qa_notes` | QA feedback |

## Timestamps

| Field | Description |
|-------|-------------|
| `claimed_at` | When claimed |
| `started_at` | When started |
| `completed_at` | When completed |
| `target_date` | Target date |

## Indexes

- `ix_tasks_team_status` - Team + Status queries
- `ix_tasks_assigned_status` - Assignee + Status
- `ix_tasks_project_status` - Project + Status
