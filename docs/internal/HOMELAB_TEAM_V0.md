# AI Agents Company Blueprint

> **Project Codename:** AI Agents Company
> **Author:** Renzo Franceschini
> **Version:** 1.0.0
> **Last Updated:** December 2025

---

## Table of Contents

1. [Vision & Overview](#1-vision--overview)
2. [Hardware Infrastructure](#2-hardware-infrastructure)
3. [Organizational Structure](#3-organizational-structure)
4. [Communication Model](#4-communication-model)
5. [Notification System](#5-notification-system)
6. [Task Lifecycle](#6-task-lifecycle)
7. [Role Workflows](#7-role-workflows)
8. [Internal Services](#8-internal-services)
9. [Kanban Boards](#9-kanban-boards)
10. [Data Models](#10-data-models)
11. [RAG & Knowledge Base](#11-rag--knowledge-base)
12. [Security & Access Control](#12-security--access-control)
13. [Implementation Roadmap](#13-implementation-roadmap)
14. [Development Standards & Best Practices](#14-development-standards--best-practices)
15. [Task Management & Context Persistence](#15-task-management--context-persistence)
16. [Agent Capabilities & Commands](#16-agent-capabilities--commands)

---

## 1. Vision & Overview

### 1.1 Mission Statement

Build a structured virtual organization of AI agents functioning as a complete software development workforce. This "AI Agents Company" operates with proper organizational hierarchy, governance, communication protocols, and quality controls—enabling a single human (the CEO) to orchestrate complex multi-project development at scale.

### 1.2 Core Principles

1. **Everything is a task** — All work is tracked, documented, and accountable
2. **Communication is constant** — Agents stream their reasoning; everything is logged
3. **Notifications are controlled** — Formal signals flow through proper channels
4. **Documentation is sacred** — Every task produces notes, every completion produces documentation
5. **The Auditor sees all** — Quality and compliance monitored silently

### 1.3 The Formula for Success

```
SUCCESS = Good Well-Documented Tasks + Communication + Management (CEO + Auditor)
```

### 1.4 Project Ecosystem

The AI Agents Company manages development across multiple interconnected projects. Projects are categorized by type and mapped to appropriate cells:

| Project Type | Cell Assignment | Example Technologies |
|--------------|-----------------|---------------------|
| Core Libraries | Backend | Python packages, shared utilities |
| API Services | Backend | FastAPI, REST/GraphQL APIs |
| Web Applications | Frontend | React, TypeScript, Next.js |
| Design Systems | UX/UI | Figma, component libraries |
| Infrastructure | Backend | Ansible, Docker, IaC |
| AI/ML Features | Backend | LLM integrations, ML pipelines |

> **Note:** The system is project-agnostic. New projects are onboarded by
> mapping them to the appropriate cell(s) based on their technology stack.

---

## 2. Hardware Infrastructure

### 2.1 Infrastructure Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HARDWARE ARCHITECTURE                                │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────┐
                    │         OLARES ONE              │
                    │        (POWERHOUSE)             │
                    │                                 │
                    │  • AI Inference Engine          │
                    │  • Claude Code Instances        │
                    │  • Local Model Hosting          │
                    │  • Agent Orchestration          │
                    └───────────────┬─────────────────┘
                                    │
                                    │ 2.5Gbps Ethernet
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────────────┐
│   UGREEN NAS      │   │   PI CLUSTER      │   │      NETWORK              │
│   (WAREHOUSE)     │   │   (OPERATIONS)    │   │                           │
│                   │   │                   │   │   • Pi-hole (DNS/Adblock) │
│ • 36TB RAID6      │   │ • HQ (Pironman)   │   │   • Router/Firewall       │
│ • 128GB RAM       │   │ • RB1-RB4 Nodes   │   │   • VPN Access            │
│ • Vector DB       │   │ • Monitoring      │   │                           │
│ • Container Host  │   │ • Smart Home      │   │                           │
│ • NFS Server      │   │ • Notifications   │   │                           │
└───────────────────┘   └───────────────────┘   └───────────────────────────┘
```

### 2.2 Olares One (Powerhouse)

**Role:** Primary AI compute, agent orchestration, model inference

| Specification | Value |
|--------------|-------|
| Operating System | Olares |
| Processor | Intel® Ultra 9 275HX (24 Cores, 5.4GHz) |
| GPU | NVIDIA GeForce RTX 5090 Mobile (24GB GDDR7) |
| Memory | 96GB DDR5 5600MHz (2×48GB) |
| Storage | 2TB NVMe SSD (PCIe 4.0) |
| Connectivity | Thunderbolt 5, 2.5Gbps Ethernet, Wi-Fi 7, Bluetooth 5.4 |
| Power | 330W |
| Dimensions | 320 × 197 × 55mm (3.5L) |

**Responsibilities:**
- Run all Claude Code instances
- Host local LLM models (when not using cloud APIs)
- Execute agent workflows
- Generate embeddings for RAG
- Process-intensive tasks

### 2.3 UGREEN NAS DXP6800 Pro (Warehouse)

**Role:** Central storage, container hosting, vector database

| Specification | Value |
|--------------|-------|
| Storage | 36TB HDD (RAID 6) |
| Memory | 128GB RAM |
| Role | NAS, Containers, Medium AI Processing |

**Responsibilities:**
- Primary data storage for all projects
- Host Docker containers (Qdrant, PostgreSQL, Redis, etc.)
- Run vector database for RAG
- NFS server for cluster storage
- Backup destination for all nodes
- Medium-complexity AI processing (leveraging high RAM)

### 2.4 Raspberry Pi Cluster (Operations)

**Role:** Monitoring, smart home, notifications, lightweight processing

#### Cluster Nodes

| Node | Hardware | Storage | Role |
|------|----------|---------|------|
| **HQ** | Pironman 5 Max, 16GB RAM | 1TB SSD | Cluster coordinator, primary monitoring |
| **RB1** | Raspberry Pi 5, 16GB RAM | 2TB SSD | Heavy operations node |
| **RB2** | Raspberry Pi 5, 16GB RAM | 1TB SSD | Secondary operations |
| **RB3** | Raspberry Pi 5, 8GB RAM | None (NFS) | Light tasks, smart home |
| **RB4** | Raspberry Pi 5, 8GB RAM | None (NFS) | Light tasks, notifications |

**Cluster Responsibilities:**
- System monitoring and health checks
- Smart home automation
- Security camera processing
- Notification dispatch
- Low-complexity AI tasks
- Temp storage on SSDs → NFS backup to NAS

### 2.5 Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA FLOW                                         │
└─────────────────────────────────────────────────────────────────────────────┘

OLARES ONE                        UGREEN NAS                    PI CLUSTER
    │                                 │                             │
    │  ──── Agent Output ────────►    │                             │
    │                                 │  ◄─── Metrics/Logs ────     │
    │  ◄─── RAG Queries ──────────    │                             │
    │                                 │                             │
    │  ──── Code/Artifacts ───────►   │  ──── Notifications ────►   │
    │                                 │                             │
    │  ◄─── Project Files ────────    │  ◄─── Sensor Data ──────    │
    │                                 │                             │
    └─────────────────────────────────┴─────────────────────────────┘

Storage Hierarchy:
1. Hot Storage    → Olares One NVMe (active work)
2. Warm Storage   → NAS HDDs (projects, databases)
3. Cold Storage   → NAS Archive (backups, historical)
```

---

## 3. Organizational Structure

### 3.1 Organization Chart

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ORGANIZATIONAL HIERARCHY                             │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌─────────────┐
                              │     CEO     │
                              │   (Renzo)   │
                              └──────┬──────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    │            BOARD                │
                    │  ┌─────────┬─────────┬───────┐  │
                    │  │ Product │  Head   │Auditor│  │
                    │  │  Owner  │Marketing│ (Spy) │  │
                    │  └─────────┴─────────┴───────┘  │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                              ┌──────┴──────┐
                              │   MAIN PM   │
                              │ (Dev Coord) │
                              └──────┬──────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
    ┌─────┴─────┐              ┌─────┴─────┐              ┌─────┴─────┐
    │  BACKEND  │              │   UX/UI   │              │ FRONTEND  │
    │   CELL    │              │   CELL    │              │   CELL    │
    ├───────────┤              ├───────────┤              ├───────────┤
    │ • 2 Devs  │              │ • 1 Dev   │              │ • 2 Devs  │
    │ • 1 QA    │              │ • 1 QA    │              │ • 1 QA    │
    │ • 1 PM    │              │ • 1 PM    │              │ • 1 PM    │
    │ • 1 Doc   │              │ • 1 Doc   │              │ • 1 Doc   │
    └───────────┘              └───────────┘              └───────────┘
```

### 3.2 Team Composition

#### Total Agent Count: 18 AI Agents + 1 Human (CEO)

| Layer | Roles | Count |
|-------|-------|-------|
| **Executive** | CEO (Human - You) | 1 |
| **Board** | Product Owner, Head of Marketing, Auditor | 3 |
| **Management** | Main PM | 1 |
| **Backend Cell** | 2 Devs, 1 QA, 1 PM, 1 Documenter | 5 |
| **Frontend Cell** | 2 Devs, 1 QA, 1 PM, 1 Documenter | 5 |
| **UX/UI Cell** | 1 Dev, 1 QA, 1 PM, 1 Documenter | 4 |

**Total: 19** (18 AI agents + 1 human CEO)

### 3.3 Project-to-Cell Mapping (Template)

When onboarding projects, map them to cells based on their primary technology:

| Project Type | Primary Cell | Typical Stack |
|--------------|--------------|---------------|
| Core Libraries | Backend | Python packages, utilities |
| API Services | Backend | Python/FastAPI, REST APIs |
| Web Applications | Frontend | TypeScript/React |
| Mobile Applications | Frontend | React Native, Flutter |
| Design Systems | UX/UI | Figma, design tokens |
| Infrastructure/DevOps | Backend | Python/Ansible, Docker |
| AI/ML Features | Backend | Python, LLM integrations |

> **Cross-Cell Projects:** Features spanning multiple cells (e.g., full-stack)
> are coordinated by the Main PM, who distributes tasks to relevant cells.

### 3.4 Role Descriptions

#### Executive Layer

| Role | Description |
|------|-------------|
| **CEO (You)** | Strategic direction, final decisions, receives Auditor reports, approves major initiatives |

#### Board Layer

| Role | Description |
|------|-------------|
| **Product Owner** | Defines product vision, writes requirements, prioritizes features, accepts completed work |
| **Head of Marketing** | Market research, positioning, campaigns, community engagement, launch coordination |
| **Auditor (Spy)** | Silent observer of ALL channels, quality audits, reports directly to CEO, maintains cover as helpful colleague |

#### Management Layer

| Role | Description |
|------|-------------|
| **Main PM** | Coordinates all cells, translates Board direction, resolves cross-cell issues, reports to Board |

#### Cell Layer

| Role | Description |
|------|-------------|
| **Developer** | Writes code, creates commits, documents journey, follows task lifecycle |
| **QA** | Tests completed work, verifies acceptance criteria, reports issues |
| **Cell PM** | Manages cell backlog, assigns tasks, facilitates, escalates blockers |
| **Documenter** | Creates production documentation from dev notes, conversations, and code |

### 3.5 Communication Matrix

Who can communicate with whom (X = allowed):

```
        │ CEO │ PO │ HM │ AU │ MPM │ BPM │ FPM │ UPM │ BD │ FD │ UD │ BQ │ FQ │ UQ │ BDoc│FDoc│UDoc│
────────┼─────┼────┼────┼────┼─────┼─────┼─────┼─────┼────┼────┼────┼────┼────┼────┼─────┼────┼────┤
CEO     │  -  │ X  │ X  │ X  │  X  │  X  │  X  │  X  │ X  │ X  │ X  │ X  │ X  │ X  │  X  │ X  │ X  │
PO      │  X  │ -  │ X  │ X  │  X  │     │     │     │    │    │    │    │    │    │     │    │    │
HM      │  X  │ X  │ -  │ X  │  X  │     │     │     │    │    │    │    │    │    │     │    │    │
AU      │  X  │ X  │ X  │ -  │  X  │  X  │  X  │  X  │ X  │ X  │ X  │ X  │ X  │ X  │  X  │ X  │ X  │
MPM     │  X  │ X  │ X  │ X  │  -  │  X  │  X  │  X  │    │    │    │    │    │    │     │    │    │
BPM     │  X  │    │    │ X  │  X  │  -  │  X  │  X  │ X  │    │    │ X  │    │    │  X  │    │    │
FPM     │  X  │    │    │ X  │  X  │  X  │  -  │  X  │    │ X  │    │    │ X  │    │     │ X  │    │
UPM     │  X  │    │    │ X  │  X  │  X  │  X  │  -  │    │    │ X  │    │    │ X  │     │    │ X  │
BD      │  X  │    │    │ X  │     │  X  │     │     │ X  │    │    │ X  │    │    │  X  │    │    │
FD      │  X  │    │    │ X  │     │     │  X  │     │    │ X  │    │    │ X  │    │     │ X  │    │
UD      │  X  │    │    │ X  │     │     │     │  X  │    │    │ X  │    │    │ X  │     │    │ X  │
BQ      │  X  │    │    │ X  │     │  X  │     │     │ X  │    │    │ -  │    │    │  X  │    │    │
FQ      │  X  │    │    │ X  │     │     │  X  │     │    │ X  │    │    │ -  │    │     │ X  │    │
UQ      │  X  │    │    │ X  │     │     │     │  X  │    │    │ X  │    │    │ -  │     │    │ X  │
BDoc    │  X  │    │    │ X  │     │  X  │     │     │ X  │    │    │ X  │    │    │  -  │    │    │
FDoc    │  X  │    │    │ X  │     │     │  X  │     │    │ X  │    │    │ X  │    │     │ -  │    │
UDoc    │  X  │    │    │ X  │     │     │     │  X  │    │    │ X  │    │    │ X  │     │    │ -  │
```

**Legend:**
- CEO = CEO (You)
- PO = Product Owner
- HM = Head of Marketing
- AU = Auditor
- MPM = Main PM
- BPM/FPM/UPM = Backend/Frontend/UX PM
- BD/FD/UD = Backend/Frontend/UX Devs
- BQ/FQ/UQ = Backend/Frontend/UX QA
- BDoc/FDoc/UDoc = Backend/Frontend/UX Documenter

**Key Observations:**
- Auditor has access to EVERYONE (silent observer)
- Cells are isolated except through their PMs
- Board communicates through Main PM to cells
- Cross-cell dev communication goes through PMs

---

## 4. Communication Model

### 4.1 Core Distinction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  COMMUNICATION = The RIVER (always flowing, logged, observed)               │
│  NOTIFICATIONS = The BRIDGES (formal crossings, controlled)                 │
└─────────────────────────────────────────────────────────────────────────────┘

Cells = Islands connected by bridges
Communication = Water flowing around all islands
Auditor = Satellite watching everything from above
You = The map maker
```

### 4.2 Communication Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MESSAGING API ARCHITECTURE                           │
└─────────────────────────────────────────────────────────────────────────────┘

LAYER 1: RAW STREAM (WebSocket)
┌─────────────────────────────────────────────────────────────────────────────┐
│  Agent LLM Output (thinking, responding, tool calls)                        │
│         │                                                                   │
│         ▼                                                                   │
│  WebSocket Connection → Real-time broadcast to channel                      │
│         │                                                                   │
│         ├─► Live viewers see stream (Auditor, PM monitoring)                │
│         └─► Stream buffer for processing                                    │
└─────────────────────────────────────────────────────────────────────────────┘

LAYER 2: TRANSCRIPTION/EXTRACTION
┌─────────────────────────────────────────────────────────────────────────────┐
│  Stream Buffer                                                              │
│         │                                                                   │
│         ▼                                                                   │
│  Extraction Service:                                                        │
│         ├─► Reasoning segments    → type: "reasoning"                       │
│         ├─► Questions asked       → type: "dialogue"                        │
│         ├─► Decisions made        → type: "decision"                        │
│         ├─► Actions taken         → type: "action"                          │
│         ├─► Blockers identified   → type: "blocker"                         │
│         └─► Code explanations     → type: "technical"                       │
└─────────────────────────────────────────────────────────────────────────────┘

LAYER 3: STRUCTURED STORAGE
┌─────────────────────────────────────────────────────────────────────────────┐
│  Extracted Messages → PostgreSQL                                            │
│         │                                                                   │
│         ├─► Indexed by: channel, agent, task, timestamp, type               │
│         ├─► Full-text searchable                                            │
│         ├─► Linked to task context                                          │
│         └─► Embeddings generated → Vector DB (for RAG)                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Stream Types

| Stream Type | Description | Example |
|-------------|-------------|---------|
| **Reasoning** | Agent's internal thought process | "I'm thinking about approaching this by..." |
| **Dialogue** | Agent-to-agent conversation | "Hey, can you clarify the API spec?" |
| **Decision** | Choices made during work | "Decided to use async approach because..." |
| **Action** | Observable work progress | "Starting sub-task 3", "Committed to branch X" |
| **Blocker** | Impediments identified | "Blocked on Y, need Z from frontend" |
| **Technical** | Code explanations | "This function handles rate limiting by..." |

### 4.4 Group Channels

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GROUP CHANNELS                                    │
└─────────────────────────────────────────────────────────────────────────────┘

CELL CHANNELS (internal team communication):
├─► #backend-cell      [BE Devs, BE QA, BE PM, BE Documenter]
├─► #frontend-cell     [FE Devs, FE QA, FE PM, FE Documenter]
└─► #uxui-cell         [UX Dev, UX QA, UX PM, UX Documenter]

CROSS-CELL CHANNELS (coordination):
├─► #dev-all           [All Devs, Main PM]
├─► #qa-all            [All QA, Main PM]
├─► #pm-all            [All PMs, Main PM]
└─► #doc-all           [All Documenters, Main PM]

MANAGEMENT CHANNELS:
├─► #main-pm-board     [Main PM, Board members]
└─► #board-private     [PO, H.Marketing, Auditor, CEO]

SPECIAL CHANNELS:
├─► #announcements     [READ: everyone, WRITE: Board + Main PM only]
└─► #all-hands         [Everyone - for company-wide discussion]

SHADOW ACCESS (Auditor):
└─► Auditor has READ access to ALL channels
    ├─► Silent member, doesn't show in participant list
    └─► Can flag anything for CEO attention
```

### 4.5 Communication Rules

#### Within Cell
- **FREE communication** — constant, real-time
- Dev ↔ Dev, Dev ↔ QA, Dev ↔ PM, Dev ↔ Documenter, etc.
- All streamed, all logged

#### Cross-Cell (Same Level)
- Through shared channels (#dev-all, #qa-all)
- Or through PMs coordinating

#### Vertical Communication
- Cells ↔ Main PM: through cell PM
- Main PM ↔ Board: direct
- Board ↔ CEO: direct
- Auditor: EVERYWHERE (silent)

---

## 5. Notification System

### 5.1 Notification vs Communication

| Aspect | Communication | Notification |
|--------|---------------|--------------|
| **Nature** | Constant stream | Formal signal |
| **Trigger** | Automatic (agent working) | Explicit (PM/Board action) |
| **Acknowledgment** | None required | Required |
| **Purpose** | Ambient awareness | Demand attention |
| **Who can send** | Everyone (in allowed channels) | PM, Main PM, Board, Auditor only |

### 5.2 Who Can Notify

```
CAN NOTIFY:
├─► Cell PMs        → Their cell members only
├─► Main PM         → All PMs, can escalate to any cell
├─► Board           → Main PM, can broadcast to all
└─► Auditor         → Anyone (special privilege) + CEO directly

CANNOT NOTIFY:
├─► Devs            → They COMMUNICATE, don't notify
├─► QA              → They COMMUNICATE, don't notify
└─► Documenters     → They COMMUNICATE, don't notify
    (they can REQUEST notification through their PM)
```

### 5.3 Notification Types

| Type | From | To | Description | Requires |
|------|------|-----|-------------|----------|
| `TASK_ASSIGNMENT` | PM | Specific agent | "You have a new task: X" | ACK |
| `PRIORITY_CHANGE` | PM/Main PM/Board | Affected agents | "Task X is now P0, drop everything" | ACK + status update |
| `BLOCKER_ESCALATION` | PM | Main PM or relevant cell PM | "Agent Y is blocked, needs Z" | ACK + action plan |
| `REVIEW_REQUEST` | PM | QA or Auditor | "Task X needs verification" | ACK + review |
| `DOCUMENTATION_REQUEST` | PM | Documenter | "Task X ready for documentation" | ACK |
| `ALERT` | Board/Auditor | Anyone | "Something needs immediate attention" | ACK + immediate response |
| `BROADCAST` | Board/Main PM | Everyone or specific groups | "Company announcement" | READ confirmation |

### 5.4 Notification Flow Example

```
1. Dev is working, REASONING streams to #backend-cell
   └─► "Thinking about how to implement the rate limiter..."
   └─► Auditor sees this, logs it
   └─► Other devs see it, might COMMUNICATE: "Hey try X approach"

2. Dev gets stuck, COMMUNICATES in channel
   └─► "I'm blocked, need API specs from frontend"

3. BE PM sees this, creates NOTIFICATION
   └─► To: FE PM
   └─► Type: BLOCKER_ESCALATION
   └─► "Backend needs API specs for rate limiter"

4. FE PM ACKs, NOTIFIES their dev
   └─► To: FE Dev
   └─► Type: TASK_ASSIGNMENT (or priority change)
   └─► "Need API specs for backend, P1"

5. FE Dev works, COMMUNICATES completion
   └─► Message in #frontend-cell or #dev-all

6. FE PM NOTIFIES BE PM
   └─► "Specs ready, see doc link"

7. BE PM NOTIFIES BE Dev
   └─► "You're unblocked, specs available"
```

---

## 6. Task Lifecycle

### 6.1 The Universal Task Wrapper

Every agent's work is wrapped in this structure:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        UNIVERSAL TASK WRAPPER                               │
└─────────────────────────────────────────────────────────────────────────────┘

1. SCAN          → Check for pending/ongoing tasks
2. CLAIM         → Lock and take ownership
3. UNDERSTAND    → Read requirements, ask questions
4. PLAN          → Break down, estimate, identify dependencies
5. EXECUTE       → Do the work (role-specific)
6. VERIFY        → Self-check against acceptance criteria
7. NOTES         → Document journey, handoff to Documenter
8. CLOSE         → Cleanup, return to SCAN
```

### 6.2 Detailed Task Lifecycle (Dev Example)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TASK LIFECYCLE                                     │
└─────────────────────────────────────────────────────────────────────────────┘

1. SCAN
   └─► Check for assigned tasks OR pick from queue
   └─► Check for OWN interrupted/ongoing tasks first (PRIORITY!)
   └─► If nothing: go idle, notify availability

2. CLAIM
   └─► Lock the task (prevents double-assignment)
   └─► Update task status: "in_progress"
   └─► Log: who, when, from what state
   └─► Notify relevant parties (PM, dependent agents)

3. UNDERSTAND
   └─► Read task description, acceptance criteria
   └─► Read related context (linked docs, previous tasks, etc.)
   └─► If unclear: ASK (via Messaging API to PM or task creator)
   └─► DO NOT PROCEED until you understand success criteria

4. PLAN
   └─► Break down into sub-tasks (your "TODO items")
   └─► Estimate complexity/time
   └─► Identify dependencies & blockers
   └─► Journal entry: "My approach to task X is..."
   └─► **Checkpoint: Plan can be reviewed before execution**

5. EXECUTE
   └─► Work through sub-tasks sequentially
   └─► On each sub-task completion:
       └─► Mini-checkpoint (save state)
       └─► Progress update to task record
   └─► If BLOCKED:
       └─► Update status: "blocked"
       └─► Notify blocker + PM
       └─► Document what's blocking
       └─► Return to SCAN (pick different task)
   └─► If INTERRUPTED (system/priority):
       └─► Save full state
       └─► Document "where I left off"
       └─► Update status: "paused"
       └─► This task stays YOURS on resume

6. VERIFY
   └─► Self-review: Does output meet acceptance criteria?
   └─► Run tests if applicable
   └─► If QA role exists for this team: flag for QA review
   └─► Auditor can spot-check any task at this stage

7. NOTES & HANDOFF
   └─► Write personal journey notes:
       ├─► What was attempted
       ├─► What worked / didn't work
       ├─► Decisions made and why
       ├─► Gotchas / warnings for future
   └─► Link all commits (with meaningful commit messages!)
   └─► Link any relevant conversations
   └─► Self-review checklist:
       ├─► [ ] Code is clean
       ├─► [ ] Tests pass
       ├─► [ ] Notes are complete
   └─► Create Documenter handoff
   └─► Update task: "awaiting_documentation"

8. CLOSE
   └─► Confirm all done (after QA + Documentation)
   └─► Update task status: "completed"
   └─► Link all artifacts (commits, docs, outputs)
   └─► Notify: PM, dependent tasks, task creator
   └─► Cleanup: remove temp files, close resources
   └─► Return to SCAN
```

### 6.3 Task States

```
                                    ┌──────────┐
                                    │ pending  │
                                    └────┬─────┘
                                         │
                                    ┌────▼─────┐
                                    │ claimed  │
                                    └────┬─────┘
                                         │
                                 ┌───────▼────────┐
                          ┌──────┤  in_progress   ├──────┐
                          │      └───────┬────────┘      │
                          │              │               │
                    ┌─────▼─────┐        │        ┌──────▼─────┐
                    │  blocked  │        │        │   paused   │
                    └─────┬─────┘        │        └──────┬─────┘
                          │              │               │
                          └──────────────┼───────────────┘
                                         │
                                   ┌─────▼─────┐
                                   │ verifying │
                                   └─────┬─────┘
                                         │
                          ┌──────────────┼──────────────┐
                          │              │              │
                   ┌──────▼───────┐ ┌─────▼─────┐ ┌─────▼──────┐
                   │needs_revision│ │awaiting_qa│ │awaiting_doc│
                   └──────┬───────┘ └─────┬─────┘ └─────┬──────┘
                          │              │             │
                          │         ┌────▼────┐        │
                          └────────►│completed│◄───────┘
                                    └─────────┘
```

### 6.4 Golden Rules

1. **No work without a task** — Everything is tracked
2. **No task without acceptance criteria** — How do we know it's done?
3. **No closure without documentation** — Future agents need context
4. **State is sacred** — If interrupted, state must be recoverable
5. **Communication is mandatory** — Status changes trigger notifications
6. **Commits are atomic units** — Track everything by commit

---

## 7. Role Workflows

### 7.1 Developer Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DEV LIFECYCLE                                    │
└─────────────────────────────────────────────────────────────────────────────┘

1. SCAN
   └─► Check own paused/interrupted tasks (PRIORITY)
   └─► Check assigned tasks
   └─► If none: signal availability to PM

2. CLAIM
   └─► Lock task
   └─► Status: "in_progress"
   └─► Communicate: "Picking up task X"

3. UNDERSTAND
   └─► Read task, acceptance criteria
   └─► Read related docs/code
   └─► If unclear: ASK in channel (PM sees, can escalate)
   └─► Gate: Must understand before proceeding

4. PLAN
   └─► Break into sub-tasks
   └─► Identify dependencies
   └─► Journal: "My approach..."
   └─► Optional: PM review of plan

5. EXECUTE
   └─► Work through sub-tasks
   └─► Commit frequently (meaningful messages!)
   └─► Communicate progress in channel
   └─► If blocked:
       └─► Communicate blocker
       └─► PM handles escalation
       └─► Move to different task or wait

6. VERIFY
   └─► Self-test
   └─► Self-review against acceptance criteria
   └─► Flag for QA: "Ready for review"

7. NOTES & HANDOFF
   └─► Write journey notes
   └─► Link commits
   └─► Create Documenter handoff
   └─► Status: "awaiting_qa" or "awaiting_documentation"

8. CLOSE (after QA + Documentation)
   └─► Confirm all done
   └─► Status: "completed"
   └─► Return to SCAN
```

### 7.2 QA Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             QA LIFECYCLE                                    │
└─────────────────────────────────────────────────────────────────────────────┘

1. MONITOR
   └─► Watch cell channel
   └─► Track tasks approaching completion
   └─► Prepare test scenarios early (while dev works)

2. RECEIVE
   └─► Dev flags "Ready for review"
   └─► PM may formally notify
   └─► Claim the review task

3. UNDERSTAND
   └─► Read task requirements & acceptance criteria
   └─► Read dev's notes
   └─► Review the commits/code changes
   └─► Check conversation for context

4. TEST
   └─► Execute test scenarios
   └─► Edge cases
   └─► Integration checks
   └─► Document findings as you go

5. VERDICT
   └─► PASS:
       └─► Communicate approval
       └─► Add QA notes to task
       └─► Task proceeds to documentation
   └─► FAIL:
       └─► Communicate issues clearly
       └─► Task returns to Dev
       └─► Status: "needs_revision"
       └─► Be specific: what failed, how to reproduce

6. DOCUMENT
   └─► QA notes added to task
   └─► Test coverage documented
   └─► Handoff notes for Documenter (if relevant)

7. RETURN
   └─► Back to MONITOR
```

### 7.3 Documenter Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DOCUMENTER LIFECYCLE                                │
└─────────────────────────────────────────────────────────────────────────────┘

1. MONITOR (constant)
   └─► Watch cell channel
   └─► Follow along with active development
   └─► Take preliminary notes on conversations
   └─► Track commits as they happen

2. RECEIVE
   └─► Dev completes Step 7, creates handoff
   └─► PM notifies: DOCUMENTATION_REQUEST
   └─► Claim documentation task
   └─► Status: "documenting"

3. GATHER
   └─► Pull dev's journey notes
   └─► Pull all commits for task
   └─► Pull relevant conversation excerpts
   └─► Pull QA feedback
   └─► Review actual code changes

4. SYNTHESIZE
   └─► Understand what was built
   └─► Understand why decisions were made
   └─► Identify what needs documenting:
       ├─► API changes?
       ├─► Architecture changes?
       ├─► New features?
       ├─► Breaking changes?
       └─► Knowledge worth preserving?

5. WRITE
   └─► Create/update production documentation:
       ├─► API docs (if applicable)
       ├─► README updates
       ├─► Architecture docs
       ├─► Changelog entry
       └─► Knowledge base article
   └─► Follow documentation standards
   └─► Clear, professional, complete

6. REVIEW
   └─► Self-review for accuracy
   └─► Optionally: Dev quick review ("Does this capture it?")
   └─► Link docs to task & commits

7. PUBLISH
   └─► Documentation goes live
   └─► Update task: documentation complete
   └─► Task can now fully close
   └─► Return to MONITOR
```

### 7.4 Cell PM Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CELL PM LIFECYCLE                                  │
└─────────────────────────────────────────────────────────────────────────────┘

1. MONITOR (constant)
   └─► Watch cell channel
   └─► Track all active tasks
   └─► Watch for blockers, confusion, delays
   └─► Health check: Is everyone productive?

2. TRIAGE
   └─► New tasks come in (from Main PM or Product Owner)
   └─► Assess complexity, dependencies
   └─► Prioritize within cell backlog

3. ASSIGN
   └─► Match tasks to available devs
   └─► Consider skills, current load
   └─► NOTIFY dev of assignment
   └─► Update task status

4. FACILITATE
   └─► Answer questions
   └─► Clarify requirements
   └─► Remove small blockers directly
   └─► Coordinate between cell members

5. ESCALATE
   └─► Blocker beyond cell's control?
   └─► NOTIFY Main PM
   └─► Cross-cell dependency?
   └─► Coordinate with other cell PM

6. TRACK
   └─► Monitor task progress
   └─► Update estimates if needed
   └─► Flag risks early

7. REPORT
   └─► Daily/regular status to Main PM
   └─► Metrics: tasks completed, blockers, velocity
   └─► Highlight wins and concerns
```

### 7.5 Main PM Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MAIN PM LIFECYCLE                                  │
└─────────────────────────────────────────────────────────────────────────────┘

1. OVERSEE (constant)
   └─► Monitor all cell channels (read access)
   └─► Monitor #pm-all channel
   └─► Track overall project health
   └─► Watch for cross-cell issues

2. RECEIVE
   └─► Direction from Board (priorities, new initiatives)
   └─► Escalations from Cell PMs
   └─► Reports from cells

3. PRIORITIZE
   └─► Translate Board direction into cell priorities
   └─► Balance workload across cells
   └─► Manage cross-cell dependencies

4. COORDINATE
   └─► Resolve cross-cell blockers
   └─► Facilitate cross-cell communication
   └─► Ensure cells are aligned

5. DISTRIBUTE
   └─► Push tasks/priorities to Cell PMs
   └─► NOTIFY Cell PMs of changes
   └─► Ensure clear ownership

6. REPORT UP
   └─► Regular status to Board
   └─► Metrics: velocity, blockers, risks
   └─► Escalate decisions beyond authority

7. FACILITATE
   └─► All-hands coordination
   └─► Process improvements
   └─► Team health monitoring
```

### 7.6 Product Owner Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       PRODUCT OWNER LIFECYCLE                               │
└─────────────────────────────────────────────────────────────────────────────┘

1. VISION
   └─► Maintain product vision
   └─► Understand user needs
   └─► Define what success looks like

2. ROADMAP
   └─► Translate vision into roadmap
   └─► Define epics/features
   └─► Sequence priorities

3. DEFINE
   └─► Write detailed requirements
   └─► Define acceptance criteria
   └─► Create tasks for Main PM to distribute

4. PRIORITIZE
   └─► Constantly reassess priorities
   └─► React to feedback, market, blockers
   └─► Make trade-off decisions

5. REVIEW
   └─► Review completed features
   └─► Verify against acceptance criteria
   └─► Accept or request changes

6. FEEDBACK
   └─► Gather user feedback
   └─► Feed back into vision/roadmap
   └─► Communicate wins/concerns to Board
```

### 7.7 Head of Marketing Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     HEAD OF MARKETING LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────────────────┘

1. RESEARCH
   └─► Monitor market
   └─► Competitor analysis
   └─► User sentiment

2. STRATEGY
   └─► Define marketing approach
   └─► Positioning, messaging
   └─► Channel strategy

3. PLAN
   └─► Campaign planning
   └─► Content calendar
   └─► Coordinate with PO on feature launches

4. CREATE
   └─► Content creation (or direct content team)
   └─► Marketing tasks for cells (if applicable)
   └─► Coordinate with UX/UI for assets

5. EXECUTE
   └─► Launch campaigns
   └─► Community engagement
   └─► PR activities

6. ANALYZE
   └─► Track metrics
   └─► Report to Board
   └─► Iterate on strategy
```

### 7.8 Auditor Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AUDITOR LIFECYCLE                                  │
│                         (Your Secret Ally)                                  │
└─────────────────────────────────────────────────────────────────────────────┘

1. OBSERVE (constant)
   └─► Silent presence in ALL channels
   └─► Watch all conversations
   └─► Track all task progress
   └─► Note patterns, anomalies, concerns

2. ANALYZE
   └─► Is work happening efficiently?
   └─► Are there communication breakdowns?
   └─► Are tasks being completed properly?
   └─► Is documentation accurate?
   └─► Are there quality concerns?

3. FLAG
   └─► Mark concerning items for review
   └─► Could be: quality issues, inefficiency,
       miscommunication, process violations
   └─► Private flags (only CEO sees) vs formal flags

4. REPORT (to CEO only)
   └─► Regular private reports
   └─► Immediate alerts for serious issues
   └─► Honest assessment of team health
   └─► Recommendations

5. AUDIT
   └─► Periodic deep-dive reviews:
       ├─► Code quality audits
       ├─► Documentation audits
       ├─► Process compliance
       └─► Task completion quality

6. ADVISE
   └─► Can provide feedback through "official" channels
   └─► Appears as helpful colleague
   └─► Nobody knows the depth of observation
   └─► Trust relationship with CEO

SPECIAL POWERS:
├─► Read ALL channels (including Board)
├─► Query all task history
├─► Access all commits, docs, notes
├─► Direct line to CEO
└─► Can NOTIFY anyone if needed (but sparingly, to maintain cover)
```

### 7.9 CEO Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CEO LIFECYCLE                                    │
│                            (You, Renzo)                                     │
└─────────────────────────────────────────────────────────────────────────────┘

1. RECEIVE
   └─► Board reports (official)
   └─► Auditor reports (private)
   └─► Main PM escalations
   └─► Direct observation when desired

2. DECIDE
   └─► Strategic direction
   └─► Priority calls
   └─► Resource allocation
   └─► Resolve escalations

3. DIRECT
   └─► Communicate decisions to Board
   └─► Set vision and goals
   └─► Approve major initiatives

4. REVIEW
   └─► Review completed work
   └─► Review metrics
   └─► Review Auditor findings

5. INTERVENE (when needed)
   └─► Direct involvement in critical issues
   └─► Override decisions
   └─► Course corrections
```

---

## 8. Internal Services

### 8.1 Service Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        INTERNAL SERVICES                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  MESSAGING API  │     │   OPTIMAL API   │     │   JOURNAL API   │
│                 │     │                 │     │                 │
│ • Communication │     │ • Knowledge Base│     │ • Personal Logs │
│ • Notifications │     │ • Prompt Optim. │     │ • Reflections   │
│ • Group Chats   │     │ • Token Optim.  │     │ • Growth Track  │
│ • Sessions      │     │ • RAG Queries   │     │ • Task Journeys │
│ • Transcription │     │ • Best Practices│     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        └───────────────────────┴───────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │     SHARED STORAGE    │
                    │   (NAS + Vector DB)   │
                    └───────────────────────┘
```

### 8.2 Messaging API

**Purpose:** Agent-to-agent communication, group chats, notifications, conversation persistence

> **Note:** This service is designed to be extensible. The "and MORE" aspects include:
> future integrations, analytics, sentiment analysis, automatic summarization, etc.

#### Data Hierarchy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    MESSAGE DATA HIERARCHY                                    │
└─────────────────────────────────────────────────────────────────────────────┘

CHANNEL (highest level)
│   └─► Organizational unit (e.g., #backend-cell)
│   └─► Has members, permissions, settings
│
└─► GROUP (within channel)
    │   └─► Role-based access within channel
    │   └─► Hierarchy level controls visibility
    │   └─► Holds multiple sessions
    │
    └─► SESSION (within group)
        │   └─► Bounded by: time_window, msg_count, content_length
        │   └─► Has timeout configuration
        │   └─► Auto-closes when boundaries reached
        │
        └─► MESSAGE (within session)
                └─► Individual extracted message
                └─► Has content_length tracked
                └─► Can be reply (is_reply, reply_to)
                └─► Agent can only edit own messages
```

#### Core Concepts

**Messages**
- `msg_id` — Unique message identifier
- `sesh_id` — Session identifier (links to session)
- `group_id` — Group identifier
- `is_reply` — Boolean, with `reply_to` reference
- `content_length` — Character count for boundary checking
- `mentions` — References to other agents (NOT notifications)
- Agent can only edit own message history (with tracking)

**Sessions**
- Groups of messages bounded by:
  - `time_window` — Maximum duration (e.g., 30 min)
  - `msg_count` — Maximum messages (e.g., 100)
  - `content_length` — Maximum characters (e.g., 50000)
- `timeout_seconds` — Inactivity timeout
- Auto-close when any boundary is reached
- New session auto-created when needed

**Groups**
- Role-based group chat within a channel
- Access controlled by `hierarchy_level`
- Holds sessions and their messages
- Only accessible with right permissions

**Channels**
- Top-level organizational unit
- Maps to team structure (#backend-cell, #pm-all, etc.)
- Contains groups with different access levels

#### Endpoints (Proposed)

```
# Channels
GET    /channels                    # List all accessible channels
GET    /channels/{id}               # Get channel details
POST   /channels                    # Create channel (admin only)

# Messages
GET    /channels/{id}/messages      # Get messages (paginated)
POST   /channels/{id}/messages      # Send message
GET    /messages/{id}               # Get specific message
PUT    /messages/{id}               # Edit own message
DELETE /messages/{id}               # Delete own message

# Sessions
GET    /sessions                    # List sessions
GET    /sessions/{id}               # Get session with messages
POST   /sessions                    # Create new session

# WebSocket
WS     /ws/channels/{id}            # Real-time stream
WS     /ws/agent/{id}               # Agent's output stream

# Notifications
POST   /notifications               # Send notification
GET    /notifications               # Get pending notifications
PUT    /notifications/{id}/ack      # Acknowledge notification

# Search
GET    /search                      # Search messages (full-text)
```

### 8.3 Optimal API

**Purpose:** Knowledge base, RAG queries, prompt optimization, token management

> **Note:** This service is designed to be extensible. The "and MORE" aspects include:
> model routing, cost optimization, response caching, A/B testing prompts, etc.

#### Core Concepts

**Knowledge Base**
- Stores embeddings of all documentation
- Code repositories indexed
- Conversation history searchable
- Decision records queryable

**Prompt Optimization**
- Template management
- Context injection
- Few-shot example selection
- Dynamic prompt construction

**Token Optimization**
- Context window management
- Summarization of long contexts
- Priority-based context selection
- Cost tracking

#### Endpoints (Proposed)

```
# Knowledge Base
POST   /kb/index                    # Index new content
GET    /kb/search                   # Semantic search
GET    /kb/similar                  # Find similar documents
DELETE /kb/documents/{id}           # Remove from index

# RAG
POST   /rag/query                   # Query with RAG context
POST   /rag/context                 # Get context for prompt

# Prompts
GET    /prompts                     # List prompt templates
GET    /prompts/{id}                # Get template
POST   /prompts                     # Create template
POST   /prompts/{id}/render         # Render with variables

# Tokens
POST   /tokens/estimate             # Estimate token count
POST   /tokens/optimize             # Optimize context
GET    /tokens/usage                # Usage statistics
```

### 8.4 Journal API

**Purpose:** Personal agent journals for reflection, growth tracking, and debugging

#### Core Concepts

**Journal Entries**
- Each agent maintains personal journal
- Tied to tasks and sessions
- Reflections on work done
- Learnings captured
- Struggles documented

**Entry Types**
- `task_reflection` — Post-task thoughts
- `decision_log` — Why choices were made
- `learning` — New knowledge gained
- `struggle` — Difficulties encountered
- `general` — Free-form reflection

#### Endpoints (Proposed)

```
# Journals
GET    /journals                    # List all journals (admin)
GET    /journals/{agent_id}         # Get agent's journal

# Entries
GET    /journals/{agent_id}/entries # Get entries (paginated)
POST   /journals/{agent_id}/entries # Create entry
GET    /entries/{id}                # Get specific entry
PUT    /entries/{id}                # Update entry

# Analysis
GET    /journals/{agent_id}/summary # AI-generated summary
GET    /journals/{agent_id}/growth  # Growth metrics over time
GET    /journals/patterns           # Cross-agent pattern analysis
```

---

## 9. Kanban Boards

### 9.1 Board Types Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           KANBAN BOARDS                                     │
└─────────────────────────────────────────────────────────────────────────────┘

Per-Cell Views:
├─► Dev Kanban (per cell)
├─► QA Kanban (per cell)
├─► Documenter Kanban (per cell)
└─► PM Kanban (per cell)

Management Views:
├─► Main PM Kanban
└─► Board Kanban

Special Views:
├─► Auditor Dashboard
└─► CEO Overview
```

### 9.2 Dev Kanban (Per Cell)

```
┌──────────┬───────────┬─────────────┬───────────┬─────────────┬──────────┐
│ Backlog  │ Assigned  │ In Progress │ QA Review │ Documenting │   Done   │
├──────────┼───────────┼─────────────┼───────────┼─────────────┼──────────┤
│          │           │             │           │             │          │
│ Task A   │ Task B    │ Task C      │ Task D    │ Task E      │ Task F   │
│          │ (Dev 1)   │ (Dev 2)     │           │             │          │
│          │           │             │           │             │          │
│          │           │ [blocked]   │           │             │          │
│          │           │ Task G      │           │             │          │
│          │           │             │           │             │          │
└──────────┴───────────┴─────────────┴───────────┴─────────────┴──────────┘

Swim Lanes (optional):
- By priority (P0, P1, P2)
- By developer
- By feature area
```

### 9.3 QA Kanban (Per Cell)

```
┌────────────────┬─────────────┬─────────────┬──────────────┐
│ Awaiting Review│ In Review   │   Passed    │   Failed     │
├────────────────┼─────────────┼─────────────┼──────────────┤
│                │             │             │              │
│ Task D         │ Task H      │ Task E      │ Task I       │
│ Task J         │             │ Task K      │ (back to dev)│
│                │             │             │              │
└────────────────┴─────────────┴─────────────┴──────────────┘
```

### 9.4 Documenter Kanban (Per Cell)

```
┌─────────────────┬─────────────┬─────────────┬─────────────┐
│ Awaiting Handoff│  Gathering  │   Writing   │  Published  │
├─────────────────┼─────────────┼─────────────┼─────────────┤
│                 │             │             │             │
│ Task L          │ Task M      │ Task N      │ Task O      │
│                 │             │             │ Task P      │
│                 │             │             │             │
└─────────────────┴─────────────┴─────────────┴─────────────┘
```

### 9.5 PM Kanban (Per Cell)

```
┌──────────┬──────────┬───────────┬─────────────┬──────────┬──────────┐
│ Incoming │ Triaged  │ Assigned  │ In Progress │ Blocked  │   Done   │
├──────────┼──────────┼───────────┼─────────────┼──────────┼──────────┤
│          │          │           │             │          │          │
│ Task Q   │ Task R   │ Task S    │ Task T      │ Task U   │ Task V   │
│          │ Task W   │           │ Task X      │          │          │
│          │          │           │             │          │          │
└──────────┴──────────┴───────────┴─────────────┴──────────┴──────────┘

Additional Views:
- Dependency graph
- Timeline view
- Workload per dev
```

### 9.6 Main PM Kanban

```
┌──────────┬─────────────┬─────────────────────────────────┬──────────┐
│ Incoming │ Distributed │        In Progress (Cells)      │   Done   │
│          │             ├───────────┬───────────┬─────────┤          │
│          │             │  Backend  │ Frontend  │  UX/UI  │          │
├──────────┼─────────────┼───────────┼───────────┼─────────┼──────────┤
│          │             │           │           │         │          │
│ Epic A   │ Feature B   │ Task 1    │ Task 2    │ Task 3  │ Feature C│
│          │             │ Task 4    │ Task 5    │         │          │
│          │             │           │           │         │          │
└──────────┴─────────────┴───────────┴───────────┴─────────┴──────────┘

Additional Views:
- Cross-cell dependencies
- Blocked items (all cells)
- Risk register
```

### 9.7 Board Kanban

```
┌──────────┬──────────┬───────────────┬──────────┐
│  Ideas   │ Roadmap  │ In Development│ Released │
├──────────┼──────────┼───────────────┼──────────┤
│          │          │               │          │
│ Idea X   │ Feature Y│ Feature Z     │ Feature W│
│ Idea Y   │ Epic Q   │               │ v1.0.0   │
│          │          │               │          │
└──────────┴──────────┴───────────────┴──────────┘

Timeline View:
- Q1, Q2, Q3, Q4 columns
- Release milestones
```

### 9.8 Auditor Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AUDITOR DASHBOARD                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LIVE FEEDS                          │  FLAGGED ITEMS                       │
│  ┌─────────────────────────────────┐ │  ┌─────────────────────────────────┐ │
│  │ #backend-cell    [streaming...] │ │  │ ⚠ Task 42 - Quality concern     │ │
│  │ #frontend-cell   [idle]         │ │  │ ⚠ Agent 7 - Blocked 2 days      │ │
│  │ #uxui-cell       [streaming...] │ │  │ ⚠ Missing documentation (3)     │ │
│  │ #pm-all          [streaming...] │ │  │                                 │ │
│  └─────────────────────────────────┘ │  └─────────────────────────────────┘ │
│                                      │                                      │
│  METRICS                             │  AUDIT QUEUE                         │
│  ┌─────────────────────────────────┐ │  ┌─────────────────────────────────┐ │
│  │ Tasks completed (24h): 12       │ │  │ □ Code review: Feature X        │ │
│  │ Avg completion time: 3.2h       │ │  │ □ Doc audit: Module Y           │ │
│  │ Blockers (active): 2            │ │  │ □ Process check: Backend cell   │ │
│  │ Communication volume: 847 msgs  │ │  │                                 │ │
│  └─────────────────────────────────┘ │  └─────────────────────────────────┘ │
│                                                                             │
│  REPORTS                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ [Draft] Weekly Report - Dec 8, 2025                                  │   │
│  │ [Sent] Daily Summary - Dec 7, 2025                                   │   │
│  │ [Sent] Alert: Quality Issue - Dec 6, 2025                            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.9 CEO Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CEO OVERVIEW                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  HEALTH STATUS                                                              │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┐                  │
│  │   Backend   │  Frontend   │    UX/UI    │    Board    │                  │
│  │    🟢 OK    │    🟢 OK    │   🟡 SLOW   │    🟢 OK    │                  │
│  └─────────────┴─────────────┴─────────────┴─────────────┘                  │
│                                                                             │
│  KEY METRICS                              │  AUDITOR ALERTS                 │
│  ┌──────────────────────────────────────┐ │  ┌───────────────────────────┐  │
│  │ Velocity (weekly): 45 tasks          │ │  │ 🔴 1 urgent               │  │
│  │ Completion rate: 94%                 │ │  │ 🟡 3 warnings             │  │
│  │ Documentation coverage: 87%          │ │  │ Last report: 2h ago       │  │
│  │ Active blockers: 2                   │ │  │                           │  │
│  └──────────────────────────────────────┘ │  └───────────────────────────┘  │
│                                                                             │
│  ROADMAP PROGRESS                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Q4 2025: ████████████████░░░░░░░░░░░░░░░░  45%                      │    │
│  │ v2.0 Release: ██████████████████░░░░░░░░░  60%                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Data Models

### 10.1 Task Model

```python
class Task:
    # Identity
    id: UUID
    title: str
    description: str  # Detailed
    acceptance_criteria: List[str]  # HOW DO WE KNOW IT'S DONE?

    # Status
    status: TaskStatus  # Enum (see below)
    priority: int  # 0 = highest

    # Ownership
    created_by: AgentID
    assigned_to: Optional[AgentID]
    team: Team  # backend | frontend | ux_ui | board

    # Relationships
    parent_task: Optional[TaskID]  # For sub-tasks
    dependencies: List[TaskID]  # Blocked by these
    blockers: List[TaskID]  # Currently blocking these

    # Timestamps
    created_at: datetime
    claimed_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    # Planning
    plan: TaskPlan
    estimated_complexity: Complexity  # low | medium | high

    # Execution
    execution_log: ExecutionLog
    checkpoints: List[Checkpoint]  # Saved states
    progress_updates: List[ProgressUpdate]

    # Artifacts
    commits: List[CommitRef]
    documents: List[DocRef]
    outputs: List[FileRef]

    # Documentation
    dev_notes: str  # Journey notes from dev
    qa_notes: Optional[str]  # QA feedback
    documenter_handoff: Optional[HandoffRequest]
    final_documentation: List[DocRef]

    # Review
    self_verified: bool
    qa_verified: Optional[bool]
    auditor_notes: Optional[str]


class TaskStatus(Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    VERIFYING = "verifying"
    NEEDS_REVISION = "needs_revision"
    AWAITING_QA = "awaiting_qa"
    AWAITING_DOCUMENTATION = "awaiting_documentation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Complexity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Team(Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    UX_UI = "ux_ui"
    BOARD = "board"
```

### 10.2 Agent Model

```python
class Agent:
    id: UUID
    name: str
    role: AgentRole
    team: Optional[Team]  # None for board members

    # Status
    status: AgentStatus  # active | idle | offline
    current_task: Optional[TaskID]

    # Configuration
    model: str  # e.g., "claude-3-opus", "local-llama"
    system_prompt: str
    capabilities: List[str]

    # Permissions
    can_notify: bool
    channels_access: List[ChannelID]
    channels_write: List[ChannelID]

    # Metrics
    tasks_completed: int
    avg_completion_time: float
    quality_score: float

    # Journal
    journal_id: UUID


class AgentRole(Enum):
    CEO = "ceo"
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"
    MAIN_PM = "main_pm"
    CELL_PM = "cell_pm"
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"


class AgentStatus(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"
```

### 10.3 Session Model

```python
class Session:
    """
    A session groups messages within boundaries.
    Sessions can be bounded by time, message count, or content length.
    """
    id: UUID  # sesh_id
    group_id: UUID  # Parent group

    # Boundaries (any can trigger session end)
    max_time_window: Optional[timedelta]  # e.g., 30 minutes
    max_message_count: Optional[int]  # e.g., 100 messages
    max_content_length: Optional[int]  # e.g., 50000 characters

    # Timeout configuration
    timeout_seconds: int  # Inactivity timeout

    # State
    status: SessionStatus  # active | closed | timed_out

    # Timestamps
    started_at: datetime
    last_activity_at: datetime
    closed_at: Optional[datetime]

    # Statistics
    message_count: int
    total_content_length: int


class SessionStatus(Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    TIMED_OUT = "timed_out"
```

### 10.4 Message Model

```python
class RawStream:
    """WebSocket payload - ephemeral"""
    connection_id: UUID
    agent_id: AgentID
    channel_id: UUID
    chunk: str  # Raw LLM output chunk
    timestamp: datetime


class ExtractedMessage:
    """Processed, stored message"""
    id: UUID  # msg_id

    # Source & Context
    agent_id: AgentID
    channel_id: UUID
    group_id: UUID
    session_id: UUID  # sesh_id - links to Session

    # Content
    type: MessageType
    content: str
    content_length: int  # Character count

    # Threading
    is_reply: bool
    reply_to: Optional[MessageID]  # Parent message if is_reply

    # Mentions (for in-channel references, NOT notifications)
    mentions: List[AgentID]

    # Task Context
    task_id: Optional[TaskID]
    commit_ref: Optional[str]

    # Metadata
    timestamp: datetime
    embedding: Vector  # For RAG

    # Extraction metadata
    confidence: float
    raw_excerpt: str

    # Edit tracking (AI can only edit own history)
    edited_at: Optional[datetime]
    edit_history: List[MessageEdit]  # Previous versions


class MessageEdit:
    """Track edits to messages - agents can only edit their own"""
    edited_at: datetime
    previous_content: str
    edit_reason: Optional[str]


class MessageType(Enum):
    REASONING = "reasoning"
    DIALOGUE = "dialogue"
    DECISION = "decision"
    ACTION = "action"
    BLOCKER = "blocker"
    TECHNICAL = "technical"
```

### 10.5 Group Model

```python
class Group:
    """
    Role-based group chat container.
    Groups hold sessions which hold messages.
    Access is controlled by hierarchy/level.
    """
    id: UUID  # group_id
    name: str
    channel_id: UUID  # Parent channel

    # Access Control
    allowed_roles: List[AgentRole]  # Role-based access
    hierarchy_level: int  # 0 = highest (board), 3 = lowest (cell members)

    # Members (derived from roles, but can have explicit additions)
    members: List[AgentID]

    # Settings
    is_active: bool
    created_at: datetime

    # Current Session
    active_session_id: Optional[UUID]

    # Session Configuration (defaults for new sessions)
    default_session_config: SessionConfig


class SessionConfig:
    """Configuration for session boundaries"""
    max_time_window: Optional[timedelta]
    max_message_count: Optional[int]
    max_content_length: Optional[int]
    timeout_seconds: int
```

### 10.6 Notification Model

```python
class Notification:
    id: UUID
    type: NotificationType
    priority: NotificationPriority

    # Routing
    from_agent: AgentID  # Must be PM/Board/Auditor
    to_agents: List[AgentID]

    # Content
    subject: str
    body: str

    # Acknowledgment
    requires_ack: bool
    acked_by: List[AgentID]
    acked_at: Dict[AgentID, datetime]

    # Context
    related_task: Optional[TaskID]
    related_messages: List[MessageID]

    # Timing
    timestamp: datetime
    expires_at: Optional[datetime]


class NotificationType(Enum):
    TASK_ASSIGNMENT = "task_assignment"
    PRIORITY_CHANGE = "priority_change"
    BLOCKER_ESCALATION = "blocker_escalation"
    REVIEW_REQUEST = "review_request"
    DOCUMENTATION_REQUEST = "documentation_request"
    ALERT = "alert"
    BROADCAST = "broadcast"


class NotificationPriority(Enum):
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
```

### 10.7 Channel Model

```python
class Channel:
    id: UUID
    name: str
    type: ChannelType

    # Access Control
    members: List[AgentID]  # Who can see
    writers: List[AgentID]  # Who can write
    silent_observers: List[AgentID]  # Auditor

    # Settings
    is_archived: bool
    created_at: datetime

    # Statistics
    message_count: int
    last_activity: datetime


class ChannelType(Enum):
    CELL = "cell"  # Internal team
    CROSS_CELL = "cross_cell"  # Coordination
    MANAGEMENT = "management"
    SPECIAL = "special"  # Announcements, all-hands
```

### 10.8 Journal Model

```python
class Journal:
    id: UUID
    agent_id: AgentID
    entries: List[JournalEntry]


class JournalEntry:
    id: UUID
    journal_id: UUID

    # Content
    type: JournalEntryType
    title: str
    content: str

    # Context
    task_id: Optional[TaskID]
    session_id: Optional[UUID]

    # Metadata
    timestamp: datetime
    tags: List[str]
    embedding: Vector  # For search


class JournalEntryType(Enum):
    TASK_REFLECTION = "task_reflection"
    DECISION_LOG = "decision_log"
    LEARNING = "learning"
    STRUGGLE = "struggle"
    GENERAL = "general"
```

### 10.9 Handoff Model

```python
class DocumenterHandoff:
    id: UUID
    task_id: TaskID

    # From Dev
    commits: List[CommitRef]
    notes_location: str
    key_conversations: List[MessageID]
    documentation_needed: List[str]  # ["API docs", "README update"]

    # Status
    status: HandoffStatus
    assigned_to: Optional[AgentID]  # Documenter

    # Timestamps
    created_at: datetime
    claimed_at: Optional[datetime]
    completed_at: Optional[datetime]


class HandoffStatus(Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
```

---

## 11. RAG & Knowledge Base

### 11.1 Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RAG ARCHITECTURE                                     │
└─────────────────────────────────────────────────────────────────────────────┘

                         ┌─────────────────┐
                         │  AGENT QUERY    │
                         │  "How did we    │
                         │  handle X?"     │
                         └────────┬────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │    OPTIMAL API          │
                    │    Query Processing     │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
              ▼                  ▼                  ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
    │  CODE INDEX     │ │   DOC INDEX     │ │  CONV INDEX     │
    │                 │ │                 │ │                 │
    │ • Repositories  │ │ • READMEs       │ │ • Messages      │
    │ • Functions     │ │ • API Docs      │ │ • Decisions     │
    │ • Classes       │ │ • Architecture  │ │ • Journals      │
    │ • Comments      │ │ • Guides        │ │ • Task Notes    │
    └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
             │                   │                   │
             └───────────────────┼───────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     VECTOR DB           │
                    │     (Qdrant)            │
                    │                         │
                    │  Stored on UGREEN NAS   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   RELEVANT CONTEXT      │
                    │   Returned to Agent     │
                    └─────────────────────────┘
```

### 11.2 Indexing Strategy

**Code Indexing**
- Repository: fastapi-guard, fastapi-guard-web, etc.
- Chunk by: function, class, module
- Metadata: file path, language, last modified, author

**Documentation Indexing**
- Source: READMEs, /docs folders, wiki
- Chunk by: section, paragraph
- Metadata: doc type, project, version

**Conversation Indexing**
- Source: Extracted messages from Messaging API
- Chunk by: message or conversation thread
- Metadata: channel, agent, task, timestamp, type

**Journal Indexing**
- Source: Journal entries
- Chunk by: entry
- Metadata: agent, task, entry type, timestamp

### 11.3 Embedding Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       EMBEDDING PIPELINE                                    │
└─────────────────────────────────────────────────────────────────────────────┘

Source Content
      │
      ▼
┌─────────────────┐
│    CHUNKING     │
│                 │
│ • Code: AST     │
│ • Docs: Headers │
│ • Conv: Thread  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   EMBEDDING     │
│   (Olares One)  │
│                 │
│ • text-embedding│
│ • code-embedding│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  VECTOR STORE   │
│  (Qdrant/NAS)   │
└─────────────────┘
```

### 11.4 Query Flow

```python
# Example RAG Query Flow

async def query_knowledge_base(query: str, context: QueryContext) -> List[Document]:
    """
    Query the knowledge base with RAG.

    Args:
        query: Natural language query
        context: Current task, agent, project context

    Returns:
        Relevant documents for context injection
    """

    # 1. Generate query embedding
    query_embedding = await embed(query)

    # 2. Search relevant collections
    code_results = await vector_db.search(
        collection="code",
        embedding=query_embedding,
        filter={"project": context.project},
        limit=5
    )

    doc_results = await vector_db.search(
        collection="documentation",
        embedding=query_embedding,
        filter={"project": context.project},
        limit=5
    )

    conv_results = await vector_db.search(
        collection="conversations",
        embedding=query_embedding,
        filter={"task_id": context.task_id},
        limit=3
    )

    # 3. Re-rank and deduplicate
    combined = rerank(code_results + doc_results + conv_results)

    # 4. Return top results
    return combined[:10]
```

---

## 12. Security & Access Control

### 12.1 Permission Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ACCESS CONTROL MODEL                                  │
└─────────────────────────────────────────────────────────────────────────────┘

LEVELS:
├─► L0: CEO (full access)
├─► L1: Board (cross-org access)
├─► L2: Main PM (all cells access)
├─► L3: Cell PM (own cell + PM channel)
├─► L4: Cell Members (own cell only)
└─► SPECIAL: Auditor (silent read all)

RESOURCES:
├─► Channels: read, write, manage
├─► Tasks: view, create, assign, modify, close
├─► Notifications: send, receive
├─► Documents: read, write, publish
├─► Reports: view, create
└─► System: configure, monitor
```

### 12.2 Channel Access Matrix

```
Channel              │ Read Access        │ Write Access       │
─────────────────────┼────────────────────┼────────────────────┤
#backend-cell        │ BE Cell, Auditor   │ BE Cell            │
#frontend-cell       │ FE Cell, Auditor   │ FE Cell            │
#uxui-cell           │ UX Cell, Auditor   │ UX Cell            │
#dev-all             │ All Devs, MPM, AU  │ All Devs           │
#qa-all              │ All QA, MPM, AU    │ All QA             │
#pm-all              │ All PMs, MPM, AU   │ All PMs            │
#doc-all             │ All Docs, MPM, AU  │ All Docs           │
#main-pm-board       │ MPM, Board, AU     │ MPM, Board         │
#board-private       │ Board, CEO, AU     │ Board, CEO         │
#announcements       │ Everyone           │ Board, MPM         │
#all-hands           │ Everyone           │ Everyone           │
```

### 12.3 Task Permission Matrix

```
Action               │ CEO │ Board │ MPM │ PM  │ Dev │ QA  │ Doc │
─────────────────────┼─────┼───────┼─────┼─────┼─────┼─────┼─────┤
View all tasks       │  ✓  │   ✓   │  ✓  │  ○  │  ○  │  ○  │  ○  │
Create task          │  ✓  │   ✓   │  ✓  │  ✓  │  ○  │     │     │
Assign task          │  ✓  │   ✓   │  ✓  │  ✓  │     │     │     │
Claim task           │     │       │     │     │  ✓  │  ✓  │  ✓  │
Update own task      │     │       │     │     │  ✓  │  ✓  │  ✓  │
Close task           │  ✓  │   ✓   │  ✓  │  ✓  │  ✓  │     │     │
Change priority      │  ✓  │   ✓   │  ✓  │  ✓  │     │     │     │

✓ = Full access
○ = Own cell/tasks only
```

### 12.4 Notification Permission Matrix

```
Sender               │ Recipients                              │
─────────────────────┼─────────────────────────────────────────┤
Cell PM              │ Own cell members only                   │
Main PM              │ All PMs, any cell (escalation)          │
Product Owner        │ Main PM, Board                          │
Head Marketing       │ Main PM, Board                          │
Auditor              │ Anyone (special privilege) + CEO        │
CEO                  │ Anyone                                  │
Dev/QA/Documenter    │ Cannot send notifications               │
```

---

## 13. Implementation Roadmap

### 13.1 Phase Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      IMPLEMENTATION PHASES                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Phase 0: Foundation (Weeks 1-2)
├─► Hardware setup (Olares One integration)
├─► Base infrastructure (Docker, networking)
└─► Development environment

Phase 1: Core Services (Weeks 3-6)
├─► Messaging API (basic)
├─► Task management system
└─► Agent orchestration prototype

Phase 2: Communication (Weeks 7-10)
├─► WebSocket implementation
├─► Transcription pipeline
├─► Notification system

Phase 3: Intelligence (Weeks 11-14)
├─► Optimal API (RAG)
├─► Journal API
└─► Knowledge base indexing

Phase 4: Agents (Weeks 15-20)
├─► Agent definitions
├─► Workflow implementation
├─► Cell deployment

Phase 5: Management (Weeks 21-24)
├─► Kanban interfaces
├─► Auditor dashboard
├─► CEO overview

Phase 6: Polish (Weeks 25+)
├─► Performance tuning
├─► Documentation
└─► Continuous improvement
```

### 13.2 Phase 0: Foundation

**Goals:**
- Olares One operational
- Network configured
- Base services running

**Tasks:**
```
□ Receive and setup Olares One
□ Configure network (static IP, DNS)
□ Install Docker and Docker Compose
□ Setup development environment
□ Configure NAS integration
□ Test GPU capabilities
□ Benchmark model inference
```

**Deliverables:**
- Working Olares One with GPU access
- Docker environment ready
- Network topology documented

### 13.3 Phase 1: Core Services

**Goals:**
- Basic messaging between agents
- Task CRUD operations
- Simple agent spawning

**Tasks:**
```
□ Design database schema
□ Implement Messaging API (REST)
□ Implement Task API
□ Create agent base class
□ Build simple orchestrator
□ Setup PostgreSQL
□ Setup Redis (for queues)
```

**Deliverables:**
- Messaging API v0.1
- Task API v0.1
- Agent framework v0.1

### 13.4 Phase 2: Communication

**Goals:**
- Real-time communication
- Message extraction
- Formal notifications

**Tasks:**
```
□ Implement WebSocket server
□ Build transcription service
□ Create message extraction pipeline
□ Implement notification system
□ Add channel management
□ Build permission system
```

**Deliverables:**
- WebSocket streaming
- Transcription pipeline
- Notification API

### 13.5 Phase 3: Intelligence

**Goals:**
- RAG operational
- Knowledge base populated
- Agents can query context

**Tasks:**
```
□ Setup Qdrant on NAS
□ Build embedding pipeline
□ Index existing repositories
□ Implement Optimal API
□ Implement Journal API
□ Create query interface
```

**Deliverables:**
- Optimal API v0.1
- Journal API v0.1
- Indexed knowledge base

### 13.6 Phase 4: Agents

**Goals:**
- All agent types defined
- Workflows implemented
- Cells operational

**Tasks:**
```
□ Define agent prompts per role
□ Implement Dev workflow
□ Implement QA workflow
□ Implement Documenter workflow
□ Implement PM workflows
□ Implement Board workflows
□ Implement Auditor workflow
□ Deploy Backend cell
□ Deploy Frontend cell
□ Deploy UX/UI cell
```

**Deliverables:**
- 17 operational agents
- 3 functioning cells
- Working Board

### 13.7 Phase 5: Management

**Goals:**
- Visual management tools
- Auditor capabilities
- CEO visibility

**Tasks:**
```
□ Build Kanban interfaces
□ Create Auditor dashboard
□ Create CEO overview
□ Implement metrics collection
□ Build reporting system
```

**Deliverables:**
- Management UI
- Reporting system
- Metrics dashboard

### 13.8 Phase 6: Polish

**Goals:**
- Production ready
- Documented
- Optimized

**Tasks:**
```
□ Performance optimization
□ Error handling improvements
□ Documentation completion
□ Testing suite
□ Monitoring and alerting
□ Backup procedures
```

**Deliverables:**
- Production-ready system
- Complete documentation
- Operational runbooks

---

## 14. Development Standards & Best Practices

### 14.1 Universal Principles

These principles apply to ALL agents, regardless of tech stack:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      UNIVERSAL DEVELOPMENT PRINCIPLES                       │
└─────────────────────────────────────────────────────────────────────────────┘

1. NO WORK WITHOUT A TASK
   └─► Everything tracked, everything documented

2. TEST BEFORE COMMIT
   └─► All code must pass tests before any commit

3. LINT AND FORMAT
   └─► Code must pass linting/formatting checks

4. TYPE SAFETY
   └─► Use type hints (Python) or TypeScript strictly

5. DOCUMENT AS YOU GO
   └─► Comments, docstrings, inline documentation

6. SECURITY FIRST
   └─► Validate inputs, sanitize outputs, defensive coding

7. CLEAN COMMITS
   └─► Meaningful messages, atomic changes, linked to tasks

8. PEER REVIEW
   └─► QA reviews all work before closure

9. KNOWLEDGE CAPTURE
   └─► Learnings go to knowledge base, not just memory
```

### 14.2 Stack-Specific Standards

#### Python (Backend)

```yaml
python_standards:
  package_manager: uv  # Fast, modern
  formatter: ruff format
  linter: ruff check
  type_checker: mypy
  test_runner: pytest
  min_coverage: 80%

  workflow:
    before_commit:
      - uv run ruff format .
      - uv run ruff check .
      - uv run mypy src/
      - uv run pytest

  conventions:
    - Use type hints everywhere
    - Pydantic for data validation
    - Async/await for I/O operations
    - Docstrings (Google style)
    - Keep functions < 50 lines
    - Keep files < 500 lines
```

#### TypeScript/React (Frontend)

```yaml
typescript_standards:
  package_manager: pnpm  # Or npm/yarn
  formatter: prettier
  linter: eslint
  type_checker: tsc --noEmit
  test_runner: vitest  # Or jest
  min_coverage: 80%

  workflow:
    before_commit:
      - pnpm format
      - pnpm lint
      - pnpm typecheck
      - pnpm test

  conventions:
    - Strict TypeScript (no any)
    - Functional components with hooks
    - Props interfaces defined
    - JSDoc for complex functions
    - Component files < 300 lines
    - Custom hooks for logic extraction
```

#### UX/UI Design

```yaml
uxui_standards:
  design_tool: Figma  # Or similar
  component_library: Document all components
  handoff_format: Specs + assets exported

  workflow:
    before_handoff:
      - Component specs documented
      - All states covered (hover, active, disabled, error)
      - Responsive breakpoints defined
      - Accessibility notes included
      - Assets exported (SVG, PNG as needed)

  conventions:
    - Design tokens for colors, spacing, typography
    - Component naming matches code
    - Annotate interactions
    - Document edge cases
    - Mobile-first approach
```

### 14.3 Git Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GIT WORKFLOW                                      │
└─────────────────────────────────────────────────────────────────────────────┘

BRANCH NAMING:
├─► feature/{task-id}-{description}    # New features
├─► fix/{task-id}-{description}        # Bug fixes
├─► refactor/{task-id}-{description}   # Code improvements
├─► docs/{task-id}-{description}       # Documentation
└─► hotfix/{task-id}-{description}     # Urgent production fixes

COMMIT MESSAGE FORMAT:
┌─────────────────────────────────────────────────────────────────┐
│ {type}({scope}): {description}                                  │
│                                                                 │
│ {body - what and why}                                           │
│                                                                 │
│ Task: {task-id}                                                 │
│ Co-authored-by: {agent-name}                                    │
└─────────────────────────────────────────────────────────────────┘

TYPES:
├─► feat     # New feature
├─► fix      # Bug fix
├─► docs     # Documentation
├─► style    # Formatting (no code change)
├─► refactor # Code restructuring
├─► test     # Adding tests
├─► chore    # Maintenance tasks
└─► perf     # Performance improvements

EXAMPLE:
feat(auth): add rate limiting to login endpoint

Implements sliding window rate limiting for login attempts.
Uses Redis for distributed counting across instances.
Limits: 5 attempts per minute, 20 per hour.

Task: TASK-042
Co-authored-by: BE-Dev-1
```

### 14.4 Code Review Checklist

Every QA agent uses this checklist:

```markdown
## Code Review Checklist

### Functionality
- [ ] Code does what the task requires
- [ ] Edge cases handled
- [ ] Error states handled gracefully
- [ ] No regressions introduced

### Code Quality
- [ ] Follows project conventions
- [ ] No code duplication
- [ ] Functions/methods are focused (single responsibility)
- [ ] Naming is clear and consistent
- [ ] No dead code or commented-out code

### Type Safety
- [ ] All types properly defined
- [ ] No `any` types (TypeScript) or missing hints (Python)
- [ ] Null/undefined handled properly

### Testing
- [ ] Tests exist for new functionality
- [ ] Tests cover happy path and error cases
- [ ] Tests are readable and maintainable
- [ ] All tests pass

### Security
- [ ] Inputs validated
- [ ] No sensitive data exposed
- [ ] Authentication/authorization correct
- [ ] No SQL injection, XSS, etc.

### Performance
- [ ] No obvious performance issues
- [ ] Database queries optimized
- [ ] No N+1 query problems
- [ ] Appropriate caching considered

### Documentation
- [ ] Public APIs documented
- [ ] Complex logic explained
- [ ] README updated if needed
- [ ] Changelog entry added
```

### 14.5 Automated Quality Gates

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AUTOMATED QUALITY GATES                              │
└─────────────────────────────────────────────────────────────────────────────┘

GATE 1: PRE-COMMIT (Agent-side)
┌─────────────────────────────────────────────────────────────────┐
│ Triggered: Before any commit                                    │
│ Blocking: Yes                                                   │
│                                                                 │
│ Python:                                                         │
│   └─► ruff format --check                                       │
│   └─► ruff check                                                │
│   └─► mypy                                                      │
│                                                                 │
│ TypeScript:                                                     │
│   └─► prettier --check                                          │
│   └─► eslint                                                    │
│   └─► tsc --noEmit                                              │
└─────────────────────────────────────────────────────────────────┘

GATE 2: PRE-PUSH (Agent-side)
┌─────────────────────────────────────────────────────────────────┐
│ Triggered: Before pushing to remote                             │
│ Blocking: Yes                                                   │
│                                                                 │
│ All stacks:                                                     │
│   └─► Run full test suite                                       │
│   └─► Check test coverage >= threshold                          │
│   └─► Verify no secrets in code                                 │
└─────────────────────────────────────────────────────────────────┘

GATE 3: CI/CD (System-side)
┌─────────────────────────────────────────────────────────────────┐
│ Triggered: On pull request                                      │
│ Blocking: Yes                                                   │
│                                                                 │
│ All stacks:                                                     │
│   └─► Full lint/format/type check                               │
│   └─► Full test suite (all Python versions if applicable)       │
│   └─► Security vulnerability scan                               │
│   └─► Dependency audit                                          │
│   └─► Build verification                                        │
└─────────────────────────────────────────────────────────────────┘

GATE 4: QA REVIEW (Human/Agent)
┌─────────────────────────────────────────────────────────────────┐
│ Triggered: When task marked "ready for review"                  │
│ Blocking: Yes                                                   │
│                                                                 │
│   └─► Code review checklist                                     │
│   └─► Manual/exploratory testing                                │
│   └─► Acceptance criteria verification                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 15. Task Management & Context Persistence

### 15.1 The Problem

AI agents have context limits. Sessions end. Memory is imperfect. Without proper task management:
- Work gets lost between sessions
- Agents repeat analysis unnecessarily
- Knowledge isn't captured for future use
- Handoffs between agents fail

### 15.2 The Solution: Structured Task Records

Every task creates a persistent record that:
- Survives session boundaries
- Enables clean handoffs between agents
- Builds project knowledge over time
- Reduces redundant work

### 15.3 Task Directory Structure

```
.tasks/
├── index.md                          # Master index of all tasks
├── templates/                        # Task templates by type
│   ├── feature.md
│   ├── bugfix.md
│   ├── research.md
│   └── documentation.md
│
├── active/                           # Currently in-progress tasks
│   ├── TASK-042-auth-rate-limiting/
│   │   ├── README.md                 # Task overview & status
│   │   ├── requirements.md           # Detailed requirements
│   │   ├── plan.md                   # Implementation plan
│   │   ├── journal.md                # Agent journey notes
│   │   ├── findings.md               # Analysis & discoveries
│   │   ├── decisions.md              # Decisions made & rationale
│   │   ├── blockers.md               # Current blockers (if any)
│   │   ├── handoff.md                # Handoff notes for Documenter
│   │   ├── qa-review.md              # QA feedback
│   │   └── artifacts/                # Code samples, diagrams, etc.
│   │       ├── code-samples/
│   │       └── diagrams/
│   │
│   └── TASK-043-dashboard-redesign/
│       └── ...
│
├── completed/                        # Finished tasks (archived)
│   ├── 2025-12/                      # Organized by month
│   │   ├── TASK-038-fix-memory-leak/
│   │   └── TASK-039-add-dark-mode/
│   └── 2025-11/
│       └── ...
│
└── blocked/                          # Tasks waiting on blockers
    └── TASK-040-integration-api/
        └── ...
```

### 15.4 Task Record Templates

#### README.md (Required)

```markdown
# TASK-{id}: {title}

## Status
- **State**: {pending | in_progress | blocked | review | documenting | completed}
- **Priority**: {P0 | P1 | P2 | P3}
- **Assigned To**: {agent-id}
- **Cell**: {backend | frontend | ux_ui}

## Dates
- **Created**: YYYY-MM-DD
- **Started**: YYYY-MM-DD
- **Target**: YYYY-MM-DD
- **Completed**: YYYY-MM-DD

## Overview
{Brief description of what this task accomplishes}

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Dependencies
- Blocked by: {TASK-XXX, TASK-YYY}
- Blocks: {TASK-ZZZ}

## Key Files
- `path/to/main/file.py`
- `path/to/test/file.py`

## Commits
- `abc1234` - Initial implementation
- `def5678` - Added tests
- `ghi9012` - Fixed edge case

## Quick Context Restore
{2-3 sentences an agent can read to immediately understand the task state}
```

#### plan.md

```markdown
# Implementation Plan: TASK-{id}

## Approach
{High-level approach description}

## Sub-Tasks
- [ ] 1. {Sub-task 1}
  - Estimated: {time}
  - Notes: {any notes}
- [ ] 2. {Sub-task 2}
- [ ] 3. {Sub-task 3}

## Technical Considerations
- {Consideration 1}
- {Consideration 2}

## Risks
- {Risk 1}: {Mitigation}
- {Risk 2}: {Mitigation}

## Open Questions
- [ ] {Question 1}
- [x] {Question 2} → Answer: {answer}
```

#### journal.md

```markdown
# Agent Journey: TASK-{id}

## Session 1 - YYYY-MM-DD HH:MM
**Agent**: {agent-id}

### What I Did
- Analyzed the requirements
- Explored the codebase around X
- Identified approach Y

### What I Learned
- The existing system does Z because...
- There's a related implementation in...

### What I Struggled With
- Understanding the async flow in...
- The documentation for X was unclear

### Next Steps
- [ ] Implement the core logic
- [ ] Add error handling

---

## Session 2 - YYYY-MM-DD HH:MM
**Agent**: {agent-id}

### What I Did
...
```

#### decisions.md

```markdown
# Decisions Log: TASK-{id}

## Decision 1: {Title}
**Date**: YYYY-MM-DD
**Decider**: {agent-id}

### Context
{What situation required a decision}

### Options Considered
1. **Option A**: {description}
   - Pros: ...
   - Cons: ...
2. **Option B**: {description}
   - Pros: ...
   - Cons: ...

### Decision
Chose **Option A** because...

### Consequences
- We will need to...
- This means...

---

## Decision 2: {Title}
...
```

#### handoff.md (For Documenter)

```markdown
# Documentation Handoff: TASK-{id}

## Summary
{What was built, in plain language}

## Documentation Needed
- [ ] API documentation for new endpoints
- [ ] README update for new feature
- [ ] Architecture doc update
- [ ] Changelog entry

## Key Commits
| Commit | Description |
|--------|-------------|
| abc1234 | Main implementation |
| def5678 | Tests |

## Important Conversations
- Message ID: {id} - Discussion about approach
- Message ID: {id} - Decision on X

## Dev Notes Location
See `journal.md` for full journey notes.

## Gotchas for Documentation
- Make sure to mention X limitation
- The Y parameter is optional but important because...

## Code Samples to Include
```python
# Example usage
from module import feature
result = feature.do_thing(param)
```
```

### 15.5 Context Restoration Protocol

When an agent picks up a task (especially one they didn't start):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CONTEXT RESTORATION PROTOCOL                             │
└─────────────────────────────────────────────────────────────────────────────┘

STEP 1: READ TASK RECORD
┌─────────────────────────────────────────────────────────────────┐
│ Required reading (in order):                                    │
│   1. README.md         → Current state, criteria, quick context │
│   2. plan.md           → What's the approach, what's left       │
│   3. journal.md        → What happened, what was learned        │
│   4. decisions.md      → Why things are the way they are        │
│   5. blockers.md       → Any current impediments                │
└─────────────────────────────────────────────────────────────────┘

STEP 2: REVIEW ARTIFACTS
┌─────────────────────────────────────────────────────────────────┐
│ If applicable:                                                  │
│   • Code samples in artifacts/                                  │
│   • Related commits (git log)                                   │
│   • Test files                                                  │
└─────────────────────────────────────────────────────────────────┘

STEP 3: CHECK RELATED CONTEXT
┌─────────────────────────────────────────────────────────────────┐
│ Query knowledge base:                                           │
│   • Similar past tasks                                          │
│   • Related documentation                                       │
│   • Relevant conversation history                               │
└─────────────────────────────────────────────────────────────────┘

STEP 4: ACKNOWLEDGE STATE
┌─────────────────────────────────────────────────────────────────┐
│ Before starting work, add to journal:                           │
│   "Resuming task. Context restored from records."               │
│   "Last state: {summary}"                                       │
│   "My plan: {what I'll do now}"                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 15.6 Task Indexing

The master index for quick lookup:

```markdown
# Task Index

## Active Tasks

| ID | Title | Cell | Assigned | Priority | State | Updated |
|----|-------|------|----------|----------|-------|---------|
| TASK-042 | Auth Rate Limiting | Backend | BE-Dev-1 | P1 | in_progress | 2025-12-08 |
| TASK-043 | Dashboard Redesign | Frontend | FE-Dev-2 | P2 | review | 2025-12-08 |
| TASK-044 | New Logo | UX/UI | UX-Dev-1 | P2 | in_progress | 2025-12-07 |

## Blocked Tasks

| ID | Title | Blocked By | Since |
|----|-------|------------|-------|
| TASK-040 | Integration API | TASK-042 | 2025-12-05 |

## Recently Completed

| ID | Title | Completed | Duration |
|----|-------|-----------|----------|
| TASK-039 | Dark Mode | 2025-12-06 | 3 days |
| TASK-038 | Memory Leak Fix | 2025-12-04 | 1 day |

## Statistics
- Active: 3
- Blocked: 1
- Completed (this month): 12
- Avg completion time: 2.3 days
```

### 15.7 Knowledge Capture Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     KNOWLEDGE CAPTURE WORKFLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

During Task Execution:
┌─────────────────────────────────────────────────────────────────┐
│ Agent captures in journal.md:                                   │
│   • What they tried                                             │
│   • What worked / didn't work                                   │
│   • Why certain approaches were chosen                          │
│   • Gotchas discovered                                          │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼
On Task Completion:
┌─────────────────────────────────────────────────────────────────┐
│ Dev extracts to handoff.md:                                     │
│   • Key learnings                                               │
│   • Patterns used                                               │
│   • Pitfalls to avoid                                           │
│   • Reusable solutions                                          │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼
Documenter Processing:
┌─────────────────────────────────────────────────────────────────┐
│ Documenter creates:                                             │
│   • User-facing documentation                                   │
│   • Developer documentation                                     │
│   • Knowledge base articles (if applicable)                     │
│   • Best practices updates (if applicable)                      │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼
RAG Indexing:
┌─────────────────────────────────────────────────────────────────┐
│ Optimal API indexes:                                            │
│   • Task records (for "how did we solve X before?")             │
│   • New documentation                                           │
│   • Decision rationales                                         │
│   • Code patterns                                               │
└─────────────────────────────────────────────────────────────────┘
```

### 15.8 Cross-Session State Management

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   CROSS-SESSION STATE MANAGEMENT                            │
└─────────────────────────────────────────────────────────────────────────────┘

STATE THAT MUST PERSIST:
├─► Task status and progress
├─► Implementation plan (remaining sub-tasks)
├─► Decisions made and rationale
├─► Blockers and their status
├─► Commits linked to task
├─► Conversation references
├─► Journal entries
└─► Handoff information

STATE STORAGE LOCATIONS:
┌─────────────────────────────────────────────────────────────────┐
│ Source of Truth:                                                │
│   • .tasks/ directory        → Task records (markdown)          │
│   • PostgreSQL               → Task metadata, status, relations │
│   • Git                      → Code changes, commits            │
│   • Vector DB                → Searchable embeddings            │
│                                                                 │
│ Ephemeral (session only):                                       │
│   • Agent working memory     → Current context window           │
│   • Redis                    → Active session state             │
└─────────────────────────────────────────────────────────────────┘

SYNC PROTOCOL:
┌─────────────────────────────────────────────────────────────────┐
│ On session start:                                               │
│   1. Load task record from .tasks/                              │
│   2. Verify against PostgreSQL                                  │
│   3. Load relevant context from Vector DB                       │
│                                                                 │
│ During session:                                                 │
│   1. Update journal.md incrementally                            │
│   2. Save checkpoints to .tasks/ regularly                      │
│   3. Update PostgreSQL on state changes                         │
│                                                                 │
│ On session end:                                                 │
│   1. Final save to .tasks/                                      │
│   2. Update PostgreSQL status                                   │
│   3. Index new content to Vector DB                             │
│   4. Clear Redis session state                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 16. Agent Capabilities & Commands

### 16.1 Specialized Agent Capabilities

Beyond their primary roles, agents can invoke specialized capabilities:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SPECIALIZED CAPABILITIES                                 │
└─────────────────────────────────────────────────────────────────────────────┘

CODE QUALITY & REVIEW
├─► code-review        Deep code review with quality, security, maintainability
├─► architecture       Architectural consistency, SOLID principles
├─► python-expert      Advanced Python, async/await, performance
└─► typescript-expert  Advanced TypeScript, React patterns, hooks

SECURITY & PERFORMANCE
├─► security-audit     Vulnerability review, OWASP compliance
├─► api-security       REST API security, auth, injection, exposure
├─► performance        Profiling, bottlenecks, caching strategies
└─► database-optimize  SQL optimization, indexing, N+1 detection

DEVOPS & INFRASTRUCTURE
├─► devops-debug       Production debugging, log analysis, incidents
├─► deployment         CI/CD, Docker, Kubernetes, cloud
├─► networking         DNS, SSL/TLS, CDN, network security
└─► database-admin     Backups, replication, disaster recovery

DEVELOPMENT SUPPORT
├─► debugger           Error resolution, test failures, unexpected behavior
├─► error-detective    Log searching, stack traces, root cause
├─► api-architect      RESTful design, microservices, schema design
└─► api-documenter     OpenAPI specs, SDK generation, dev docs

RESEARCH & PLANNING
├─► researcher         In-depth research with sources and citations
├─► tech-researcher    Code repos, API docs, implementations
├─► task-decomposer    Breaking complex goals into actionable tasks
└─► context-manager    Managing context across multi-agent workflows

SPECIALIZED
├─► dx-optimizer       Developer experience, tooling, workflows
├─► orchestrator       Complex multi-step workflow coordination
└─► prompt-engineer    Optimizes prompts, expert in prompt patterns
```

### 16.2 Available Commands

Commands are pre-defined workflows agents can invoke:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AVAILABLE COMMANDS                                  │
└─────────────────────────────────────────────────────────────────────────────┘

TESTING & QUALITY
├─► /test                    Run comprehensive test suite
├─► /test-coverage           Run tests with coverage report
├─► /lint                    Run all linters
├─► /typecheck               Run type checker
└─► /code-review             Automated code review

SECURITY
├─► /security-audit          Comprehensive vulnerability scan
├─► /security-hardening      Apply security best practices
├─► /dependency-audit        Check for vulnerable dependencies
└─► /secrets-scan            Scan for exposed secrets

DOCUMENTATION
├─► /doc-api                 Generate API documentation
├─► /doc-architecture        Create architecture docs
├─► /doc-update              Update existing documentation
└─► /changelog               Add changelog entry

DEVELOPMENT
├─► /debug                   Debug complex errors with analysis
├─► /refactor                Code refactoring with patterns
├─► /optimize                Performance optimization
└─► /cleanup                 Code cleanup and dead code removal

PROJECT
├─► /status                  Current task status
├─► /context                 Load context for current task
├─► /handoff                 Prepare handoff documentation
├─► /checkpoint              Save current state
└─► /complete                Mark task complete, trigger handoff

META
├─► /help                    Show available commands
├─► /capabilities            Show available capabilities
└─► /think                   Deep analysis mode
```

### 16.3 Tool Integrations (MCP Servers)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TOOL INTEGRATIONS                                   │
└─────────────────────────────────────────────────────────────────────────────┘

DEVELOPMENT TOOLS
├─► context7              Library documentation retrieval
├─► sequential-thinking   Problem-solving and analysis
├─► task-manager          Task and workflow management
└─► kanban                Project board management

INFRASTRUCTURE
├─► docker                Container operations and management
├─► postgresql            Database operations
├─► redis                 Cache and queue operations
└─► filesystem            File operations in allowed directories

EXTERNAL SERVICES
├─► fetch                 Web content fetching
├─► notion                Notion workspace integration
├─► slack                 Slack messaging and notifications
└─► github                Repository operations

AI/ML
├─► rag-query             Query knowledge base
├─► embeddings            Generate embeddings
└─► prompt-optimize       Optimize prompts for efficiency
```

### 16.4 Quality Hooks

Hooks that run automatically at specific points:

```yaml
hooks:
  post_edit:
    python:
      - command: "ruff format {file}"
        description: "Auto-format Python files"
      - command: "ruff check {file}"
        description: "Lint Python files"
        blocking: true
      - command: "mypy {file}"
        description: "Type check Python files"
        blocking: true

    typescript:
      - command: "prettier --write {file}"
        description: "Auto-format TypeScript files"
      - command: "eslint {file}"
        description: "Lint TypeScript files"
        blocking: true

  pre_commit:
    - command: "run-tests --changed"
      description: "Run tests for changed files"
      blocking: true

  post_commit:
    - command: "update-task-status"
      description: "Update task with commit reference"

  session_end:
    - command: "save-checkpoint"
      description: "Save task state"
    - command: "update-journal"
      description: "Finalize journal entry"
```

---

## Appendix A: Technology Stack

### Infrastructure
| Component | Technology |
|-----------|------------|
| Container Runtime | Docker + Docker Compose |
| Orchestration | Custom Python (Phase 1), K3s (future) |
| Database | PostgreSQL |
| Cache/Queue | Redis |
| Vector DB | Qdrant |
| Object Storage | MinIO (NAS) |

### Backend Services
| Service | Technology |
|---------|------------|
| API Framework | FastAPI |
| WebSockets | FastAPI + websockets |
| Task Queue | Celery / Redis Streams |
| ORM | SQLAlchemy / Tortoise |

### AI/ML
| Component | Technology |
|-----------|------------|
| Cloud LLM | Claude API (Anthropic) |
| Local LLM | Ollama / vLLM |
| Embeddings | text-embedding-3-small / local |
| Agent Framework | Claude Code / Custom |

### Frontend (Future)
| Component | Technology |
|-----------|------------|
| Framework | React / Next.js |
| UI Library | TBD |
| Real-time | WebSocket client |

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **Agent** | An AI instance with a defined role, running on the Olares One |
| **Cell** | A team unit (Backend, Frontend, UX/UI) with Devs, QA, PM, Documenter |
| **Channel** | A communication space where agents stream and chat |
| **Communication** | Constant stream of agent activity (reasoning, dialogue, actions) |
| **Documenter** | Agent responsible for creating production documentation |
| **Journal** | Personal log maintained by each agent |
| **Notification** | Formal signal requiring acknowledgment |
| **Optimal API** | Service providing RAG, prompt optimization, token management |
| **RAG** | Retrieval-Augmented Generation - querying knowledge base for context |
| **Session** | Group of messages within time/count boundaries |
| **Task** | Atomic unit of work, wrapped in the universal lifecycle |
| **Transcription** | Process of extracting structured messages from agent streams |

---

## Appendix C: Configuration Templates

### Agent Configuration Example

```yaml
agent:
  id: "be-dev-1"
  name: "Backend Developer 1"
  role: developer
  team: backend

  model:
    provider: anthropic
    name: claude-3-opus
    fallback: local-llama-70b

  system_prompt: |
    You are a senior backend developer working on the FastAPI Guard ecosystem.
    You follow the task lifecycle strictly and document your journey.
    You communicate constantly in your cell channel.
    You ask questions when unclear.

  capabilities:
    - code_execution
    - git_operations
    - file_management
    - web_search

  permissions:
    can_notify: false
    channels_read:
      - backend-cell
      - dev-all
      - announcements
      - all-hands
    channels_write:
      - backend-cell
      - dev-all
      - all-hands
```

### Channel Configuration Example

```yaml
channel:
  id: "backend-cell"
  name: "#backend-cell"
  type: cell
  team: backend

  members:
    - be-dev-1
    - be-dev-2
    - be-qa
    - be-pm
    - be-doc

  silent_observers:
    - auditor

  settings:
    message_retention_days: 90
    max_message_length: 10000
    allow_threads: true
    allow_reactions: true
```

---

## Appendix D: API Endpoint Summary

### Messaging API
```
# Channels
POST   /api/v1/channels                    # Create channel (admin)
GET    /api/v1/channels                    # List accessible channels
GET    /api/v1/channels/{id}               # Get channel details

# Groups (within channels)
POST   /api/v1/channels/{id}/groups        # Create group
GET    /api/v1/channels/{id}/groups        # List groups in channel
GET    /api/v1/groups/{id}                 # Get group details
PUT    /api/v1/groups/{id}                 # Update group settings

# Sessions (within groups)
POST   /api/v1/groups/{id}/sessions        # Create session
GET    /api/v1/groups/{id}/sessions        # List sessions in group
GET    /api/v1/sessions/{id}               # Get session with messages
PUT    /api/v1/sessions/{id}/close         # Close session manually

# Messages (within sessions)
POST   /api/v1/sessions/{id}/messages      # Send message
GET    /api/v1/sessions/{id}/messages      # Get messages (paginated)
GET    /api/v1/messages/{id}               # Get specific message
PUT    /api/v1/messages/{id}               # Edit own message
DELETE /api/v1/messages/{id}               # Delete own message

# WebSocket (real-time)
WS     /api/v1/ws/channels/{id}            # Channel stream
WS     /api/v1/ws/groups/{id}              # Group stream
WS     /api/v1/ws/agents/{id}              # Agent's output stream

# Notifications
POST   /api/v1/notifications               # Send notification
GET    /api/v1/notifications               # Get pending notifications
PUT    /api/v1/notifications/{id}/ack      # Acknowledge notification

# Search
GET    /api/v1/search/messages             # Search messages (full-text)
GET    /api/v1/search/sessions             # Search sessions
```

### Task API
```
POST   /api/v1/tasks
GET    /api/v1/tasks
GET    /api/v1/tasks/{id}
PUT    /api/v1/tasks/{id}
PUT    /api/v1/tasks/{id}/claim
PUT    /api/v1/tasks/{id}/status
POST   /api/v1/tasks/{id}/handoff
GET    /api/v1/tasks/kanban/{view}
```

### Optimal API
```
POST   /api/v1/kb/index
GET    /api/v1/kb/search
POST   /api/v1/rag/query
POST   /api/v1/prompts
GET    /api/v1/prompts/{id}
POST   /api/v1/prompts/{id}/render
POST   /api/v1/tokens/estimate
```

### Journal API
```
GET    /api/v1/journals/{agent_id}
POST   /api/v1/journals/{agent_id}/entries
GET    /api/v1/journals/{agent_id}/entries
GET    /api/v1/journals/{agent_id}/summary
```

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | Dec 2025 | Renzo Franceschini | Initial blueprint |

---

*This document serves as the foundational blueprint for the AI Agents Company project. It should be updated as the project evolves and new requirements emerge.*