# RoboCo Workflows & Permissions

Visual documentation of task lifecycles, permissions, and workflows.

## 1. Task Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> pending: Task Created

    pending --> claimed: Developer claims
    pending --> cancelled: PM cancels

    claimed --> in_progress: Developer starts
    claimed --> pending: Developer unclaims
    claimed --> cancelled: PM cancels

    in_progress --> blocked: Developer blocked
    in_progress --> paused: Developer pauses
    in_progress --> verifying: Developer self-verifies
    in_progress --> cancelled: PM cancels

    blocked --> in_progress: Unblocked
    blocked --> cancelled: PM cancels

    paused --> in_progress: Developer resumes
    paused --> cancelled: PM cancels

    verifying --> awaiting_qa: Submit for QA
    verifying --> needs_revision: Self-found issues
    verifying --> awaiting_documentation: Skip QA (small tasks)
    verifying --> cancelled: PM cancels

    awaiting_qa --> awaiting_documentation: QA PASS
    awaiting_qa --> needs_revision: QA FAIL
    awaiting_qa --> blocked: Blocked during QA
    awaiting_qa --> cancelled: PM cancels

    needs_revision --> in_progress: Developer resumes
    needs_revision --> cancelled: PM cancels

    awaiting_documentation --> awaiting_pm_review: Documenter marks docs done
    awaiting_documentation --> cancelled: PM cancels

    awaiting_pm_review --> completed: PM completes
    awaiting_pm_review --> cancelled: PM cancels

    completed --> [*]
    cancelled --> [*]

    quarantined --> pending: Un-quarantined
```

## 2. Agent Hierarchy & Roles

```
                    +-------+
                    |  CEO  |
                    +-------+
                        |
        +---------------+---------------+
        |               |               |
   +---------+    +-----------+    +---------+
   | Product |    |   Head    |    | Auditor |
   | Owner   |    | Marketing |    | (silent)|
   +---------+    +-----------+    +---------+
        |               |               |
        +-------+-------+               |
                |                       |
           +---------+                  |
           | Main PM |<-----------------+
           +---------+      (observes all)
                |
    +-----------+-----------+
    |           |           |
+-------+   +-------+   +-------+
| BE PM |   | FE PM |   | UX PM |
+-------+   +-------+   +-------+
    |           |           |
+-------+   +-------+   +-------+
|Backend|   |Frontend|  | UX/UI |
| Cell  |   | Cell   |  | Cell  |
+-------+   +-------+   +-------+

Each Cell:
  - 2 Developers (BE/FE) or 1 Developer (UX)
  - 1 QA Engineer
  - 1 Documenter
  - 1 Cell PM
```

## 3. Notification Permissions

```
WHO CAN SEND NOTIFICATIONS:

+------------------+-------------+----------------------------------------------+
| Sender Role      | Can Send?   | Scope                                        |
+------------------+-------------+----------------------------------------------+
| CEO              | YES         | Anyone                                       |
| Auditor          | YES         | Anyone                                       |
| Main PM          | YES         | Anyone                                       |
| Product Owner    | YES         | main-pm, head-marketing, auditor, ceo        |
| Head Marketing   | YES         | main-pm, product-owner, auditor, ceo         |
| Cell PM          | YES         | Own cell only                                |
+------------------+-------------+----------------------------------------------+
| Developer        | NO          | -                                            |
| QA               | NO          | -                                            |
| Documenter       | NO          | -                                            |
+------------------+-------------+----------------------------------------------+

TOOLS VISIBILITY:

+----------------------+------------+----------+---------+---------+---------+
| Tool                 | Dev/QA/Doc | Cell PM  | Main PM | Board   | Aud/CEO |
+----------------------+------------+----------+---------+---------+---------+
| roboco_notify_list   | YES        | YES      | YES     | YES     | YES     |
| roboco_notify_get    | YES        | YES      | YES     | YES     | YES     |
| roboco_notify_ack    | YES        | YES      | YES     | YES     | YES     |
| roboco_notify_send   | HIDDEN     | YES      | YES     | YES     | YES     |
| roboco_escalate      | HIDDEN     | YES      | YES     | HIDDEN  | HIDDEN  |
| roboco_request_appr  | HIDDEN     | YES      | YES     | YES     | HIDDEN  |
+----------------------+------------+----------+---------+---------+---------+

