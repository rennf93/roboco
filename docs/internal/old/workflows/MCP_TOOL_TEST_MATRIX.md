# MCP Tool Test Matrix

Comprehensive test matrix for validating all MCP tools work correctly for each agent role.

## Test Environment Setup

```bash
# Start services
docker-compose up -d postgres redis qdrant
cd roboco && uv run python -m roboco.api.main

# Test agent endpoints
curl -H "X-Agent-ID: be-dev-1" http://localhost:8000/health
```

---

## Task MCP Tools (55 total tools)

### Core Lifecycle Tools (All Agents)

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_scan` | Y | Y | Y | Y | Y | Scan returns tasks filtered by role/team |
| `roboco_task_get` | Y | Y | Y | Y | Y | Fetch task by ID returns full details |
| `roboco_task_claim` | Y | Y | Y | Y | N | Only claimable statuses for role |
| `roboco_task_plan` | Y | N | Y | N | N | Saves plan, requires claimed status |
| `roboco_task_start` | Y | Y | Y | Y | N | Status → in_progress, requires plan |
| `roboco_task_progress` | Y | Y | Y | Y | N | Updates percentage (0-100) |
| `roboco_task_escalate` | Y | Y | Y | Y | N | Routes to correct manager |
| `roboco_task_substitute` | Y | Y | Y | Y | N | Graceful exit with reason |
| `roboco_agent_idle` | Y | Y | Y | Y | N | Signals no work available |

### Blocking Tools (Developer + PM)

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_block` | Y | N | Y | N | N | Status → blocked, records reason |
| `roboco_task_unblock` | Y (own) | N | Y (cell) | N | N | Status → in_progress |
| `roboco_task_pause` | Y | N | Y | N | N | Status → paused, saves checkpoint |

### Developer Submit Tools

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_submit_verification` | Y | N | N | N | N | Status → verifying |
| `roboco_task_submit_qa` | Y | N | N | N | N | Status → awaiting_qa |
| `roboco_task_submit_pm_review` | Y | N | N | N | N | Status → awaiting_pm_review (non-dev tasks) |

### QA Tools

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_qa_pass` | N | Y | N | N | N | Status → awaiting_documentation |
| `roboco_task_qa_fail` | N | Y | N | N | N | Status → needs_revision, records issues |

### Documenter Tools

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_docs_complete` | N | N | N | Y | N | Status → awaiting_pm_review |

### PM/Management Tools

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_task_create` | N | N | Y | N | Y | Creates task in backlog |
| `roboco_task_assign` | N | N | Y | N | Y | Sets assigned_to field |
| `roboco_task_activate` | N | N | Y | N | Y | Status: backlog → pending |
| `roboco_task_complete` | N | N | Y | N | Y | Status → completed |
| `roboco_task_cancel` | N | N | Y | N | Y | Status → cancelled |

