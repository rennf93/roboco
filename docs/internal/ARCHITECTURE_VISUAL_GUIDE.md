# RoboCo Architecture Visual Guide

> Comprehensive visual documentation of the RoboCo AI Agentic Company system.
> Last Updated: December 29, 2025

---

## Table of Contents

1. [System Overview Mind Map](#1-system-overview-mind-map)
2. [Agent Hierarchy Diagram](#2-agent-hierarchy-diagram)
3. [Task Lifecycle Flowchart](#3-task-lifecycle-flowchart)
4. [Permissions Matrix](#4-permissions-matrix)
5. [Communication Flow Diagram](#5-communication-flow-diagram)
6. [Services Dependency Graph](#6-services-dependency-graph)
7. [Data Model Entity Relationships](#7-data-model-entity-relationships)
8. [API Route Structure](#8-api-route-structure)
9. [Workflow Enforcement Diagram](#9-workflow-enforcement-diagram)

---

## 1. System Overview Mind Map

```mermaid
mindmap
  root((RoboCo))
    Agents[18 AI Agents]
      Board[Board - 3]
        Product Owner
        Head Marketing
        Auditor
      MainPM[Main PM - 1]
      Backend[Backend Cell - 5]
        be-dev-1
        be-dev-2
        be-qa
        be-pm
        be-doc
      Frontend[Frontend Cell - 5]
        fe-dev-1
        fe-dev-2
        fe-qa
        fe-pm
        fe-doc
      UXUI[UX/UI Cell - 4]
        ux-dev-1
        ux-qa
        ux-pm
        ux-doc
    Services[Core Services]
      Task Management
      Messaging
      Notifications
      RAG/Knowledge
      Journals
      Permissions
    Infrastructure
      PostgreSQL
      Redis Streams
      WebSocket
      Docker
    Protocols
      A2A Protocol
      MCP Tools
      REST API
```

### Architecture Layers

```mermaid
graph TB
    subgraph "Presentation Layer"
        API[FastAPI REST API]
        WS[WebSocket Server]
        MCP[MCP Tool Servers]
    end

    subgraph "Service Layer"
        TS[TaskService]
        MS[MessagingService]
        NS[NotificationService]
        OS[OptimalService RAG]
        JS[JournalService]
        PS[PermissionService]
        AS[AuditService]
    end

    subgraph "Enforcement Layer"
        TL[Task Lifecycle]
        TO[Task Ownership]
        CA[Channel Access]
        JP[Journal Permissions]
        NP[Notification Permissions]
    end

    subgraph "Data Layer"
        DB[(PostgreSQL)]
        RD[(Redis)]
        VDB[(Vector DB)]
    end

    subgraph "Runtime Layer"
        ORCH[Orchestrator]
        AGENTS[Agent Instances]
    end

    API --> TS & MS & NS & OS & JS
    WS --> MS & NS
    MCP --> TS & MS & JS

    TS --> TL & TO
    MS --> CA
    NS --> NP
    JS --> JP

    TS & MS & NS & JS --> DB
    MS & NS --> RD
    OS --> VDB

    ORCH --> AGENTS
    AGENTS --> MCP
```

---

## 2. Agent Hierarchy Diagram

### Organizational Structure

```mermaid
graph TB
    CEO[CEO - Renzo<br/>Human Executive]

    subgraph BOARD["Board (3 Agents)"]
        PO[Product Owner]
        HM[Head of Marketing]
        AUD[Auditor<br/>Silent Observer]
    end

    MAIN_PM[Main PM<br/>Cross-Cell Coordinator]

    subgraph BACKEND["Backend Cell (5 Agents)"]
        BE_PM[Cell PM]
        BE_DEV1[Developer 1]
        BE_DEV2[Developer 2]
        BE_QA[QA]
        BE_DOC[Documenter]
    end

    subgraph FRONTEND["Frontend Cell (5 Agents)"]
        FE_PM[Cell PM]
        FE_DEV1[Developer 1]
        FE_DEV2[Developer 2]
        FE_QA[QA]
        FE_DOC[Documenter]
    end

    subgraph UXUI["UX/UI Cell (4 Agents)"]
        UX_PM[Cell PM]
        UX_DEV1[Developer 1]
        UX_QA[QA]
        UX_DOC[Documenter]
    end

    CEO --> BOARD
    AUD -.->|Reports to| CEO
    BOARD --> MAIN_PM
    MAIN_PM --> BACKEND & FRONTEND & UXUI

    BE_PM --> BE_DEV1 & BE_DEV2 & BE_QA & BE_DOC
    FE_PM --> FE_DEV1 & FE_DEV2 & FE_QA & FE_DOC
    UX_PM --> UX_DEV1 & UX_QA & UX_DOC

    style CEO fill:#ff6b6b
    style BOARD fill:#feca57
    style MAIN_PM fill:#48dbfb
    style BACKEND fill:#1dd1a1
    style FRONTEND fill:#5f27cd
    style UXUI fill:#ff9ff3
```

### Escalation Chain

```mermaid
graph LR
    subgraph "Escalation Path"
        DEV[Developer/QA/Doc] -->|Escalates to| CPM[Cell PM]
        CPM -->|Escalates to| MPM[Main PM]
        MPM -->|Escalates to| PO[Product Owner]
        PO -->|Escalates to| CEO[CEO]
    end

    style DEV fill:#1dd1a1
    style CPM fill:#48dbfb
    style MPM fill:#48dbfb
    style PO fill:#feca57
    style CEO fill:#ff6b6b
```

### Agent Roles and Capabilities

| Role | Team | Can Create Tasks | Can Assign | Can Cancel | Can Notify | Permission Level |
|------|------|------------------|------------|------------|------------|------------------|
| CEO | - | Yes | Yes | Yes* | Yes | L0 (Full Access) |
| Product Owner | Board | Yes | Yes | Yes | Yes | L1 (Cross-Org) |
| Head Marketing | Board | Yes | Yes | Yes | Yes | L1 (Cross-Org) |
| Auditor | Board | Yes | Yes | No* | Yes | L99 (Silent Read All) |
| Main PM | - | Yes | Yes | Yes | Yes | L2 (All Cells) |
| Cell PM | Cell | Yes | Yes | Yes | Yes | L3 (Own Cell + PM) |
| Developer | Cell | No | No | No | No | L4 (Own Cell) |
| QA | Cell | No | No | No | No | L4 (Own Cell) |
| Documenter | Cell | No | No | No | No | L4 (Own Cell) |

*CEO and Auditor observe only - they don't actively cancel tasks

---

## 3. Task Lifecycle Flowchart

### Complete State Machine

```mermaid
stateDiagram-v2
    [*] --> BACKLOG : Task Created

    BACKLOG --> PENDING : PM Activates
    BACKLOG --> CANCELLED : PM Cancels

    PENDING --> CLAIMED : Agent Claims
    PENDING --> CANCELLED : PM Cancels

    CLAIMED --> IN_PROGRESS : Agent Starts<br/>(requires plan)
    CLAIMED --> PENDING : Agent Releases
    CLAIMED --> CANCELLED : PM Cancels

    IN_PROGRESS --> BLOCKED : Hard/Soft Block
    IN_PROGRESS --> PAUSED : Agent Pauses
    IN_PROGRESS --> VERIFYING : Self-Verify
    IN_PROGRESS --> AWAITING_PM_REVIEW : Direct PM Submit
    IN_PROGRESS --> COMPLETED : PM Completes
    IN_PROGRESS --> CANCELLED : PM Cancels

    BLOCKED --> IN_PROGRESS : Unblocked
    BLOCKED --> CANCELLED : PM Cancels

    PAUSED --> IN_PROGRESS : Agent Resumes
    PAUSED --> CANCELLED : PM Cancels

    VERIFYING --> AWAITING_QA : Submit for QA
    VERIFYING --> NEEDS_REVISION : Self-Review Fails
    VERIFYING --> AWAITING_DOCUMENTATION : Skip QA
    VERIFYING --> CANCELLED : PM Cancels

    NEEDS_REVISION --> CLAIMED : Dev Reclaims
    NEEDS_REVISION --> IN_PROGRESS : Dev Continues
    NEEDS_REVISION --> CANCELLED : PM Cancels

    AWAITING_QA --> AWAITING_DOCUMENTATION : QA Passes
    AWAITING_QA --> NEEDS_REVISION : QA Fails
    AWAITING_QA --> BLOCKED : Blocked Issue
    AWAITING_QA --> CANCELLED : PM Cancels

    AWAITING_DOCUMENTATION --> AWAITING_PM_REVIEW : Docs Complete
    AWAITING_DOCUMENTATION --> CLAIMED : Reassign
    AWAITING_DOCUMENTATION --> CANCELLED : PM Cancels

    AWAITING_PM_REVIEW --> COMPLETED : PM Approves
    AWAITING_PM_REVIEW --> CLAIMED : PM Requests Changes
    AWAITING_PM_REVIEW --> CANCELLED : PM Cancels

    COMPLETED --> [*]
    CANCELLED --> [*]

    note right of COMPLETED : Terminal State
    note right of CANCELLED : Terminal State
```

### Simplified Developer Workflow

```mermaid
graph TD
    START((Start)) --> SCAN[SCAN<br/>Check for pending tasks]
    SCAN --> CLAIM[CLAIM<br/>Lock and announce task]
    CLAIM --> UNDERSTAND[UNDERSTAND<br/>Read requirements, ask questions]
    UNDERSTAND --> PLAN[PLAN<br/>Break down, identify dependencies]
    PLAN --> EXECUTE[EXECUTE<br/>Do the work, commit frequently]
    EXECUTE --> VERIFY[VERIFY<br/>Self-check against criteria]
    VERIFY -->|Pass| NOTES[NOTES<br/>Document journey, handoff]
    VERIFY -->|Fail| EXECUTE
    NOTES --> CLOSE[CLOSE<br/>Return to SCAN]
    CLOSE --> SCAN

    EXECUTE -->|Blocked| BLOCKED[BLOCKED<br/>Report blocker]
    BLOCKED -->|Resolved| EXECUTE

    style START fill:#1dd1a1
    style SCAN fill:#48dbfb
    style CLAIM fill:#feca57
    style UNDERSTAND fill:#5f27cd
    style PLAN fill:#ff9ff3
    style EXECUTE fill:#ff6b6b
    style VERIFY fill:#feca57
    style NOTES fill:#48dbfb
    style CLOSE fill:#1dd1a1
```

### Role-Based Status Access

| From Status | To Status | Allowed Roles |
|-------------|-----------|---------------|
| BACKLOG | PENDING | Cell PM, Main PM, Product Owner, Head Marketing |
| AWAITING_QA | Any QA transition | QA only |
| AWAITING_DOCUMENTATION | Any Doc transition | Documenter only |
| AWAITING_PM_REVIEW | COMPLETED | Cell PM, Main PM only |
| Any | CANCELLED | Cell PM, Main PM, Product Owner, Head Marketing |
| Any | COMPLETED | Cell PM, Main PM only |

---

## 4. Permissions Matrix

### Channel Access Matrix

```mermaid
graph TB
    subgraph "Cell Channels"
        BC[#backend-cell]
        FC[#frontend-cell]
        UC[#uxui-cell]
    end

    subgraph "Cross-Cell Channels"
        DA[#dev-all]
        QA[#qa-all]
        PA[#pm-all]
        DOC[#doc-all]
    end

    subgraph "Management Channels"
        MPB[#main-pm-board]
        BP[#board-private]
    end

    subgraph "Broadcast Channels"
        ANN[#announcements]
        AH[#all-hands]
    end

    subgraph "Access Types"
        R((Read))
        W((Write))
        S((Silent))
    end
```

### Detailed Channel Permissions

| Channel | Read Access | Write Access | Silent (Auditor) |
|---------|-------------|--------------|------------------|
| #backend-cell | BE members, Main PM | BE members | Auditor |
| #frontend-cell | FE members, Main PM | FE members | Auditor |
| #uxui-cell | UX members, Main PM | UX members | Auditor |
| #dev-all | All Devs, All PMs | All Devs, Cell PMs, Main PM | Auditor |
| #qa-all | All QA, Cell PMs, Main PM | All QA, Cell PMs | Auditor |
| #pm-all | All PMs | All PMs | Auditor |
| #doc-all | All Docs, Cell PMs, Main PM | All Docs, Cell PMs | Auditor |
| #main-pm-board | Main PM, PO, HM, Auditor | Main PM, PO, HM, Auditor | - |
| #board-private | PO, HM, Auditor, CEO, Main PM | PO, HM, Auditor, CEO | - |
| #announcements | Everyone | Main PM, Board, CEO | - |
| #all-hands | Everyone | Everyone | - |

### Task Permission Matrix

```mermaid
graph LR
    subgraph "Task Actions"
        VA[VIEW_ALL]
        VO[VIEW_OWN]
        CR[CREATE]
        AS[ASSIGN]
        CL[CLAIM]
        UO[UPDATE_OWN]
        CLO[CLOSE]
        CP[CHANGE_PRIORITY]
    end

    subgraph "Roles"
        CEO_R[CEO]
        BOARD_R[Board]
        MPM_R[Main PM]
        CPM_R[Cell PM]
        DEV_R[Developer]
        QA_R[QA]
        DOC_R[Documenter]
    end
```

| Role | VIEW_ALL | VIEW_OWN | CREATE | ASSIGN | CLAIM | UPDATE_OWN | CLOSE | CHANGE_PRIORITY |
|------|----------|----------|--------|--------|-------|------------|-------|-----------------|
| CEO | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| Board | ✓ | ✓ | ✓ | ✓ | - | - | ✓ | ✓ |
| Main PM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cell PM | - | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Developer | - | ✓ | - | - | ✓ | ✓ | ✓ | - |
| QA | - | ✓ | - | - | ✓ | ✓ | - | - |
| Documenter | - | ✓ | - | - | ✓ | ✓ | ✓ | - |

### Knowledge Base Permission Matrix

| Role | INDEX_CODE | INDEX_DOCS | SEARCH | QUERY | VIEW_STATS | CLEAR_INDEX | REFRESH_INDEX |
|------|------------|------------|--------|-------|------------|-------------|---------------|
| CEO | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Board | - | ✓ | ✓ | ✓ | ✓ | - | - |
| Main PM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cell PM | ✓ | ✓ | ✓ | ✓ | ✓ | - | - |
| Developer | ✓ | ✓ | ✓ | ✓ | ✓ | - | - |
| QA | - | - | ✓ | ✓ | ✓ | - | - |
| Documenter | - | ✓ | ✓ | ✓ | ✓ | - | - |

---

## 5. Communication Flow Diagram

### Message Flow Architecture

```mermaid
sequenceDiagram
    participant A as Agent
    participant API as FastAPI
    participant MS as MessagingService
    participant DB as PostgreSQL
    participant RD as Redis Streams
    participant WS as WebSocket
    participant C as Connected Clients

    A->>API: POST /messages
    API->>MS: send_message(req)
    MS->>DB: Store message
    MS->>MS: Check session boundaries

    alt Session boundary exceeded
        MS->>DB: Close session
        MS->>RD: Publish SESSION_CLOSED
    end

    MS->>RD: Publish MESSAGE_SENT
    MS->>API: Return message

    RD-->>WS: Event notification
    WS-->>C: Broadcast to subscribers
```

### Notification Flow

```mermaid
sequenceDiagram
    participant PM as PM Agent
    participant API as FastAPI
    participant NDS as NotificationDeliveryService
    participant DB as PostgreSQL
    participant RD as Redis Streams
    participant WS as WebSocket
    participant R as Recipient Agents

    PM->>API: POST /notifications
    API->>API: Validate: is_pm_or_board(sender)
    API->>NDS: deliver(notification)
    NDS->>DB: Store notification
    NDS->>RD: Publish NOTIFICATION_SENT
    NDS->>API: Return notification_id

    RD-->>WS: Event received
    WS-->>R: Push to connected recipients

    R->>API: POST /notifications/{id}/ack
    API->>DB: Update acked_by
    API->>RD: Publish NOTIFICATION_ACKED
```

### Channel Hierarchy

```mermaid
graph TD
    subgraph "Communication Structure"
        CH[Channel]
        GR[Group]
        SE[Session]
        MSG[Messages]

        CH --> GR
        GR --> SE
        SE --> MSG
    end

    subgraph "Session Boundaries"
        TIME[Max Time: 30 min]
        COUNT[Max Messages: 100]
        LENGTH[Max Content: 50KB]
    end

    SE --> TIME & COUNT & LENGTH

    style CH fill:#48dbfb
    style GR fill:#5f27cd
    style SE fill:#ff9ff3
    style MSG fill:#1dd1a1
```

### A2A Protocol Flow

```mermaid
sequenceDiagram
    participant EA as External Agent
    participant WK as /.well-known/agent.json
    participant A2A as A2A API
    participant TS as TaskService
    participant IA as Internal Agent

    EA->>WK: GET Agent Card
    WK-->>EA: Capabilities, Skills, Endpoint

    EA->>A2A: POST /message/send
    A2A->>TS: Create task from A2A message
    TS->>IA: Assign to matching agent

    loop Task Processing
        IA->>TS: Update task status
        TS->>A2A: Map to A2A state
        A2A-->>EA: SSE status updates
    end

    IA->>TS: Complete task
    TS->>A2A: COMPLETED state
    A2A-->>EA: Final response with artifacts
```

---

## 6. Services Dependency Graph

### Service Architecture

```mermaid
graph TB
    subgraph "Knowledge Layer"
        OS[OptimalService<br/>RAG Engine]
        VS[ValidatorService]
        MEN[MentorService]
        REV[ReviewerService]
        LP[LearningPropagation]
        PK[ProactiveKnowledge]

        OS --> VS & MEN & REV & LP & PK
    end

    subgraph "Plugin Architecture"
        CODE[Code Index]
        DOCS[Docs Index]
        CONV[Conversations]
        JOUR[Journals]
        ERR[Errors]
        STD[Standards]
        DEC[Decisions]
        REVS[Reviews]
        LEARN[Learnings]

        OS --> CODE & DOCS & CONV & JOUR & ERR & STD & DEC & REVS & LEARN
    end

    subgraph "Communication Layer"
        MS[MessagingService]
        NDS[NotificationDeliveryService]
        NS[NotificationService]
        A2A[A2AService]

        NS --> NDS
    end

    subgraph "Task Layer"
        TS[TaskService]
        KS[KanbanService]

        TS --> KS
    end

    subgraph "Agent Layer"
        JS[JournalService]

        JS --> OS
    end

    subgraph "Support Layer"
        PS[PermissionService]
        AS[AuditService]
        MET[MetricsService]
        DASH[DashboardService]
        HS[HealthService]

        DASH --> MET
    end

    subgraph "Enforcement Layer"
        TL[TaskLifecycle]
        TO[TaskOwnership]
        CA[ChannelAccess]
        JP[JournalPerms]
        NP[NotificationPerms]
    end

    TS --> TL & TO
    MS --> CA
    NS --> NP
    JS --> JP

    subgraph "Infrastructure"
        DB[(PostgreSQL)]
        RD[(Redis)]
        SE[SharedEmbedder]
    end

    TS & MS & NS & JS --> DB
    MS & NDS --> RD
    OS --> SE
```

### Service Initialization Order

```mermaid
graph LR
    subgraph "Startup Sequence"
        DB[Database] --> RD[Redis]
        RD --> PS[PermissionService]
        PS --> AS[AuditService]
        AS --> OS[OptimalService]
        OS --> TS[TranscriptionService]
        TS --> EP[ExtractionPipeline]
        EP --> API[FastAPI Ready]
    end
```

### Service Types

| Service | Type | Stateful | DB Required | Description |
|---------|------|----------|-------------|-------------|
| TaskService | Session-based | No | Yes | Task CRUD and lifecycle |
| MessagingService | Session-based | No | Yes | Channel, Group, Session, Message operations |
| NotificationService | Session-based | No | Yes | Notification creation |
| NotificationDeliveryService | Session-based | No | Yes | Multi-channel delivery |
| JournalService | Session-based | No | Yes | Agent journals |
| OptimalService | Singleton | Yes | Yes | RAG knowledge engine |
| PermissionService | Singleton | No | No | Access control |
| AuditService | Singleton | No | No | Audit logging |
| ValidatorService | Singleton | Yes | No | Standards validation |
| MentorService | Singleton | Yes | No | Conversational RAG |
| ReviewerService | Singleton | No | No | Code review |

---

## 7. Data Model Entity Relationships

### Core Entities

```mermaid
erDiagram
    AGENT ||--o{ TASK : creates
    AGENT ||--o{ TASK : assigned_to
    AGENT ||--o{ MESSAGE : sends
    AGENT ||--o{ NOTIFICATION : sends
    AGENT ||--o{ NOTIFICATION : receives
    AGENT ||--|| JOURNAL : has

    CHANNEL ||--|{ GROUP : contains
    GROUP ||--|{ SESSION : contains
    SESSION ||--|{ MESSAGE : contains

    TASK ||--o{ TASK : parent_child
    TASK ||--o{ TASK : dependencies
    TASK ||--o{ SESSION : discussed_in
    TASK ||--o{ MESSAGE : referenced_in

    JOURNAL ||--|{ JOURNAL_ENTRY : contains

    NOTIFICATION ||--o{ TASK : relates_to
    NOTIFICATION ||--o{ MESSAGE : relates_to

    AGENT {
        uuid id PK
        string name
        string slug UK
        enum role
        enum team
        enum status
        uuid journal_id FK
        uuid current_task_id FK
    }

    TASK {
        uuid id PK
        string title
        text description
        array acceptance_criteria
        enum status
        int priority
        uuid created_by FK
        uuid assigned_to FK
        enum team
        uuid parent_task_id FK
        array dependency_ids
        json plan
        json execution_log
    }

    CHANNEL {
        uuid id PK
        string name
        string slug UK
        enum type
        array members
        array writers
        array silent_observers
        bool is_archived
    }

    GROUP {
        uuid id PK
        string name
        uuid channel_id FK
        array allowed_roles
        int hierarchy_level
        uuid active_session_id FK
    }

    SESSION {
        uuid id PK
        uuid group_id FK
        enum status
        enum scope
        int message_count
        int total_content_length
        timestamp closed_at
    }

    MESSAGE {
        uuid id PK
        uuid agent_id FK
        uuid channel_id FK
        uuid group_id FK
        uuid session_id FK
        enum type
        text content
        uuid reply_to FK
        array mentions
        uuid task_id FK
        vector embedding
    }

    NOTIFICATION {
        uuid id PK
        enum type
        enum priority
        uuid from_agent FK
        array to_agents
        string subject
        text body
        bool requires_ack
        array acked_by
    }

    JOURNAL {
        uuid id PK
        uuid agent_id FK
        int total_entries
        timestamp last_entry_at
    }

    JOURNAL_ENTRY {
        uuid id PK
        uuid journal_id FK
        enum type
        string title
        text content
        uuid task_id FK
        array tags
        vector embedding
    }
```

### Enums Summary

```mermaid
graph LR
    subgraph "Status Enums"
        TS[TaskStatus<br/>13 states]
        SS[SessionStatus<br/>3 states]
        AS[AgentStatus<br/>3 states]
    end

    subgraph "Type Enums"
        MT[MessageType<br/>6 types]
        NT[NotificationType<br/>9 types]
        CT[ChannelType<br/>4 types]
        JT[JournalEntryType<br/>5 types]
    end

    subgraph "Role/Team Enums"
        AR[AgentRole<br/>10 roles]
        TM[Team<br/>6 teams]
    end

    subgraph "Priority Enums"
        NP[NotificationPriority<br/>3 levels]
        CX[Complexity<br/>3 levels]
    end
```

---

## 8. API Route Structure

### Endpoint Map

```mermaid
graph TD
    subgraph "Root"
        H[/health]
        R[/ready]
    end

    subgraph "/api/v1"
        AG[/agents]
        CH[/channels]
        GR[/groups]
        SE[/sessions]
        MS[/messages]
        NO[/notifications]
        ST[/stream]
        OP[/optimal]
        JO[/journals]
        TA[/tasks]
        KA[/kanban]
        DA[/dashboard]
        OR[/orchestrator]
        A2A[/a2a]
    end

    subgraph "Well-Known"
        WK[/.well-known/agent.json]
    end

    subgraph "WebSocket"
        WS[/ws]
    end
```

### Task Endpoints Detail

```mermaid
graph LR
    subgraph "/api/v1/tasks"
        CRUD[CRUD]
        LIFECYCLE[Lifecycle]
        QUERIES[Queries]
        ARTIFACTS[Artifacts]
    end

    subgraph "CRUD Operations"
        POST_T[POST /]
        GET_T[GET /]
        GET_ID[GET /{id}]
        PUT_T[PUT /{id}]
        DEL_T[DELETE /{id}]
    end

    subgraph "Lifecycle Operations"
        CLAIM[POST /{id}/claim]
        START[POST /{id}/start]
        BLOCK[POST /{id}/block]
        UNBLOCK[POST /{id}/unblock]
        PAUSE[POST /{id}/pause]
        RESUME[POST /{id}/resume]
        VERIFY[POST /{id}/verify]
        QA_SUB[POST /{id}/submit-qa]
        QA_PASS[POST /{id}/pass-qa]
        QA_FAIL[POST /{id}/fail-qa]
        DOCS[POST /{id}/docs-complete]
        PM_REV[POST /{id}/submit-pm-review]
        COMPLETE[POST /{id}/complete]
        CANCEL[POST /{id}/cancel]
    end

    subgraph "Query Operations"
        MY[GET /my]
        PENDING[GET /pending]
        BLOCKED_Q[GET /blocked]
        QA_Q[GET /awaiting-qa]
        DOCS_Q[GET /awaiting-docs]
        TEAM[GET /team/{team}]
        STATS[GET /stats]
    end

    CRUD --> POST_T & GET_T & GET_ID & PUT_T & DEL_T
    LIFECYCLE --> CLAIM & START & BLOCK & UNBLOCK & PAUSE & RESUME
    LIFECYCLE --> VERIFY & QA_SUB & QA_PASS & QA_FAIL
    LIFECYCLE --> DOCS & PM_REV & COMPLETE & CANCEL
    QUERIES --> MY & PENDING & BLOCKED_Q & QA_Q & DOCS_Q & TEAM & STATS
```

### API Response Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | OK | GET operations, updates |
| 201 | Created | POST new resource |
| 204 | No Content | DELETE, non-returning POST |
| 400 | Bad Request | Validation error |
| 401 | Unauthorized | Missing/invalid auth |
| 403 | Forbidden | Permission denied |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Invalid state transition |
| 500 | Server Error | Internal error |

---

## 9. Workflow Enforcement Diagram

### Enforcement Architecture

```mermaid
graph TB
    subgraph "Request Flow"
        REQ[API Request]
        AUTH[Authentication<br/>X-Agent-ID Header]
        PERM[Permission Check]
        ENFOR[Enforcement Layer]
        SVC[Service Layer]
        DB[(Database)]
    end

    REQ --> AUTH
    AUTH --> PERM
    PERM --> ENFOR
    ENFOR --> SVC
    SVC --> DB

    subgraph "Enforcement Modules"
        TL[task_lifecycle.py<br/>State Transitions]
        TO[task_ownership.py<br/>Ownership Rules]
        CA[channel_access.py<br/>Channel Permissions]
        JP[journal_perms.py<br/>Journal Access]
        NP[notification_perms.py<br/>Notification Rules]
    end

    ENFOR --> TL & TO & CA & JP & NP

    subgraph "Validation Results"
        ALLOW[✓ Allowed]
        DENY[✗ Denied + Audit Log]
    end

    TL & TO & CA & JP & NP --> ALLOW
    TL & TO & CA & JP & NP --> DENY
```

### Task State Enforcement

```mermaid
graph TD
    subgraph "Validation Chain"
        REQ[Status Change Request]
        VT[validate_task_transition]
        RR[Check Role Restrictions]
        OC[Ownership Check]
        BC[Boundary Conditions]
        OK[✓ Apply Change]
        FAIL[✗ Raise Exception]
    end

    REQ --> VT
    VT -->|Valid Transition?| RR
    VT -->|Invalid| FAIL
    RR -->|Role Allowed?| OC
    RR -->|Not Allowed| FAIL
    OC -->|Owner/PM?| BC
    OC -->|Not Owner| FAIL
    BC -->|Conditions Met?| OK
    BC -->|Failed| FAIL
```

### Self-Review Prevention

```mermaid
sequenceDiagram
    participant DEV as Developer
    participant TS as TaskService
    participant DB as Database

    DEV->>TS: Complete work, submit for QA
    TS->>DB: Store original_developer in quick_context

    Note over DB: quick_context = "original_developer:{dev_uuid}"

    participant QA as QA Agent
    QA->>TS: Claim task for review
    TS->>DB: Fetch task.quick_context

    alt QA == original_developer
        TS-->>QA: ✗ TaskOwnershipError<br/>"Cannot review own work"
    else QA != original_developer
        TS->>DB: Assign to QA
        TS-->>QA: ✓ Task claimed
    end
```

### Claiming Rules Flowchart

```mermaid
flowchart TD
    START([Agent Claims Task]) --> CHECK_STATUS{Task Status<br/>Valid for Role?}

    CHECK_STATUS -->|Yes| CHECK_ACTIVE{Agent has<br/>Active Tasks?}
    CHECK_STATUS -->|No| FAIL1[✗ Invalid status<br/>for role]

    CHECK_ACTIVE -->|No| CHECK_PAUSED{Agent has<br/>Paused Tasks?}
    CHECK_ACTIVE -->|Yes| FAIL2[✗ Must complete<br/>or pause active tasks]

    CHECK_PAUSED -->|No| CHECK_TEAM{Same Team<br/>or Management?}
    CHECK_PAUSED -->|Yes| FAIL3[✗ Must resume<br/>paused tasks first]

    CHECK_TEAM -->|Yes| CHECK_SELF{Is Self-Review?<br/>QA/Doc checking own work}
    CHECK_TEAM -->|No| WARN[⚠ Warning:<br/>Cross-team claim]
    WARN --> CHECK_SELF

    CHECK_SELF -->|No| SUCCESS[✓ Task Claimed]
    CHECK_SELF -->|Yes| FAIL4[✗ Cannot review<br/>own work]

    style SUCCESS fill:#1dd1a1
    style FAIL1 fill:#ff6b6b
    style FAIL2 fill:#ff6b6b
    style FAIL3 fill:#ff6b6b
    style FAIL4 fill:#ff6b6b
    style WARN fill:#feca57
```

---

## Appendix: Quick Reference

### Agent IDs (Static UUIDs)

| Agent | UUID Pattern |
|-------|-------------|
| CEO | 00000000-0000-0000-0000-000000000000 |
| Backend Cell | 00000000-0000-0000-0001-00000000000X |
| Frontend Cell | 00000000-0000-0000-0002-00000000000X |
| UX/UI Cell | 00000000-0000-0000-0003-00000000000X |
| Board/Management | 00000000-0000-0000-0004-00000000000X |

### Common Status Transitions

| From | To | Required By |
|------|-----|-------------|
| PENDING | CLAIMED | Any agent (matching role) |
| CLAIMED | IN_PROGRESS | Assigned agent (requires plan) |
| IN_PROGRESS | VERIFYING | Assigned agent |
| VERIFYING | AWAITING_QA | Assigned agent |
| AWAITING_QA | AWAITING_DOCUMENTATION | QA only |
| AWAITING_DOCUMENTATION | AWAITING_PM_REVIEW | Documenter only |
| AWAITING_PM_REVIEW | COMPLETED | PM only |

### Key Configuration Files

| File | Purpose |
|------|---------|
| `roboco/agents_config.py` | Single source of truth for roles, teams, permissions |
| `roboco/config.py` | Environment-based settings |
| `roboco/enforcement/*.py` | Validation rules |
| `roboco/models/base.py` | Core enums and base models |

---

*This document was generated from deep exploration of the RoboCo codebase and represents the actual implementation as of December 29, 2025.*
