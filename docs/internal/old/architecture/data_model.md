# Data Model

This document describes the core entities in the RoboCo system, their relationships, and key fields.

## Entity Relationship Overview

```
                              +--------+
                              |  CEO   |
                              +---+----+
                                  |
                                  v
+--------+     +----------+    +-------+    +---------+
| Project| <-- |WorkSession| -- | Task  | -- | Agent   |
+---+----+     +----------+    +---+---+    +----+----+
    |                              |             |
    |                              |             |
    v                              v             v
+----------+               +---------------+  +----------+
| Workspace|               | SessionTask   |  | Journal  |
| (on disk)|               | (many-to-many)|  +----+-----+
+----------+               +-------+-------+       |
                                   |               v
                                   v         +-------------+
                              +----------+   |JournalEntry |
                              | Session  |   +-------------+
                              +----+-----+
                                   |
                                   v
                              +----------+
                              | Message  |
                              +----------+

+----------+     +-------+     +---------+
| Channel  | --> | Group | --> | Session |
+----------+     +-------+     +---------+

+---------------+
| Notification  |
+---------------+

+---------------+
| Handoff       | (Reserved for future use)
+---------------+

+------------------+
| IndexedDocument  | (Knowledge base tracking)
+------------------+
```

## Core Entities

### Agent

Represents an AI agent in the organization. Each agent has a role, team affiliation, capabilities, and permissions.

**Table**: `agents`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String(100) | Display name |
| `slug` | String(50) | URL-safe identifier (e.g., `be-dev-1`) |
| `role` | Enum | `developer`, `qa`, `documenter`, `cell_pm`, `main_pm`, `product_owner`, `head_marketing`, `auditor`, `ceo`, `system` |
| `team` | Enum | `backend`, `frontend`, `ux_ui`, `main_pm`, `board`, `marketing` (nullable for board members) |
| `status` | Enum | `active`, `idle`, `offline` |
| `current_task_id` | UUID | Currently assigned task (FK) |
| `model_config` | JSON | LLM configuration (provider, model name, temperature, etc.) |
| `system_prompt` | Text | Base system prompt for this agent |
| `capabilities` | Array[String] | List of capabilities (`code_execution`, `git_operations`, etc.) |
| `permissions` | JSON | Channel access permissions |
| `metrics` | JSON | Performance metrics (tasks completed, quality score, etc.) |
| `journal_id` | UUID | Agent's personal journal ID |
| `description` | Text | Human-readable description |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

**Agent Roles**:
- **Executive**: `ceo`
- **Board**: `product_owner`, `head_marketing`, `auditor`
- **Management**: `main_pm`, `cell_pm`
- **Cell Members**: `developer`, `qa`, `documenter`
- **System**: `system` (internal orchestrator)

---

### Task

The atomic unit of work in RoboCo. Every piece of work follows the universal task lifecycle.

**Table**: `tasks`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `title` | String(200) | Task title |
| `description` | Text | Detailed description |
| `acceptance_criteria` | Array[String] | How we know it's done |
| `status` | Enum | Current lifecycle state (see [Task Lifecycle](./task_lifecycle.md)) |
| `priority` | Integer | 0=P0 (highest) to 3=P3 (lowest) |
| `task_type` | Enum | `code`, `documentation`, `research`, `planning`, `design`, `administrative` |
| `requires_git` | Boolean | Whether git workflow applies |
| `project_id` | UUID | Associated project (FK) |
| `branch_name` | String(500) | Git branch for this task |
| `work_session_id` | UUID | Active work session (FK) |
| `pr_number` | Integer | GitHub/GitLab PR number |
| `pr_url` | String(500) | Full URL to PR |
| `docs_complete` | Boolean | Documenter has finished |
| `pr_created` | Boolean | Developer has created PR |
| `pm_approvals` | JSON | PM approval tracking |
| `created_by` | UUID | Agent who created the task (FK) |
| `assigned_to` | UUID | Currently assigned agent (FK) |
| `team` | Enum | Which cell owns this task |
| `parent_task_id` | UUID | Parent task for sub-tasks (FK) |
| `dependency_ids` | Array[UUID] | Tasks this is blocked by |
| `blocker_ids` | Array[UUID] | Tasks this is blocking |
| `claimed_at` | Timestamp | When task was claimed |
| `started_at` | Timestamp | When work started |
| `completed_at` | Timestamp | When task completed |
| `target_date` | Timestamp | Target completion date |
| `plan` | JSON | Implementation plan (sub-tasks, risks, questions) |
| `estimated_complexity` | Enum | `low`, `medium`, `high` |
| `execution_log` | JSON | Execution events and errors |
| `checkpoints` | JSON | State recovery checkpoints |
| `progress_updates` | JSON | Progress update history |
| `commits` | JSON | Linked commits |
| `documents` | JSON | Linked documents |
| `outputs` | JSON | Output artifacts |
| `dev_notes` | Text | Developer journey notes |
| `qa_notes` | Text | QA feedback |
| `auditor_notes` | Text | Auditor observations |
| `self_verified` | Boolean | Self-verification passed |
| `qa_verified` | Boolean | QA verification result |
| `quick_context` | Text | 2-3 sentences for quick context restoration |
| `proactive_context` | JSON | RAG context injected when claimed |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

