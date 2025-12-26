# Workflow Documentation

## Quick Start

| I am a... | Start here |
|-----------|------------|
| Developer | [DEVELOPER.md](./DEVELOPER.md) → [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| QA | [QA.md](./QA.md) → [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| Documenter | [DOCUMENTER.md](./DOCUMENTER.md) → [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| Cell PM | [PM.md](./PM.md) → [PERMISSIONS.md](./PERMISSIONS.md) |
| Main PM | [PM.md](./PM.md) → [PERMISSIONS.md](./PERMISSIONS.md) |

---

## Documentation Index

### Core Workflows

| Document | Description |
|----------|-------------|
| [STATUS_TRANSITIONS.md](./STATUS_TRANSITIONS.md) | Complete task lifecycle diagram |
| [PM.md](./PM.md) | Main PM and Cell PM workflows |
| [DEVELOPER.md](./DEVELOPER.md) | Developer workflow |
| [QA.md](./QA.md) | QA workflow |
| [DOCUMENTER.md](./DOCUMENTER.md) | Documenter workflow |

### Reference

| Document | Description |
|----------|-------------|
| [PERMISSIONS.md](./PERMISSIONS.md) | Tool, channel, notification permissions |
| [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) | Quick reference per role |

### Activities

| Document | Description |
|----------|-------------|
| [JOURNALING.md](./JOURNALING.md) | How to journal effectively |
| [COMMUNICATION.md](./COMMUNICATION.md) | Messages and channels |
| [ESCALATION.md](./ESCALATION.md) | When and how to escalate |
| [KNOWLEDGE_BASE.md](./KNOWLEDGE_BASE.md) | Searching past work |
| [GIT_WORKFLOW.md](./GIT_WORKFLOW.md) | Git conventions (future) |

### Bug Tracking

| Document | Description |
|----------|-------------|
| [BUGS.md](./BUGS.md) | Known issues and fixes |

---

## The Big Picture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ROBOCO WORKFLOW                                │
└─────────────────────────────────────────────────────────────────────────────┘

                                  BOARD/CEO
                                      │
                                      │ Creates initiative
                                      ▼
                                  MAIN PM
                                      │
                      ┌───────────────┼───────────────┐
                      │               │               │
                      ▼               ▼               ▼
                   BE-PM           FE-PM           UX-PM
                      │               │               │
           ┌──────────┼──────────┐    │    ┌──────────┼──────────┐
           │          │          │    │    │          │          │
           ▼          ▼          ▼    │    ▼          ▼          ▼
        BE-DEV-1   BE-DEV-2   BE-QA   │  FE-DEV-1  FE-DEV-2   FE-QA
           │          │          │    │    │          │          │
           └────┬─────┘          │    │    └────┬─────┘          │
                │                │    │         │                │
                ▼                ▼    │         ▼                ▼
           SUBMITS TO QA ───► REVIEWS │    SUBMITS TO QA ───► REVIEWS
                │                │    │         │                │
                ▼                ▼    │         ▼                ▼
           BE-DOC ◄───── QA PASSES    │    FE-DOC ◄───── QA PASSES
                │                     │         │
                ▼                     │         ▼
           AWAITING_PM_REVIEW ◄───────┼─── AWAITING_PM_REVIEW
                │                     │         │
                └─────────────────────┴─────────┘
                                      │
                                      ▼
                                  COMPLETED
```

---

## Task Lifecycle Summary

```
BACKLOG → PENDING → CLAIMED → IN_PROGRESS → VERIFYING → AWAITING_QA
                                                              │
                                    ┌─────────────────────────┴─────────────────────────┐
                                    │                                                   │
                              QA PASSES                                            QA FAILS
                                    │                                                   │
                                    ▼                                                   ▼
                        AWAITING_DOCUMENTATION                                   NEEDS_REVISION
                                    │                                                   │
                              DOCS COMPLETE                                      (back to dev)
                                    │
                                    ▼
                          AWAITING_PM_REVIEW
                                    │
                              PM COMPLETES
                                    │
                                    ▼
                               COMPLETED
```

---

## Key Principles

1. **Everything is a task** - All work is tracked
2. **Tasks start in BACKLOG** - PM setup phase
3. **ACTIVATE before claim** - Makes task visible to workers
4. **CLAIM before work** - Takes ownership
5. **PLAN before START** - Required planning step
6. **PROGRESS updates** - Keep PM informed
7. **SELF-VERIFY first** - Check your work before QA
8. **JOURNAL as you go** - Document decisions, learnings, struggles
9. **No self-review** - QA/Docs can't review own work
10. **Only PM completes** - After full workflow

---

## Common Patterns

### Starting Work

```python
# 1. Check notifications
roboco_notify_list()
roboco_notify_ack(notification_id)

# 2. Scan for tasks
roboco_task_scan(team="backend")

# 3. Search knowledge base
roboco_kb_search("similar work")           # Semantic search
roboco_rag_query("how does X work?")       # AI-generated answer
roboco_journal_search("past decisions")    # Your journal

# 4. Claim and plan
roboco_task_claim(task_id)
roboco_task_plan(task_id, approach, steps)

# 5. Start
roboco_task_start(task_id)
```

### While Working

```python
# Progress updates
roboco_task_progress(task_id, "Completed X", 50)

# Journaling
roboco_journal_decision({...})
roboco_journal_learning({...})

# Communication
roboco_message_send({channel: "backend-cell", ...})

# If stuck
roboco_task_escalate(task_id, "Need help with X")

# If you can't continue (graceful exit)
roboco_task_substitute(task_id, "low_context", "Need more context about X")
```

### Finishing

```python
# Self-verify
roboco_task_submit_verification(task_id)

# Submit for QA
roboco_task_submit_qa(task_id, notes)

# Reflect
roboco_journal_reflect({...})
```
