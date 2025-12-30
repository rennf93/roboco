# Workflow Documentation

> **Status:** Implemented
>
> RoboCo workflow documentation for all agent roles.

## Quick Start

| I am a... | Start here |
|-----------|------------|
| Developer | [DEVELOPER.md](./DEVELOPER.md) -> [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| QA | [QA.md](./QA.md) -> [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| Documenter | [DOCUMENTER.md](./DOCUMENTER.md) -> [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) |
| Cell PM | [PM.md](./PM.md) -> [PERMISSIONS.md](./PERMISSIONS.md) |
| Main PM | [PM.md](./PM.md) -> [PERMISSIONS.md](./PERMISSIONS.md) |

---

## Documentation Index

### Core Workflows

| Document | Description | Status |
|----------|-------------|--------|
| [STATUS_TRANSITIONS.md](./STATUS_TRANSITIONS.md) | Complete task lifecycle diagram | Implemented |
| [PM.md](./PM.md) | Main PM and Cell PM workflows | Implemented |
| [DEVELOPER.md](./DEVELOPER.md) | Developer workflow | Implemented |
| [QA.md](./QA.md) | QA workflow | Implemented |
| [DOCUMENTER.md](./DOCUMENTER.md) | Documenter workflow | Implemented |

### Reference

| Document | Description | Status |
|----------|-------------|--------|
| [PERMISSIONS.md](./PERMISSIONS.md) | Tool, channel, notification permissions | Implemented |
| [AGENT_CHEATSHEET.md](./AGENT_CHEATSHEET.md) | Quick reference per role | Implemented |

### Activities

| Document | Description | Status |
|----------|-------------|--------|
| [JOURNALING.md](./JOURNALING.md) | Journal API usage and entry types | Implemented |
| [COMMUNICATION.md](./COMMUNICATION.md) | Messages and channels | Implemented |
| [ESCALATION.md](./ESCALATION.md) | Task escalation and CEO approval workflow | Implemented |
| [KNOWLEDGE_BASE.md](./KNOWLEDGE_BASE.md) | Searching past work | Implemented |
| [GIT_WORKFLOW.md](./GIT_WORKFLOW.md) | Multi-agent workspaces, branching, PRs | Implemented |

### Bug Tracking

| Document | Description |
|----------|-------------|
| [BUGS.md](./BUGS.md) | Known issues and fixes |

---

## The Big Picture

```
                                 ROBOCO WORKFLOW
--------------------------------------------------------------------------------

                                  BOARD/CEO
                                      |
                                      | Creates initiative
                                      v
                                  MAIN PM
                                      |
                      +---------------+---------------+
                      |               |               |
                      v               v               v
                   BE-PM           FE-PM           UX-PM
                      |               |               |
           +----------+----------+    |    +----------+----------+
           |          |          |    |    |          |          |
           v          v          v    |    v          v          v
        BE-DEV-1   BE-DEV-2   BE-QA   |  FE-DEV-1  FE-DEV-2   FE-QA
           |          |          |    |    |          |          |
           +----+-----+          |    |    +----+-----+          |
                |                |    |         |                |
                v                v    |         v                v
           SUBMITS TO QA ----> REVIEWS|    SUBMITS TO QA ----> REVIEWS
                |                |    |         |                |
                v                v    |         v                v
           BE-DOC <------ QA PASSES   |    FE-DOC <------ QA PASSES
                |                     |         |
                v                     |         v
           AWAITING_PM_REVIEW <-------+--- AWAITING_PM_REVIEW
                |                     |         |
                +---------------------+---------+
                                      |
                                      v
                                  COMPLETED
```

---

## Task Lifecycle Summary

```
BACKLOG --> PENDING --> CLAIMED --> IN_PROGRESS --> VERIFYING --> AWAITING_QA
                                                                       |
                                    +----------------------------------+----------------------------------+
                                    |                                                                     |
                              QA PASSES                                                              QA FAILS
                                    |                                                                     |
                                    v                                                                     v
                        AWAITING_DOCUMENTATION                                                     NEEDS_REVISION
                                    |                                                                     |
              +---------------------+---------------------+                                        (back to dev)
              |                                           |
         DOCUMENTER                                  DEVELOPER
         writes docs                                 creates PR
              |                                           |
              v                                           v
         docs_complete=True                        pr_created=True
              |                                           |
              +---------------------+---------------------+
                                    |
                          BOTH must be true
                                    |
                                    v
                          AWAITING_PM_REVIEW
                                    |
                    +---------------+---------------+
                    |                               |
              PM COMPLETES                     PM ESCALATES
                    |                               |
                    v                               v
               COMPLETED               AWAITING_CEO_APPROVAL
                                                    |
                                    +---------------+---------------+
                                    |                               |
                              CEO APPROVES                    CEO REJECTS
                                    |                               |
                                    v                               v
                               COMPLETED                     NEEDS_REVISION
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

## Multi-Agent Workspace Structure

Each agent gets their own git workspace:

```
/data/workspaces/
+-- {project-slug}/
    +-- {team}/
        +-- {agent-slug}/
            +-- [git repo files]
```

Example:
```
/data/workspaces/roboco/backend/be-dev-1/
/data/workspaces/roboco/backend/be-dev-2/
/data/workspaces/roboco/frontend/fe-dev-1/
```

This allows multiple agents to work on the same project in parallel, each on their own branch.

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

# Git operations
roboco_git_commit(project_slug, task_id, "add feature X")
roboco_git_push(project_slug)

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

---

## Escalation Chain

```
Developer/QA/Documenter --> Cell PM --> Main PM --> Product Owner --> CEO
```

See [ESCALATION.md](./ESCALATION.md) for details on:
- Task escalation (`roboco_task_escalate`)
- CEO approval workflow (`roboco_task_escalate_to_ceo`)
- Soft blocking (`roboco_task_soft_block`)
- Force completion (CEO only)

---

## Git Workflow

See [GIT_WORKFLOW.md](./GIT_WORKFLOW.md) for details on:
- Multi-agent workspace structure
- Branch naming conventions (`{type}/{team}/{task-id}`)
- Commit linking to tasks
- PR creation and merge workflow
- Parallel documentation phase

---

## Journal API

See [JOURNALING.md](./JOURNALING.md) for details on:
- Journal entry types (decision, reflection, learning, struggle)
- Semantic search
- Growth metrics
- Access permissions by role
