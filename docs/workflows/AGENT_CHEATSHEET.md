# Agent Cheatsheet

Quick reference for each role.

---

## Developer (be-dev-1, be-dev-2, fe-dev-1, fe-dev-2, ux-dev-1, ux-dev-2)

### Your Flow
```
SCAN → CLAIM → PLAN → START → WORK → VERIFY → SUBMIT_QA
```

### Your Tools
```
✅ roboco_task_scan(team="backend")
✅ roboco_task_get(task_id)
✅ roboco_task_claim(task_id)
✅ roboco_task_plan(task_id, approach, steps, risks?, open_questions?)
✅ roboco_task_start(task_id)
✅ roboco_task_progress(task_id, message, percentage)
✅ roboco_task_block(task_id, blocker_task_id)
✅ roboco_task_pause(task_id, reason, checkpoint, remaining_work)
✅ roboco_task_escalate(task_id, reason)
✅ roboco_task_submit_verification(task_id)
✅ roboco_task_submit_qa(task_id, notes)
✅ roboco_task_submit_pm_review(task_id, notes)  → For non-dev tasks
✅ roboco_task_substitute(task_id, reason, details)  → Graceful exit

✅ roboco_message_send(channel, content, task_id)
✅ roboco_channel_history(channel)
✅ roboco_notify_list()
✅ roboco_notify_ack(notification_id)

✅ roboco_journal_entry(type, title, content, task_id)
✅ roboco_journal_reflect(...)
✅ roboco_journal_learning(...)
✅ roboco_journal_struggle(...)
✅ roboco_journal_search(query)

✅ roboco_kb_search(query, top_k)       → Search knowledge base
✅ roboco_rag_query(query)              → AI-generated answer from KB
✅ roboco_kb_stats()                    → What's indexed
✅ roboco_kb_index_code(sources)        → Index code for search
✅ roboco_tokens_estimate(content)      → Estimate token count
```

### NOT Your Tools
```
❌ roboco_task_create      → PM only
❌ roboco_task_assign      → PM only
❌ roboco_task_activate    → PM only
❌ roboco_task_complete    → PM only
❌ roboco_task_unblock     → PM only (you can unblock your OWN)
❌ roboco_task_qa_pass     → QA only
❌ roboco_task_qa_fail     → QA only
❌ roboco_task_docs_complete → Documenter only
❌ roboco_notify_send      → PM only
```

---

## QA (be-qa, fe-qa, ux-qa)

### Your Flow
```
SCAN (awaiting_qa) → CLAIM → START → REVIEW → PASS or FAIL
```

### Your Tools
```
✅ roboco_task_scan(team="backend")  → Look for awaiting_qa
✅ roboco_task_get(task_id)
✅ roboco_task_claim(task_id)         → Only from awaiting_qa
✅ roboco_task_start(task_id)
✅ roboco_task_progress(task_id, message, percentage)
✅ roboco_task_qa_pass(task_id, notes)
✅ roboco_task_qa_fail(task_id, notes, issues)
✅ roboco_task_escalate(task_id, reason)
✅ roboco_task_substitute(task_id, reason, details)  → Graceful exit

✅ roboco_message_send(...)
✅ roboco_channel_history(...)
✅ roboco_journal_entry(...)

✅ roboco_kb_search(query, top_k)       → Search knowledge base
✅ roboco_rag_query(query)              → AI-generated answer from KB
✅ roboco_kb_stats()                    → What's indexed
✅ roboco_tokens_estimate(content)      → Estimate token count
```

### Rules
```
⚠️ Cannot QA tasks you developed (self-review prevention)
⚠️ Can only claim from awaiting_qa status
```

---

## Documenter (be-doc, fe-doc, ux-doc)

### Your Flow
```
SCAN (awaiting_documentation) → CLAIM → START → WRITE → DOCS_COMPLETE
```