### Session Tools (PM/Board)

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_session_create_for_tasks` | N | N | Y | N | Y | Creates linked session |
| `roboco_session_link_task` | N | N | Y | N | Y | Links existing session to task |
| `roboco_session_unlink_task` | N | N | Y | N | Y | Removes task-session link |
| `roboco_session_get_for_task` | Y | Y | Y | Y | Y | Returns task's sessions |
| `roboco_group_create` | N | N | Main PM | N | Y | Creates work group |

---

## Message MCP Tools

| Tool | All Agents | Test Case |
|------|------------|-----------|
| `roboco_channel_list` | Y | Returns readable/writable channels for agent |
| `roboco_channel_history` | Y | Returns messages, respects access |
| `roboco_message_send` | Y | Requires task_id, routes to session |
| `roboco_message_get` | Y | Fetches single message by ID |
| `roboco_ask_question` | Y | Wrapper for message_send |
| `roboco_report_blocker` | Y | Wrapper for message_send |
| `roboco_session_history_for_task` | Y | Returns task session messages |

---

## Notification MCP Tools

| Tool | Developer | QA | PM | Documenter | Board | Test Case |
|------|-----------|----|----|------------|-------|-----------|
| `roboco_notify_list` | Y | Y | Y | Y | Y | Returns pending notifications |
| `roboco_notify_get` | Y | Y | Y | Y | Y | Fetches single notification |
| `roboco_notify_ack` | Y | Y | Y | Y | Y | Marks notification acknowledged |
| `roboco_notify_send` | N | N | Y | N | Y | Sends notification to recipient |

---

## Journal MCP Tools

| Tool | All Agents | Special Access | Test Case |
|------|------------|----------------|-----------|
| `roboco_journal_entry` | Y | - | Creates generic entry |
| `roboco_journal_reflect` | Y | - | Creates reflection for task |
| `roboco_journal_decision` | Y | - | Logs decision with rationale |
| `roboco_journal_learning` | Y | - | Logs learning |
| `roboco_journal_struggle` | Y | - | Logs struggle |
| `roboco_journal_search` | Y | - | Semantic search own entries |
| `roboco_journal_stats` | Y | - | Returns entry statistics |
| `roboco_journal_recent` | Y | - | Returns recent entries |
| `roboco_journal_read_team` | Y | Cell reads cell, PM reads all | Reads teammate journals |
| `roboco_journal_scope` | Y | - | Shows accessible journals |

---

## Optimal MCP Tools (Knowledge Base)

| Tool | All Agents | Test Case |
|------|------------|-----------|
| `roboco_kb_search` | Y | Semantic search knowledge base |
| `roboco_rag_query` | Y | RAG query with context |
| `roboco_kb_stats` | Y | Returns KB statistics |
| `roboco_kb_index_code` | Y | Indexes code files |
| `roboco_kb_index_docs` | Y | Indexes documentation |
| `roboco_tokens_estimate` | Y | Estimates token usage |
| `roboco_escalate` | Y | Escalates to manager |
| `roboco_request_approval` | Y | Requests human approval |

---

## Critical Test Scenarios

### 1. Full Developer Workflow
```
1. roboco_notify_list() → check for assignments
2. roboco_task_scan(team="backend") → find pending task
3. roboco_task_claim(task_id) → claim it
4. roboco_kb_search("similar work") → research
5. roboco_task_plan(task_id, ...) → submit plan
6. roboco_task_start(task_id) → begin work
7. roboco_message_send(channel, "Starting", task_id) → announce
8. roboco_task_progress(task_id, "Working", 50) → update
9. roboco_journal_reflect(task_id, ...) → reflect
10. roboco_task_submit_verification(task_id) → self-check
11. roboco_task_submit_qa(task_id) → submit for QA
```

### 2. Full QA Workflow
```
1. roboco_task_scan(team="backend") → find awaiting_qa
2. roboco_task_claim(task_id) → claim it
3. roboco_task_start(task_id) → begin review
4. roboco_journal_read_team(dev_id, task_id) → read dev journey
5. roboco_task_progress(task_id, "Reviewing", 50)
6. roboco_journal_reflect(task_id, ...)
7. roboco_task_qa_pass(task_id) OR roboco_task_qa_fail(task_id, issues)
```

### 3. Full PM Workflow
```
1. roboco_task_scan() → find pending/escalations
2. roboco_task_claim(task_id)
3. roboco_task_start(task_id)
4. roboco_task_plan(task_id, ...)
5. roboco_task_create({parent_task_id, ...}) → create subtask
6. roboco_session_create_for_tasks({task_ids}) → create session
7. roboco_task_activate(subtask_id) → make visible
8. roboco_notify_send({recipient, task_id}) → notify assignee
9. roboco_journal_read_team("be-dev-1") → monitor progress
10. roboco_task_complete(subtask_id) → after full workflow
```

### 4. Blocking/Unblocking Flow
```
Developer:
1. roboco_task_block(task_id, reason, what_needed)
2. roboco_message_send(channel, "Blocked on X", task_id)
3. Wait for resolution...
4. roboco_task_unblock(task_id) → resume

PM:
1. roboco_task_scan() → see blocked tasks
2. roboco_journal_read_team(dev_id, task_id) → understand context
3. Resolve issue...
4. roboco_task_unblock(task_id) → unblock for developer
```

### 5. Escalation Chain
```
Developer → Cell PM → Main PM → Board
be-dev-1 → be-pm → main-pm → product-owner

Test:
1. roboco_task_escalate(task_id, reason) as be-dev-1
2. Verify notification goes to be-pm
3. roboco_task_escalate(task_id, reason) as be-pm
4. Verify notification goes to main-pm
```

### 6. Self-Review Prevention
```
1. be-dev-1 submits task for QA
2. be-qa claims and reviews → OK
3. be-dev-1 tries to claim as QA → FORBIDDEN
```

### 7. Session Routing
```
1. PM creates task with roboco_task_create
2. PM creates session with roboco_session_create_for_tasks
3. PM activates task with roboco_task_activate
4. Developer claims, starts
5. Developer sends message with task_id → routes to session
6. Subtasks inherit parent's session automatically
```

---

## Error Response Format

All errors return:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Task 123 not found",
    "details": {...}
  }
}
```

Standard error codes:
- `NOT_FOUND` - Resource doesn't exist
- `ACCESS_DENIED` - Permission denied
- `INVALID_INPUT` - Bad request data
- `INVALID_STATE` - Wrong status for operation
- `NOT_AUTHORIZED` - Auth required
- `ALREADY_LINKED` - Duplicate link
- `NO_SESSION_FOR_TASK` - Task has no session

---

## Validation Checklist

Before declaring ready:

- [ ] All tools return consistent error format
- [ ] All role restrictions enforced at MCP layer
- [ ] All role restrictions enforced at API layer
- [ ] Session routing works for subtasks (inherits parent session)
- [ ] Escalation chain validates correctly
- [ ] Self-review prevention works
- [ ] Channel access respects permissions
- [ ] Journal read_team respects cell boundaries
- [ ] Notifications route to correct recipients
- [ ] All prompts match available tools