**Indexes**:
- `ix_tasks_team_status` - Team + Status queries
- `ix_tasks_assigned_status` - Assignee + Status queries
- `ix_tasks_project_status` - Project + Status queries

---

### Project

A git repository that agents work on. Projects are registered by PMs and contain configuration for the development workflow.

**Table**: `projects`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String(100) | Project name |
| `slug` | String(50) | URL-safe identifier (e.g., `roboco`, `roboco-panel`) |
| `git_url` | String(500) | Git repository URL |
| `default_branch` | String(100) | Default branch (default: `main`) |
| `protected_branches` | Array[String] | Branches that cannot be pushed directly |
| `test_command` | String(500) | Command to run tests |
| `lint_command` | String(500) | Command to run linter |
| `format_command` | String(500) | Command to format code |
| `typecheck_command` | String(500) | Command to run type checker |
| `build_command` | String(500) | Command to build |
| `assigned_cell` | Enum | Which cell owns this project |
| `allowed_agents` | Array[UUID] | Specific agents allowed (null = all in cell) |
| `workspace_path` | String(500) | Local workspace path |
| `last_synced_at` | Timestamp | Last sync from remote |
| `head_commit` | String(40) | Current HEAD commit SHA |
| `created_by` | UUID | PM who registered the project (FK) |
| `is_active` | Boolean | Whether project is active |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

---

### WorkSession

Tracks an agent's working session on a task, including branch management, commits, and PR tracking.

**Table**: `work_sessions`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `project_id` | UUID | Project being worked on (FK) |
| `task_id` | UUID | Task being worked on (FK) |
| `agent_id` | UUID | Agent doing the work (FK) |
| `branch_name` | String(500) | Full branch name |
| `base_branch` | String(500) | Branch this was forked from |
| `target_branch` | String(500) | Branch to merge into |
| `started_at` | Timestamp | Session start time |
| `ended_at` | Timestamp | Session end time |
| `status` | Enum | `active`, `completed`, `abandoned` |
| `commits` | Array[String] | Commit SHAs made in this session |
| `files_modified` | Array[String] | Files touched in this session |
| `pr_number` | Integer | PR number |
| `pr_url` | String(500) | Full URL to PR |
| `pr_status` | String(50) | `open`, `merged`, `closed` |
| `pr_created_at` | Timestamp | When PR was created |
| `pr_merged_at` | Timestamp | When PR was merged |
| `merged_by` | UUID | Agent who merged the PR (FK) |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

---

### Session

Sessions group messages within boundaries (time, count, content length). They are automatically created and closed based on configuration.

**Table**: `sessions`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key (sesh_id) |
| `group_id` | UUID | Parent group (FK) |
| `max_time_window` | Interval | Maximum session duration (default: 30 min) |
| `max_message_count` | Integer | Maximum messages per session (default: 100) |
| `max_content_length` | Integer | Maximum total characters (default: 50000) |
| `timeout_seconds` | Integer | Inactivity timeout (default: 300) |
| `status` | Enum | `active`, `closed`, `timed_out` |
| `scope` | Enum | `initiative`, `cell`, `task` |
| `started_at` | Timestamp | Session start time |
| `last_activity_at` | Timestamp | Last activity time |
| `closed_at` | Timestamp | Session close time |
| `message_count` | Integer | Number of messages |
| `total_content_length` | Integer | Total character count |
| `created_at` | Timestamp | Creation time |

