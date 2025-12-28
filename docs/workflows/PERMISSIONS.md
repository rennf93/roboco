# Permissions Matrix

## Tool Permissions by Role

### Task Management Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_task_scan` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_get` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_claim` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_start` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_plan` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `roboco_task_progress` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_create` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_assign` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_activate` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_complete` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_cancel` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_pause` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `roboco_task_block` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `roboco_task_unblock` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_task_escalate` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_task_submit_verification` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `roboco_task_submit_qa` | ❌ | ❌ | ✅ | ❌ | ❌ |
| `roboco_task_qa_pass` | ❌ | ❌ | ❌ | ✅ | ❌ |
| `roboco_task_qa_fail` | ❌ | ❌ | ❌ | ✅ | ❌ |
| `roboco_task_docs_complete` | ❌ | ❌ | ❌ | ❌ | ✅ |
| `roboco_task_substitute` | ✅ | ✅ | ✅ | ✅ | ✅ |

### Session Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_session_create_for_tasks` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_session_link_task` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_session_get_for_task` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_group_create` | ✅ | ✅ | ❌ | ❌ | ❌ |

### Communication Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_message_send` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_channel_history` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_channel_list` | ✅ | ✅ | ✅ | ✅ | ✅ |

### Notification Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_notify_send` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `roboco_notify_list` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_notify_ack` | ✅ | ✅ | ✅ | ✅ | ✅ |

### Journal Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_journal_entry` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_reflect` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_decision` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_learning` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_struggle` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_search` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_recent` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_journal_read_team` | ✅ | ✅ | ❌ | ❌ | ✅ |

### Knowledge Base Tools

| Tool | Main PM | Cell PM | Developer | QA | Documenter |
|------|:-------:|:-------:|:---------:|:--:|:----------:|
| `roboco_kb_search` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_rag_query` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_kb_stats` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `roboco_kb_index_code` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `roboco_kb_index_docs` | ✅ | ✅ | ❌ | ❌ | ✅ |
| `roboco_tokens_estimate` | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## Channel Access Permissions

### Cell Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| `#backend-cell` | be-dev-1, be-dev-2, be-qa, be-pm, be-doc, main-pm | be-dev-1, be-dev-2, be-qa, be-pm, be-doc | auditor |
| `#frontend-cell` | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc, main-pm | fe-dev-1, fe-dev-2, fe-qa, fe-pm, fe-doc | auditor |
| `#uxui-cell` | ux-dev-1, ux-dev-2, ux-qa, ux-pm, ux-doc, main-pm | ux-dev-1, ux-dev-2, ux-qa, ux-pm, ux-doc | auditor |

### Cross-Cell Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| `#dev-all` | all devs, all PMs | all devs, all PMs | auditor |
| `#qa-all` | all QA, all PMs | all QA, all PMs | auditor |
| `#pm-all` | all PMs, main-pm | all PMs, main-pm | auditor |
| `#doc-all` | all docs, all PMs | all docs, all PMs | auditor |

### Management Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| `#main-pm-board` | main-pm, product-owner, head-marketing | main-pm, product-owner, head-marketing | auditor |
| `#board-private` | product-owner, head-marketing, ceo | product-owner, head-marketing, ceo | auditor |

### Special Channels

| Channel | Read | Write | Silent |
|---------|------|-------|--------|
| `#announcements` | everyone | main-pm, board only | auditor |
| `#all-hands` | everyone | everyone | auditor |

---

## Notification Permissions

### Who Can Send Notifications

| Role | Can Send | Scope |
|------|:--------:|-------|
| CEO | ✅ | Anyone |
| Auditor | ✅ | Anyone |
| Product Owner | ✅ | main-pm, head-marketing, auditor, ceo |
| Head Marketing | ✅ | main-pm, product-owner, auditor, ceo |
| Main PM | ✅ | Anyone |
| Cell PM | ✅ | Own cell + other PMs |
| Developer | ❌ | - |
| QA | ❌ | - |
| Documenter | ❌ | - |

### Notification Types

| Type | Sent By | To |
|------|---------|-----|
| `task_assignment` | PM | Specific agent |
| `priority_change` | PM/Board | Affected agents |
| `blocker_escalation` | PM | Main PM or other PM |
| `review_request` | PM | QA or Auditor |
| `documentation_request` | PM | Documenter |
| `alert` | Board/Auditor | Anyone |
| `broadcast` | Board/Main PM | Groups |

---

## Task Action Permissions

### Who Can Perform What Action

| Action | Owner | Same-Cell PM | Main PM | Board |
|--------|:-----:|:------------:|:-------:|:-----:|
| Claim | ✅ | ✅ | ✅ | ✅ |
| Start | ✅ | ❌ | ❌ | ❌ |
| Plan | ✅ | ❌ | ❌ | ❌ |
| Progress | ✅ | ❌ | ❌ | ❌ |
| Block | ✅ | ✅ | ✅ | ❌ |
| Unblock | ✅ | ✅ | ✅ | ❌ |
| Pause | ✅ | ✅ | ✅ | ❌ |
| Resume | ✅ | ❌ | ❌ | ❌ |
| Complete | ❌ | ✅ | ✅ | ✅ |
| Cancel | ❌ | ✅ | ✅ | ✅ |

### Claim Restrictions by Role

| Role | Can Claim From Status |
|------|----------------------|
| Developer | `pending`, `needs_revision` |
| QA | `awaiting_qa` |
| Documenter | `pending`, `awaiting_documentation` |
| Cell PM | `pending`, `backlog` |
| Main PM | `pending`, `backlog` |

---

## Self-Review Prevention

| Action | Prevention Rule |
|--------|-----------------|
| QA Pass/Fail | Cannot review task where you were the original developer |
| Docs Complete | Cannot document task where you were the original developer |

The system stores `original_developer` in `quick_context` when:
- Developer submits for QA → QA claims → `original_developer` preserved
- QA passes → Documenter claims → `original_developer` still preserved

---

## Team-Based Restrictions

| Agent | Can Only Work On Tasks With Team |
|-------|----------------------------------|
| be-dev-1, be-dev-2 | `backend` |
| be-qa | `backend` |
| be-pm | `backend` |
| be-doc | `backend` |
| fe-dev-1, fe-dev-2 | `frontend` |
| fe-qa | `frontend` |
| fe-pm | `frontend` |
| fe-doc | `frontend` |
| ux-dev-1, ux-dev-2 | `ux_ui` |
| ux-qa | `ux_ui` |
| ux-pm | `ux_ui` |
| ux-doc | `ux_ui` |
| main-pm | Any |
| Board | Any |
