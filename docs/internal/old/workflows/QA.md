# QA Workflow

## Overview

QA agents (be-qa, fe-qa, ux-qa) verify developer work meets acceptance criteria.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           QA WORKFLOW                                   │
└─────────────────────────────────────────────────────────────────────────┘

1. SCAN FOR WORK
   │
   │  roboco_task_scan(team="backend")
   │
   │  Look for:
   │  └── Tasks in "awaiting_qa" status
   │
   ▼
2. CLAIM TASK
   │
   │  roboco_task_claim(task_id)
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ QA can ONLY claim tasks in "awaiting_qa"                        │
   │  │                                                                 │
   │  │ BEFORE:                                                         │
   │  │   status: awaiting_qa                                           │
   │  │   assigned_to: original_developer                               │
   │  │                                                                 │
   │  │ AFTER:                                                          │
   │  │   status: claimed                                               │
   │  │   assigned_to: qa_agent                                         │
   │  │   (original_developer stored in quick_context)                  │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
3. START REVIEW
   │
   │  roboco_task_start(task_id)
   │
   │  # REQUIRED: Announce to cell
   │  roboco_message_send({
   │    channel: "backend-cell",
   │    content: "Starting QA review of [task title]",
   │    task_id: task_id
   │  })
   │
   │  STATUS: claimed → in_progress
   │
   ▼
4. GATHER CONTEXT (before reviewing)
   │
   │  # REQUIRED: Read developer's journey
   │  roboco_journal_read_team(original_developer, task_id=task_id)
   │  roboco_kb_search("similar implementations")
   │  roboco_channel_history("backend-cell")  # Related discussions
   │
   ▼
5. REVIEW WORK
   │
   │  ┌─────────────────────────────────────────────────────────────────┐
   │  │ Review checklist:                                               │
   │  │ ├── Read developer's handoff notes                              │
   │  │ ├── Check acceptance criteria                                   │
   │  │ ├── Run tests                                                   │
   │  │ ├── Verify functionality                                        │
   │  │ └── Check code quality                                          │
   │  │                                                                 │
   │  │ REQUIRED - Progress updates:                                    │
   │  │   roboco_task_progress(task_id, "Reviewing X", 50)              │
   │  │                                                                 │
   │  │ REQUIRED - Journal your review:                                 │
   │  │   roboco_journal_entry({type: "qa_review", ...})                │
   │  │   roboco_journal_decision(...)  # If making judgment calls      │
   │  │   roboco_journal_struggle(...)  # If issues found               │
   │  └─────────────────────────────────────────────────────────────────┘
   │
   ▼
6. REFLECT (before decision)
   │
   │  # REQUIRED before pass/fail
   │  roboco_journal_reflect({
   │    task_id: task_id,
   │    what_done: "Reviewed X, Y, Z",
   │    what_learned: "Discovered patterns...",
   │    what_struggled: "Edge cases were unclear"
   │  })
   │
   ▼
7. DECISION
   │
   ├──── PASS ────────────────────────────────────────────────────────┐
   │                                                                  │
   │     roboco_task_qa_pass(task_id, {                               │
   │       notes: "All acceptance criteria met. Tests pass."          │
   │     })                                                           │
   │                                                                  │
   │     STATUS: in_progress → awaiting_documentation                 │
   │     → Documenter takes over                                      │
   │                                                                  │
   └──── FAIL ────────────────────────────────────────────────────────┐
                                                                      │
         roboco_task_qa_fail(task_id, {                               │
           notes: "Issues found",                                     │
           issues: [                                                  │
             "Bug: X doesn't work",                                   │
             "Missing: Y not implemented"                             │
           ]                                                          │
         })                                                           │
                                                                      │
         STATUS: in_progress → needs_revision                         │
         ASSIGNED_TO: back to original_developer                      │
         → Developer fixes and resubmits                              │
   │
   ▼
   DONE (for QA)
```

## Self-Review Prevention

```
┌─────────────────────────────────────────────────────────────────────────┐
│ QA CANNOT review tasks they originally developed                        │
│                                                                         │
│ System tracks original_developer in quick_context                       │
│ If QA agent == original_developer → FORBIDDEN                           │
└─────────────────────────────────────────────────────────────────────────┘
```

## Status Transitions (QA perspective)

```
CAN CLAIM:
  awaiting_qa ──claim──► claimed

AFTER CLAIM:
  claimed ──start──► in_progress

DECISIONS:
  in_progress ──qa_pass──► awaiting_documentation
  in_progress ──qa_fail──► needs_revision
```

## Using Knowledge Base

Before reviewing, search for context:

```python
roboco_kb_search("similar past reviews")       # Find related QA work
roboco_rag_query("what are common issues?")    # AI-generated insights
roboco_journal_search("qa patterns")           # Your past reviews
```

See [KNOWLEDGE_BASE.md](./KNOWLEDGE_BASE.md) for full documentation.

## Agent-to-Agent (A2A) Tools

QA can collaborate directly with other agents:

```python
roboco_agent_discover(role, team, skill)     # Find agents who can help
roboco_agent_request(target_agent, skill, message)  # Request work
roboco_agent_request_status(a2a_task_id)     # Check request progress
```

**When to use A2A:**
- Need dev clarification? → `roboco_agent_request("be-dev-1", "code_review", "Can you explain...")`
- Need security review? → `roboco_agent_discover(skill="security_audit")`

## Key Rules

1. **Only claim awaiting_qa** - Can't claim pending tasks
2. **Cannot self-review** - Can't QA your own dev work
3. **MESSAGE when starting** - Announce to cell channel
4. **READ dev's journey** - roboco_journal_read_team() REQUIRED
5. **JOURNAL your review** - Document what was tested and why
6. **REFLECT before decision** - roboco_journal_reflect() REQUIRED
7. **Clear fail reasons** - Developer needs to know what to fix
8. **Cannot COMPLETE** - Only PM completes after docs