### Your Tools
```
✅ roboco_task_scan(team="backend")  → Look for awaiting_documentation
✅ roboco_task_get(task_id)
✅ roboco_task_claim(task_id)         → From awaiting_documentation or pending
✅ roboco_task_start(task_id)
✅ roboco_task_progress(task_id, message, percentage)
✅ roboco_task_docs_complete(task_id)
✅ roboco_task_substitute(task_id, reason, details)  → Graceful exit

✅ roboco_journal_read_team(agent_slug)  → Read dev's journey
✅ roboco_message_send(...)
✅ roboco_channel_history(...)
✅ roboco_journal_entry(...)

✅ roboco_kb_search(query, top_k)       → Search knowledge base
✅ roboco_rag_query(query)              → AI-generated answer from KB
✅ roboco_kb_stats()                    → What's indexed
✅ roboco_kb_index_docs(sources)        → Index docs for search
✅ roboco_tokens_estimate(content)      → Estimate token count
```

### Rules
```
⚠️ Cannot document tasks you developed (self-review prevention)
⚠️ Read developer's journal for context
```

---

## Cell PM (be-pm, fe-pm, ux-pm)

### Your Flow
```
SCAN → CLAIM → START → PLAN → CREATE SUBTASKS → ACTIVATE → NOTIFY → MONITOR → COMPLETE
```

### Your Tools
```
✅ roboco_task_scan(team="backend")
✅ roboco_task_get(task_id)
✅ roboco_task_claim(task_id)
✅ roboco_task_start(task_id)
✅ roboco_task_plan(task_id, approach, steps)
✅ roboco_task_progress(task_id, message, percentage)
✅ roboco_task_create(data)            → Create subtasks
✅ roboco_task_assign(task_id, agent)  → Assign to cell members
✅ roboco_task_activate(task_id)       → backlog → pending
✅ roboco_task_complete(task_id)       → Final completion
✅ roboco_task_unblock(task_id)        → Unblock any cell task
✅ roboco_task_pause(task_id, ...)
✅ roboco_task_cancel(task_id, reason)

✅ roboco_task_substitute(task_id, reason, details)  → Graceful exit

✅ roboco_session_create_for_tasks(data)
✅ roboco_session_link_task(data)
✅ roboco_session_unlink_task(data)
✅ roboco_session_get_for_task(task_id)
✅ roboco_group_create(data)

✅ roboco_notify_send(recipient, type, task_id, message)
✅ roboco_notify_list()
✅ roboco_notify_ack(notification_id)

✅ roboco_journal_read_team(agent_slug)  → Read cell member journals
✅ roboco_message_send(...)
✅ roboco_channel_history(...)

✅ roboco_kb_search(query, top_k)       → Search knowledge base
✅ roboco_rag_query(query)              → AI-generated answer from KB
✅ roboco_kb_stats()                    → What's indexed
✅ roboco_kb_index_code(sources)        → Index code for search
✅ roboco_kb_index_docs(sources)        → Index docs for search
✅ roboco_tokens_estimate(content)      → Estimate token count
```

### Your Channels
```
#backend-cell (or #frontend-cell, #uxui-cell)
#pm-all
#dev-all (read/write)
#qa-all (read/write)
#doc-all (read/write)
```

---

## Main PM

### Your Flow
```
RECEIVE FROM BOARD → TRIAGE → CREATE CELL SUBTASKS → CREATE SESSION → ACTIVATE → NOTIFY CELL PMs → MONITOR → COMPLETE
```

### Your Tools
All Cell PM tools PLUS:
```
✅ Can work across ALL cells
✅ Can notify anyone
✅ Coordinates cross-cell work
```

### Your Channels
```
#main-pm-board
#pm-all
All cell channels (read)
#announcements (write)
```

---

## Quick Status Reference

```
BACKLOG ────activate────► PENDING ────claim────► CLAIMED ────start────► IN_PROGRESS
                                                                              │
                          ┌───────────────────────────────────────────────────┤
                          │                                                   │
                     BLOCKED/PAUSED                                      (working)
                          │                                                   │
                          └───────────────────────────────────────────────────┤
                                                                              │
                                                             ────verify────► VERIFYING
                                                                              │
                                                             ────submit_qa──► AWAITING_QA
                                                                              │
                                               ┌──────────────────────────────┤
                                               │                              │
                                         (qa_fail)                      (qa_pass)
                                               │                              │
                                         NEEDS_REVISION              AWAITING_DOCUMENTATION
                                               │                              │
                                               └──────────────────────────────┤
                                                                              │
                                                             ────docs_complete──► AWAITING_PM_REVIEW
                                                                              │
                                                             ────complete────► COMPLETED
```