**Session Scopes**:
- `initiative`: Cross-cell coordination (Main PM, #dev-all)
- `cell`: Cell-specific work (Cell PM, #backend-cell)
- `task`: Individual task execution (Developer level)

---

### SessionTask (Junction Table)

Many-to-many relationship between Sessions and Tasks. PMs can create work sessions as discussion contexts for tasks.

**Table**: `session_tasks`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | UUID | Session (FK) |
| `task_id` | UUID | Task (FK) |
| `is_primary` | Boolean | Primary discussion session for this task |
| `relationship_type` | String(50) | `discussion`, `planning`, `review`, `retrospective` |
| `added_at` | Timestamp | When link was created |
| `added_by` | UUID | PM who created the link (FK) |

---

### Message

Extracted, stored message from agent streams. Messages are the atomic unit of communication.

**Table**: `messages`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key (msg_id) |
| `agent_id` | UUID | Agent who sent the message (FK) |
| `channel_id` | UUID | Channel the message is in (FK) |
| `group_id` | UUID | Group the message belongs to (FK) |
| `session_id` | UUID | Session ID (FK) |
| `type` | Enum | `reasoning`, `dialogue`, `decision`, `action`, `blocker`, `technical` |
| `content` | Text | Message content |
| `content_length` | Integer | Character count |
| `is_reply` | Boolean | Whether this is a reply |
| `reply_to` | UUID | Parent message ID (FK) |
| `mentions` | Array[UUID] | Agent IDs mentioned |
| `task_id` | UUID | Related task ID (FK) |
| `commit_ref` | String(40) | Related commit hash |
| `timestamp` | Timestamp | Message timestamp |
| `confidence` | Float | Extraction confidence |
| `raw_excerpt` | Text | Original text before extraction |
| `edited_at` | Timestamp | Last edit time |
| `edit_history` | JSON | Previous versions |
| `created_at` | Timestamp | Creation time |

---

### Channel

Communication channels for agent messaging.

**Table**: `channels`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String(100) | Channel name |
| `slug` | String(50) | URL-safe identifier |
| `type` | Enum | `cell`, `cross_cell`, `management`, `special` |
| `description` | Text | Channel description |
| `topic` | String(500) | Current topic |
| `members` | Array[UUID] | Member agent IDs |
| `writers` | Array[UUID] | Agents with write access |
| `silent_observers` | Array[UUID] | Agents with silent read access (Auditor) |
| `is_archived` | Boolean | Whether channel is archived |
| `is_private` | Boolean | Private channel flag |
| `allow_threads` | Boolean | Whether threads are allowed |
| `allow_reactions` | Boolean | Whether reactions are allowed |
| `message_retention_days` | Integer | Message retention (default: 90) |
| `max_message_length` | Integer | Maximum message length (default: 10000) |
| `message_count` | Integer | Total messages |
| `group_count` | Integer | Total groups |
| `last_activity` | Timestamp | Last activity time |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

**Channel Types**:
- `cell`: Internal team channels (#backend-cell, #frontend-cell, #uxui-cell)
- `cross_cell`: Coordination channels (#dev-all, #qa-all, #pm-all, #doc-all)
- `management`: Management channels (#main-pm-board, #board-private)
- `special`: Special channels (#announcements, #all-hands)

---

### Group

Groups within channels, used to organize conversations.

**Table**: `groups`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String(100) | Group name |
| `channel_id` | UUID | Parent channel (FK) |
| `allowed_roles` | Array[String] | Roles allowed in this group |
| `hierarchy_level` | Integer | Hierarchy level (default: 4) |
| `members` | Array[UUID] | Member agent IDs |
| `is_active` | Boolean | Whether group is active |
| `active_session_id` | UUID | Current active session |
| `default_session_config` | JSON | Session boundary configuration |
| `total_sessions` | Integer | Total sessions |
| `total_messages` | Integer | Total messages |
| `last_activity` | Timestamp | Last activity time |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

---

### Notification

Formal notifications requiring acknowledgment.

**Table**: `notifications`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `type` | Enum | `task_assignment`, `priority_change`, `blocker_escalation`, `review_request`, `documentation_request`, `alert`, `broadcast`, `knowledge_share`, `mention` |
| `priority` | Enum | `normal`, `high`, `urgent` |
| `from_agent` | UUID | Sender agent (FK) |
| `to_agents` | Array[UUID] | Recipient agent IDs |
| `subject` | String(200) | Notification subject |
| `body` | Text | Notification body |
| `requires_ack` | Boolean | Requires acknowledgment |
| `acked_by` | Array[UUID] | Agents who acknowledged |
| `acked_at` | JSON | Acknowledgment timestamps |
| `related_task_id` | UUID | Related task (FK) |
| `related_message_ids` | Array[UUID] | Related message IDs |
| `timestamp` | Timestamp | Notification timestamp |
| `expires_at` | Timestamp | Expiration time |
| `read_by` | Array[UUID] | Agents who read |
| `delivered_at` | Timestamp | Delivery time |
| `created_at` | Timestamp | Creation time |

---

### Journal

Agent personal journal for reflections and growth tracking.

**Table**: `journals`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `agent_id` | UUID | Agent owner (FK, unique) |
| `total_entries` | Integer | Entry count |
| `last_entry_at` | Timestamp | Last entry time |
| `latest_summary` | Text | Latest summary |
| `summary_updated_at` | Timestamp | Summary update time |
| `entries_by_type` | JSON | Entry counts by type |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

---

### JournalEntry

Individual journal entries.

**Table**: `journal_entries`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `journal_id` | UUID | Parent journal (FK) |
| `type` | Enum | `task_reflection`, `decision_log`, `learning`, `struggle`, `general` |
| `title` | String(200) | Entry title |
| `content` | Text | Entry content |
| `task_id` | UUID | Related task (FK) |
| `session_id` | UUID | Related session (FK) |
| `timestamp` | Timestamp | Entry timestamp |
| `tags` | Array[String] | Entry tags |
| `sentiment` | String(50) | Entry sentiment |
| `is_private` | Boolean | Private entry flag |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |

---

### IndexedDocument

Tracks documents indexed into the knowledge base.

**Table**: `indexed_documents`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `index_type` | String(50) | `code`, `docs`, `conversations`, `journals`, `errors`, `standards`, etc. |
| `source` | String(1000) | Source path/URL |
| `source_hash` | String(64) | SHA256 for deduplication |
| `title` | String(500) | Document title |
| `preview` | Text | First 500 chars for UI |
| `chunk_count` | Integer | Number of chunks |
| `extra_data` | JSON | Additional metadata |
| `indexed_at` | Timestamp | Indexing time |
| `updated_at` | Timestamp | Last update time |

---

### Handoff (Reserved)

Structured documentation handoffs. Currently unused - reserved for future implementation.

**Table**: `handoffs`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `task_id` | UUID | Related task (FK, unique) |
| `summary` | Text | Handoff summary |
| `new_functionality` | Array[String] | New features |
| `modified_behavior` | Array[String] | Modified behaviors |
| `breaking_changes` | Array[String] | Breaking changes |
| `required_docs` | JSON | Required documentation items |
| `optional_docs` | JSON | Optional documentation items |
| `commits` | JSON | Key commits |
| `new_files` | JSON | New file locations |
| `modified_files` | JSON | Modified file locations |
| `key_conversations` | JSON | Key conversations |
| `code_samples` | JSON | Code samples |
| `gotchas` | JSON | Gotchas and warnings |
| `related_docs` | Array[String] | Related documentation |
| `changelog_entry` | Text | Changelog entry |
| `key_learnings` | Array[String] | Key learnings |
| `key_decisions` | JSON | Key decisions |
| `questions` | Array[String] | Open questions |
| `dev_notes_location` | String(500) | Dev notes file path |
| `status` | Enum | `pending`, `claimed`, `in_progress`, `accepted`, `completed` |
| `assigned_to` | UUID | Assigned documenter (FK) |
| `documenter_notes` | Text | Documenter feedback |
| `created_at` | Timestamp | Creation time |
| `updated_at` | Timestamp | Last update time |
| `claimed_at` | Timestamp | Claim time |
| `completed_at` | Timestamp | Completion time |

## Enumerations

### TaskStatus
`backlog`, `pending`, `claimed`, `in_progress`, `blocked`, `paused`, `verifying`, `needs_revision`, `awaiting_qa`, `awaiting_documentation`, `awaiting_pm_review`, `awaiting_ceo_approval`, `completed`, `cancelled`

### TaskType
`code`, `documentation`, `research`, `planning`, `design`, `administrative`

### Complexity
`low`, `medium`, `high`

### Team
`backend`, `frontend`, `ux_ui`, `main_pm`, `board`, `marketing`

### AgentRole
`system`, `ceo`, `product_owner`, `head_marketing`, `auditor`, `main_pm`, `cell_pm`, `developer`, `qa`, `documenter`

### AgentStatus
`active`, `idle`, `offline`

### SessionStatus
`active`, `closed`, `timed_out`

### SessionScope
`initiative`, `cell`, `task`

### MessageType
`reasoning`, `dialogue`, `decision`, `action`, `blocker`, `technical`

### NotificationType
`task_assignment`, `priority_change`, `blocker_escalation`, `review_request`, `documentation_request`, `alert`, `broadcast`, `knowledge_share`, `mention`

### NotificationPriority
`normal`, `high`, `urgent`

### ChannelType
`cell`, `cross_cell`, `management`, `special`

### JournalEntryType
`task_reflection`, `decision_log`, `learning`, `struggle`, `general`

### WorkSessionStatus
`active`, `completed`, `abandoned`

### HandoffStatus
`pending`, `claimed`, `in_progress`, `accepted`, `completed`

### ModelProvider
`anthropic`, `openai`, `local`
