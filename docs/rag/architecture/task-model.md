# Task Model

## Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `title` | String | Task title |
| `description` | Text | Detailed description |
| `acceptance_criteria` | Array | How we know it's done |
| `status` | Enum | Lifecycle state |
| `priority` | Int | 0=P0 (highest) to 3=P3 |
| `team` | Enum | backend, frontend, ux_ui |

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