Note: "Board" = Product Owner + Head Marketing. Auditor/CEO can send but not escalate or request approval.
```

## 4. QA Fail → Revision Workflow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Task as Task System
    participant QA as QA Engineer
    participant PM as Cell PM

    Dev->>Task: Submit for QA (awaiting_qa)
    Note over Task: assigned_to = QA<br/>quick_context = original_developer:Dev

    QA->>Task: Claim task
    QA->>Task: Review work

    alt QA PASS
        QA->>Task: roboco_task_qa_pass()
        Task->>Task: status = awaiting_documentation
        Note over Task: Documenter claims and writes docs
        Note over Task: Documenter calls roboco_task_docs_complete()
        Task->>Task: status = awaiting_pm_review
        Note over Task: PM claims, reviews, calls roboco_task_complete()
        Task->>Task: status = completed
    else QA FAIL
        QA->>Task: roboco_task_qa_fail(issues)
        Task->>Task: status = needs_revision
        Task->>Task: assigned_to = original Dev (from quick_context)
        Note over Dev: Dev sees task in needs_revision
        Dev->>Task: roboco_task_start()
        Task->>Task: status = in_progress
        Dev->>Task: Fix issues, resubmit
    end
```

## 5. Block/Unblock Workflow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Task as Task System
    participant PM as Cell PM

    Dev->>Task: Working on task (in_progress)

    Note over Dev: Encounters blocker

    Dev->>Task: roboco_task_block(reason, type, what_needed)
    Task->>Task: POST /tasks/{id}/soft-block
    Task->>Task: status = blocked
    Task->>Task: dev_notes += blocker info

    Note over Dev: Can work on other tasks

    alt Blocker resolved
        Dev->>Task: roboco_task_unblock()
        Task->>Task: POST /tasks/{id}/unblock
        Task->>Task: status = in_progress
        Dev->>Task: Continue working
    else Need PM help
        Dev->>PM: roboco_report_blocker() via message channel
        PM->>Task: Resolves blocker
        Dev->>Task: roboco_task_unblock()
    end
```

## 6. Task Role Restrictions

```
ROLE-BASED TRANSITIONS:

+-------------------------------+-------------------------------------------+
| Transition                    | Allowed Roles                             |
+-------------------------------+-------------------------------------------+
| awaiting_qa → awaiting_doc    | QA only                                   |
| awaiting_qa → needs_rev       | QA only                                   |
| awaiting_doc → awaiting_pm    | Documenter only                           |
| awaiting_pm → completed       | Cell PM, Main PM, Product Owner, Head Mkt |
| * → cancelled                 | Cell PM, Main PM, Product Owner, Head Mkt |
+-------------------------------+-------------------------------------------+

Note: CEO and Auditor are NOT in the cancel/complete roles list - they observe but don't directly act on tasks.

VALID START STATUSES (for roboco_task_start):

+------------------+------------------------------------------+
| Status           | Who Can Start                            |
+------------------+------------------------------------------+
| claimed          | Assigned developer (requires plan)       |
| paused           | Assigned developer (resume)              |
| needs_revision   | Original developer (fix QA issues)       |
+------------------+------------------------------------------+
```

## 7. Escalation Chain

```
Developer/QA/Doc → Cell PM → Main PM → Product Owner → CEO

+------------+     +---------+     +---------+     +---------------+     +-----+
| be-dev-1   |---->|         |     |         |     |               |     |     |
| be-dev-2   |---->|  be-pm  |---->|         |     |               |     |     |
| be-qa      |---->|         |     |         |     |               |     |     |
| be-doc     |---->|         |     |         |     |               |     |     |
+------------+     +---------+     |         |     |               |     |     |
                                   | main-pm |---->| product-owner |---->| CEO |
+------------+     +---------+     |         |     |               |     |     |
| fe-dev-1   |---->|         |     |         |     |               |     |     |
| fe-dev-2   |---->|  fe-pm  |---->|         |     |               |     |     |
| fe-qa      |---->|         |     |         |     |               |     |     |
| fe-doc     |---->|         |     |         |     |               |     |     |
+------------+     +---------+     +---------+     +---------------+     +-----+
```

## 8. Communication vs Notification

```
+-------------------+----------------------------------+----------------------------------+
| Mechanism         | Who Can Use                      | Purpose                          |
+-------------------+----------------------------------+----------------------------------+
| Messages          | Everyone                         | Constant stream, logged          |
| (roboco_message)  |                                  | discussions, updates             |
+-------------------+----------------------------------+----------------------------------+
| Blocker Reports   | Everyone                         | Signal blocked status            |
| (roboco_report_   |                                  | PM auto-notified                 |
| blocker)          |                                  |                                  |
+-------------------+----------------------------------+----------------------------------+
| Notifications     | PM, Board, Auditor, CEO          | Formal signals requiring         |
| (roboco_notify)   |                                  | acknowledgment                   |
+-------------------+----------------------------------+----------------------------------+
| Escalations       | PMs only                         | High-priority issues             |
| (roboco_escalate) |                                  | up the chain                     |
+-------------------+----------------------------------+----------------------------------+
```
