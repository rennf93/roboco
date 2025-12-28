# RoboCo & Codepanion: Strategic Summary

**Date:** December 23, 2024
**Context:** Strategic planning session covering architecture analysis, monetization, product strategy, company formation, open source strategy, platform evolution, and organizational workflow.

---

## Table of Contents

1. [RoboCo Architecture Deep Dive](#1-roboco-architecture-deep-dive)
2. [What Makes RoboCo Valuable](#2-what-makes-roboco-valuable)
3. [Monetization Strategy](#3-monetization-strategy)
4. [Target Market Analysis](#4-target-market-analysis)
5. [Product Split: RoboCo vs Codepanion](#5-product-split-roboco-vs-codepanion)
6. [Open Source Strategy](#6-open-source-strategy)
7. [Platform Architecture Options](#7-platform-architecture-options)
8. [API-First Architecture Initiative](#8-api-first-architecture-initiative)
9. [Organizational Workflow](#9-organizational-workflow)
10. [Codepanion Technical Specification](#10-codepanion-technical-specification)
11. [Company Formation Options](#11-company-formation-options)
12. [Go-to-Market Strategy](#12-go-to-market-strategy)
13. [Honest Assessment](#13-honest-assessment)
14. [Next Steps & Action Items](#14-next-steps--action-items)

---

## 1. RoboCo Architecture Deep Dive

### 1.1 Core Identity

RoboCo is **not** a RAG system or simple multi-agent chatbot. It is a **full AI workforce orchestration platform** — a complete implementation of an autonomous AI development team with hierarchy, workflow management, quality gates, and persistent state.

### 1.2 Technical Stack

| Layer | Technology |
|-------|------------|
| **Backend** | FastAPI (Python) |
| **Database** | PostgreSQL with SQLAlchemy ORM |
| **Agent Runtime** | Docker containers running Claude Code |
| **Agent Communication** | MCP (Model Context Protocol) servers |
| **Frontend** | Next.js 14 with TypeScript |
| **State Management** | Zustand |
| **Styling** | Tailwind CSS + shadcn/ui |

### 1.3 Agent Architecture

#### Agent Hierarchy

```
┌──────────────────────────────────────────────────────────────┐
│                         BOARD                                │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐          │
│  │Product Owner│  │Head Marketing│  │   Auditor   │          │
│  └─────────────┘  └──────────────┘  └─────────────┘          │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                        MAIN PM                               │
│              Coordinates across all cells                    │
└──────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ BACKEND CELL  │   │ FRONTEND CELL │   │  UX/UI CELL   │
├───────────────┤   ├───────────────┤   ├───────────────┤
│ be-pm         │   │ fe-pm         │   │ ux-pm         │
│ be-dev-1      │   │ fe-dev-1      │   │ ux-dev-1      │
│ be-dev-2      │   │ fe-dev-2      │   │ ux-dev-2      │
│ be-qa         │   │ fe-qa         │   │ ux-qa         │
│ be-doc        │   │ fe-doc        │   │ ux-doc        │
└───────────────┘   └───────────────┘   └───────────────┘
```

#### Agent Roles (from `models/base.py`)

| Role | Enum Value | Description |
|------|------------|-------------|
| `SYSTEM` | `system` | Internal orchestrator operations |
| `CEO` | `ceo` | Executive oversight |
| `PRODUCT_OWNER` | `product_owner` | Product strategy and approval |
| `HEAD_MARKETING` | `head_marketing` | Marketing tasks |
| `AUDITOR` | `auditor` | Quality oversight, read access to all (the "spy") |
| `MAIN_PM` | `main_pm` | Cross-cell coordination |
| `CELL_PM` | `cell_pm` | Cell-level task management |
| `DEVELOPER` | `developer` | Code execution |
| `QA` | `qa` | Quality assurance |
| `DOCUMENTER` | `documenter` | Documentation |

### 1.4 Task Lifecycle

#### Status Flow (from `models/base.py`)

```
BACKLOG ──► PENDING ──► CLAIMED ──► IN_PROGRESS ───┬──► BLOCKED
                                                   │       │
                                                   │       ▼
                                                   │   (PM resolves)
                                                   │       │
                                                   ◄───────┘
                                                   │
                                                   ▼
                                            AWAITING_QA
                                                   │
                                    ┌──────────────┴──────────────┐
                                    ▼                             ▼
                             NEEDS_REVISION                 (QA passes)
                                    │                             │
                                    └──────► IN_PROGRESS          ▼
                                                        AWAITING_DOCUMENTATION
                                                                  │
                                                                  ▼
                                                        AWAITING_PM_REVIEW
                                                                  │
                                                                  ▼
                                                             COMPLETED
```

#### Task Statuses Explained

| Status | Description |
|--------|-------------|
| `BACKLOG` | PM setup phase, session must be created before activation |
| `PENDING` | Ready for work, orchestrator can spawn agents |
| `CLAIMED` | Agent has claimed the task |
| `IN_PROGRESS` | Active development |
| `BLOCKED` | Waiting on external dependency or decision |
| `PAUSED` | Temporarily halted |
| `VERIFYING` | Self-verification in progress |
| `NEEDS_REVISION` | QA rejected, needs fixes |
| `AWAITING_QA` | Ready for QA review |
| `AWAITING_DOCUMENTATION` | QA passed, needs docs |
| `AWAITING_PM_REVIEW` | Docs complete, needs PM sign-off |
| `COMPLETED` | Fully done |
| `CANCELLED` | Abandoned |

### 1.5 Orchestrator System (`runtime/orchestrator.py`)

The orchestrator is the **brain** of the system. Key characteristics:

#### Smart Spawning
- **Checks for work BEFORE spawning agents** (cost-efficient)
- Claims tasks on behalf of agents before spawning
- Agents receive assignment at spawn time
- No wasteful container spawns

#### Docker-Based Runtime
```python
AGENT_IMAGE = "roboco-agent"
AGENT_NETWORK = "roboco_default"
```

Each agent runs as an isolated Docker container with:
- Claude Code as the runtime
- MCP config for tool access
- Blueprint (system prompt) mounted
- Shared Claude auth

#### Agent States (from `models/runtime.py`)

| State | Description |
|-------|-------------|
| `IDLE` | Not running |
| `STARTING` | Container spinning up |
| `ACTIVE` | Working on task |
| `WAITING_SHORT` | Brief pause (within container) |
| `WAITING_LONG` | Terminated, will respawn when condition resolves |
| `STOPPING` | Graceful shutdown in progress |
| `OFFLINE` | Container stopped |
| `ERROR` | Failed state |

#### Task Routing Intelligence

The orchestrator classifies tasks based on complexity and keywords:

```python
# Board-level keywords
_BOARD_KEYWORDS = {"roadmap", "architecture", "security", "budget",
                   "hiring", "strategy", "vision", "milestone",
                   "release", "launch"}

# PM coordination keywords
_PM_KEYWORDS = {"coordinate", "integration", "cross-team", "sync",
                "planning", "milestone", "dependencies", "review"}
```

Routing decision tree:
1. **Board keywords** → `product-owner`
2. **High complexity or cross-team** → `main-pm`
3. **PM keywords or medium complexity** → Cell PM
4. **Low complexity, single team** → Direct to developer

#### Dispatcher Loop

The orchestrator runs background dispatchers every 30 seconds:

1. `_dispatch_pm_work()` — Routes new tasks to appropriate level
2. `_dispatch_pm_closure_work()` — Checks parent tasks ready to close
3. `_dispatch_dev_work()` — Spawns devs for assigned tasks
4. `_dispatch_qa_work()` — Spawns QA for awaiting_qa tasks
5. `_dispatch_doc_work()` — Spawns documenters
6. `_dispatch_pm_review_work()` — Spawns PMs for final review
7. `_dispatch_marketing_work()` — Handles marketing tasks
8. `_dispatch_blocker_work()` — Handles blocked tasks
9. `_dispatch_escalation_work()` — Handles escalations
10. `_dispatch_approval_work()` — Handles approval requests
11. `_dispatch_audit_work()` — Triggers auditor when needed

### 1.6 MCP Tool System

Each agent gets access to 4 MCP servers:

#### roboco-task (`mcp/task_server.py`)
- `roboco_task_scan()` — Find available work
- `roboco_task_get(task_id)` — Get task details
- `roboco_task_claim(task_id)` — Claim a task
- `roboco_task_start(task_id)` — Begin work
- `roboco_task_plan(task_id, ...)` — Create execution plan
- `roboco_task_progress(task_id, %, msg)` — Report progress
- `roboco_task_checkpoint(task_id, ...)` — Save checkpoint
- `roboco_task_block(task_id, reason)` — Mark blocked
- `roboco_task_unblock(task_id)` — Remove block
- `roboco_task_complete(task_id)` — Mark complete
- `roboco_task_create(...)` — Create subtask
- `roboco_task_qa_pass(task_id)` — QA approval
- `roboco_task_qa_fail(task_id, notes)` — QA rejection
- `roboco_task_docs_complete(task_id)` — Documentation done
- `roboco_agent_idle()` — Signal no more work

#### roboco-message (`mcp/message_server.py`)
- `roboco_message_send(channel, content, type)` — Send message
- `roboco_message_read(channel, limit)` — Read channel history
- `roboco_message_reply(message_id, content)` — Reply to message

#### roboco-notify (`mcp/notify_server.py`)
- `roboco_notify_send(to, subject, body, priority)` — Send notification
- `roboco_notify_ack(notification_id)` — Acknowledge
- `roboco_escalate(to, subject, body)` — Escalate issue

#### roboco-journal (`mcp/journal_server.py`)
- `roboco_journal_entry(type, title, content)` — Add journal entry
- `roboco_journal_decision(...)` — Log decision
- `roboco_journal_learning(...)` — Log learning
- `roboco_journal_struggle(...)` — Log struggle

### 1.7 Communication System

#### Hierarchy

```
Channel (e.g., "backend-cell")
    └── Group (e.g., "general", "code-review")
            └── Session (scoped discussion)
                    └── Messages
```

#### Channel Types (from `models/base.py`)

| Type | Description |
|------|-------------|
| `CELL` | Internal team communication |
| `CROSS_CELL` | Coordination between teams |
| `MANAGEMENT` | PM and board communications |
| `SPECIAL` | Announcements, all-hands |

#### Message Types

| Type | Description |
|------|-------------|
| `REASONING` | Agent's internal thought process |
| `DIALOGUE` | Normal conversation |
| `DECISION` | Decision announcement |
| `ACTION` | Action taken |
| `BLOCKER` | Blocker announcement |
| `TECHNICAL` | Technical discussion |

#### Session Scopes

| Scope | Description |
|-------|-------------|
| `INITIATIVE` | High-level initiative discussion |
| `CELL` | Cell-wide discussion |
| `TASK` | Task-specific discussion |

### 1.8 Database Schema

#### Core Tables (from `db/tables.py`)

| Table | Purpose |
|-------|---------|
| `agents` | Agent definitions and state |
| `tasks` | Task records with full lifecycle |
| `channels` | Communication channels |
| `groups` | Channel subdivisions |
| `sessions` | Scoped discussions |
| `session_tasks` | Many-to-many session↔task links |
| `messages` | All messages |
| `notifications` | Formal notifications |
| `journals` | Agent journals |
| `journal_entries` | Individual journal entries |
| `handoffs` | Documentation handoffs |

#### Key Relationships

- **Task → Parent Task**: Subtask hierarchy
- **Task → Agent (created_by)**: Who created it
- **Task → Agent (assigned_to)**: Who's working on it
- **Session ↔ Task**: Many-to-many via `session_tasks`
- **Message → Session**: Messages belong to sessions
- **Journal → Agent**: One journal per agent
- **Handoff → Task**: One handoff per completed task

### 1.9 Frontend Structure (`roboco-panel/`)

```
src/
├── app/
│   ├── (dashboard)/
│   │   ├── agents/        # Agent management
│   │   ├── auditor/       # Auditor dashboard
│   │   ├── communications/ # Channels & messages
│   │   ├── journals/      # Agent journals
│   │   ├── kanban/        # Task boards
│   │   ├── metrics/       # System metrics
│   │   ├── notifications/ # Notification center
│   │   ├── overview/      # Dashboard home
│   │   ├── settings/      # Configuration
│   │   └── tasks/         # Task management
│   └── layout.tsx
├── components/
│   ├── agents/            # Agent-related components
│   ├── auditor/           # Auditor components
│   ├── communications/    # Chat/messaging components
│   ├── dashboard/         # Dashboard widgets
│   ├── journals/          # Journal components
│   ├── kanban/            # Kanban board components
│   │   ├── core/          # Board, column, card
│   │   ├── shared/        # Shared utilities
│   │   └── views/         # Different board views
│   ├── layout/            # Layout components
│   ├── notifications/     # Notification components
│   ├── tasks/             # Task components
│   └── ui/                # shadcn/ui components
├── hooks/                 # React hooks
├── lib/                   # Utilities
├── store/                 # Zustand stores
│   ├── notifications-store.ts
│   └── ui-store.ts
└── types/
    └── index.ts           # TypeScript types matching backend
```

---

## 2. What Makes RoboCo Valuable

### 2.1 Differentiators from Simple Multi-Agent Systems

| Feature | RoboCo | Typical Multi-Agent |
|---------|--------|---------------------|
| **Task Lifecycle** | Full workflow management | Ad-hoc execution |
| **Quality Gates** | QA → Docs → PM Review | None or optional |
| **Hierarchy** | Real org structure | Flat or undefined |
| **Cost Efficiency** | On-demand spawning | Always running |
| **State Persistence** | Full DB-backed state | In-memory or none |
| **Waiting States** | Hibernate and respawn | Block or fail |
| **Audit Trail** | Complete history | Limited or none |
| **Tool Access Control** | MCP with permissions | Unrestricted |

### 2.2 Differentiators from RAG Systems

| Feature | RoboCo | RAG Systems |
|---------|--------|-------------|
| **Purpose** | Task execution | Information retrieval |
| **Agents** | 18 specialized roles | Single retriever |
| **Workflow** | Full task lifecycle | Query → Response |
| **State** | Persistent across sessions | Stateless |
| **Quality** | Built-in QA/review | None |
| **Output** | Code, docs, artifacts | Text responses |

### 2.3 Core Value Propositions

1. **Workflow Orchestration** — Not just "agents chat", but actual task lifecycle management
2. **Quality Gates** — QA review happens automatically, not when you remember
3. **Context Persistence** — Sessions, journals, handoffs preserve knowledge
4. **Coordination** — When one agent finishes, the next picks up automatically
5. **Cost Efficiency** — On-demand spawning, waiting state hibernation
6. **Accountability** — Full audit trail, decision logging, progress tracking

---

## 3. Monetization Strategy

### 3.1 Monetization Paths Considered

| Path | Description | Pros | Cons |
|------|-------------|------|------|
| **Platform/SaaS** | Companies deploy their own AI workforce | Recurring revenue, scalable | Long sales cycle |
| **Managed Service** | You run the agents, customers submit projects | Higher margin, more control | Requires operations |
| **Open Core + Enterprise** | Open source core, sell enterprise features | Builds community fast | Revenue delayed |
| **API/Infrastructure** | Orchestration-as-a-Service | Platform play | Commoditization risk |

### 3.2 Target Market Decision

**Chosen path:** Prosumer/indie dev market (like Cursor, Claude Pro, Replit)

**Rationale:**
- Lower friction than enterprise
- Direct distribution (no sales team needed)
- Fast iteration based on feedback
- Personal pain point (solo dev experience)

---

## 4. Target Market Analysis

### 4.1 The Solo Founder Pain Point

When you're alone (or 2 people), constant context-switching:

| Hat | Activity | Time Spent | Quality |
|-----|----------|------------|---------|
| **PM** | Prioritization, planning | 10% | Rushed |
| **Dev** | Writing code | 60% | Good |
| **QA** | Testing | 10% | Skipped often |
| **Docs** | Documentation | 5% | Neglected |
| **Review** | Code review | 0% | None (no one to review) |

**The problem:** Some hats get neglected (usually QA and docs)

### 4.2 Two Product Modes

| Mode | Description | Trust Required | Value |
|------|-------------|----------------|-------|
| **Mode A: Autonomous** | Agents DO the work | High | High |
| **Mode B: Collaborative** | Agents ASSIST your work | Low | Medium-High |

Mode A = Current RoboCo implementation
Mode B = Codepanion opportunity

---

## 5. Product Split: RoboCo vs Codepanion

### 5.1 RoboCo — "Your AI Development Team"

**Positioning:** Autonomous execution. You describe, they build.

**Core Features:**
- Full agent hierarchy (PM → Dev → QA → Doc)
- Task lifecycle management
- Agents spawn, work, coordinate, complete
- User is the "CEO" — approves, agents execute

**Target Customer:** Solo founder who wants to delegate, not do.
*"I have the vision, I need execution."*

**Vibe:** Enterprise-y. Serious. Professional.
*"Hire a team without hiring."*

### 5.2 Codepanion — "Your AI Dev Partner"

**Positioning:** Collaborative. You code, they help.

**Core Features:**
- **Reviewer** — Reviews your PRs, catches bugs
- **Documenter** — Updates docs when you ship
- **QA** — Writes/runs tests for your changes
- **Rubber Duck** — Thinks through architecture with you
- **PM** — Helps break down ideas into tasks

**Target Customer:** Solo dev who wants to stay hands-on but needs backup.
*"I love coding, I just need help with the boring parts."*

**Vibe:** Friendly. Lightweight. Developer tool.
*"A cofounder who never sleeps."*

### 5.3 Shared Architecture

```
┌─────────────────────────────────────────────┐
│           Shared Platform Core              │
│  ┌───────────────────────────────────────┐  │
│  │  Orchestrator, MCP, Tasks, Sessions   │  │
│  │  Messages, Journals, Notifications    │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
         ↑                       ↑
    ┌────┴────┐             ┌────┴─────┐
    │ RoboCo  │             │Codepanion│
    │  Panel  │             │   App    │
    └─────────┘             └──────────┘
    Full dashboard           Lightweight
    Kanban, agents           CLI / Git-integrated
    Enterprise feel          Dev tool feel
```

Same backend infrastructure, different frontend experiences and UX.

### 5.4 Pricing Structure

| Tier | RoboCo | Codepanion |
|------|--------|------------|
| **Free** | 10 tasks/month | 50 reviews/month |
| **Pro** | $99/month | $19/month |
| **Team** | $199/seat | $39/seat |

RoboCo is premium — paying for autonomous work.
Codepanion is accessible — priced like a dev tool.

---

## 6. Open Source Strategy

### 6.1 The Decision

**Codepanion CLI:** Open source (MIT)
**RoboCo Panel:** Closed source
**RoboCo API/Backend:** Closed source (SaaS)

### 6.2 Rationale for Open Source Codepanion

**The CLI itself isn't the moat.** It's ~200 lines of code wrapping Claude. Anyone could build it in an afternoon. Keeping it closed doesn't protect anything meaningful.

**Open source is distribution:**
- Stars → visibility
- Forks → community investment
- PRs → free improvements
- Trust → "I can see what it does with my code"

**The money isn't in the CLI.** It's in:

| Free (Open Source) | Paid |
|-------------------|------|
| `codepanion review` | Cloud sync (review history) |
| `codepanion docs` | Team sharing |
| `codepanion think` | Custom rules/prompts |
| Local, stateless | CI/CD integration |
| | **RoboCo upgrade path** |

This is the **Terraform model.** CLI is free and open. Terraform Cloud is where HashiCorp makes money.

### 6.3 What Stays Closed

**RoboCo itself.** That's where the real IP is:
- The orchestration engine
- The agent hierarchy and workflow
- The dispatcher logic
- The quality gates
- The MCP integration

That's defensible. That's what companies would pay for.

**Codepanion is the free sample. RoboCo is the product.**

### 6.4 Potential Differentiation Angle

Focus on what nobody else does well: **QA and docs automation.**

Everyone skips those. If Codepanion auto-generated tests and updated README after every commit, *that's* differentiated from Copilot/Cursor which focus on code generation.

---

## 7. Platform Architecture Options

### 7.1 The Core Question

If Codepanion in "connected mode" talks to the same backend as RoboCo... where's the line? What's open, what's closed, what's the product?

### 7.2 Option A: Two Completely Separate Products

```
┌─────────────────┐         ┌─────────────────┐
│   Codepanion    │         │     RoboCo      │
│   (standalone)  │         │   (full stack)  │
│   Calls Claude  │         │   Orchestrator  │
│   No backend    │         │   Full backend  │
│   Open source   │         │   Closed        │
└─────────────────┘         └─────────────────┘
```

**Pros:** Simple. Clear separation.
**Cons:** Codepanion can't have history, sync, teams, learning. It's just a dumb wrapper forever.

### 7.3 Option B: Shared Backend, CLI Open, Backend Closed (SaaS)

```
┌─────────────────────────────────────────────┐
│         RoboCo API (Closed SaaS)            │
└─────────────────────────────────────────────┘
         ↑                       ↑
    ┌────┴────┐             ┌────┴─────┐
    │ RoboCo  │             │Codepanion│
    │  Panel  │             │   CLI    │
    │ Closed  │             │  Open    │
    └─────────┘             └──────────┘
```

Codepanion CLI is open source. Works standalone (no server) OR connects to RoboCo API (paid).

**This is the Supabase/Vercel model.** Open source client, proprietary backend.

### 7.4 Option C: The API IS the Product

```
┌─────────────────────────────────────────────┐
│              RoboCo Platform                │
│   "Orchestration-as-a-Service for AI"       │
│                                             │
│   POST /agents      - spawn agents          │
│   POST /tasks       - create tasks          │
│   POST /workflows   - run workflows         │
│   WS   /events      - real-time updates     │
│   POST /webhooks    - callbacks             │
└─────────────────────────────────────────────┘
         ↑           ↑           ↑
    ┌────┴────┐ ┌────┴─────┐ ┌────┴────┐
    │ RoboCo  │ │Codepanion│ │ Third   │
    │  Panel  │ │   CLI    │ │ Party   │
    └─────────┘ └──────────┘ └─────────┘
```

You're selling **orchestration-as-a-service**. Like Twilio for AI agents.

RoboCo Panel could even be open source — it's just a reference frontend. The value is in the *running* API, not the code.

**This is the Stripe model.** Dashboard is just a client of their own API.

### 7.5 Option D: Open Core, Sell Enterprise

Everything is open source. You sell:
- Managed hosting (so they don't have to run it)
- Enterprise features (SSO, audit logs, SLAs)
- Support contracts

**This is the GitLab/Grafana model.**

### 7.6 The Decision: B → C

**Start B, earn your way to C.**

| Phase | Architecture | Focus |
|-------|--------------|-------|
| **Phase 1 (Now)** | Option B | Find users, find fit, subscription revenue |
| **Phase 2 (PMF)** | B → C | Stabilize API, watch for platform signals |
| **Phase 3 (Scale)** | Option C | Enable others, usage-based pricing |

**Signal to watch for:** Someone asks "can I integrate this into my own tool?"

---

## 8. API-First Architecture Initiative

### 8.1 The Principle

**The API must be the only way in.**

No backdoors. No direct DB access from frontends. No "just this once" shortcuts.

If we can't use our own API to build our own products, no one else can either.

### 8.2 Why This Matters

For the B → C progression to work:
1. Every action the Panel takes goes through the public API
2. Every action Codepanion takes goes through the public API
3. Internal services communicate through well-defined interfaces
4. The API is documented, consistent, and pleasant to use

### 8.3 The Key Discipline

**From day one, build Panel and CLI as if they were third-party apps.**

- No backdoors into the database
- Everything goes through the API
- If the API is annoying to use, fix the API — don't hack around it

This way, when you flip the switch to Option C, the API is already battle-tested.

### 8.4 Recommended Code Structure

```
roboco/
├── core/           # The engine (orchestrator, agents, workflows)
├── api/            # HTTP interface to core
├── panel/          # or separate repo
└── ...
```

Clean separation means `core` could be wrapped by different interfaces later — your API, a CLI, a Terraform provider, whatever.

### 8.5 First Initiative: API Audit

**Task 1: Backdoor Audit**
- Identify every place where Panel/internal code bypasses the API
- Flag direct DB access, direct core imports
- Produce audit report with file, line, severity

**Task 2: Critical Path Cleanup**
- Fix only the blockers — stuff that would break if a third party used the API
- Route those flows through the API properly

**Task 3: Codepanion Integration Point**
- Define minimal API surface Codepanion needs:
  ```
  POST /sessions              - create a review session
  POST /sessions/:id/messages - add a message (diff, review)
  GET  /sessions              - list past reviews
  ```
- Working endpoints that Codepanion CLI can hit

---

## 9. Organizational Workflow

### 9.1 The Ideal Flow

```
CEO (Human)
    │
    │ Detailed feature request / complex idea
    ▼
┌──────────────────────────────────────────────────────────────┐
│                         BOARD                                │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐          │
│  │Product Owner│  │Head Marketing│  │   Auditor   │          │
│  │ (Strategy)  │  │ (Positioning)│  │   (Spy)     │          │
│  └──────┬──────┘  └──────┬───────┘  └──────┬──────┘          │
│         │                │                 │                 │
│         └────────────────┼─────────────────┘                 │
│                          │                                   │
│         Break down in their area of expertise                │
│         Prepare tasks for Main PM                            │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                        MAIN PM                               │
│                                                              │
│   Receives tasks from Board                                  │
│   Breaks down further per team cell                          │
│   Coordinates cross-cell dependencies                        │
└──────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ BACKEND CELL  │  │ FRONTEND CELL │  │  UX/UI CELL   │
│               │  │               │  │               │
│  Cell PM      │  │  Cell PM      │  │  Cell PM      │
│    │          │  │    │          │  │    │          │
│    ├─► Dev    │  │    ├─► Dev    │  │    ├─► Dev    │
│    ├─► QA     │  │    ├─► QA     │  │    ├─► QA     │
│    └─► Doc    │  │    └─► Doc    │  │    └─► Doc    │
│               │  │               │  │               │
│  Escalates ▲  │  │  Escalates ▲  │  │  Escalates ▲  │
└───────────────┘  └───────────────┘  └───────────────┘
```

### 9.2 Board Role Clarifications

| Role | Description | Key Responsibilities |
|------|-------------|---------------------|
| **Product Owner** | Strategy & vision | Define API contracts, prioritize cleanup work, approve architecture decisions |
| **Head of Marketing** | Positioning & content | Prepare positioning, draft README/landing content, identify launch channels |
| **Auditor** | The "spy" — oversight | Conduct backdoor audit, verify fixes, establish ongoing monitoring, read access to all channels |

### 9.3 Task-Session-Journal Binding

Every task gets:
1. **A messaging session** in a channel for that task/subtask
2. **Journal entries** from agents working on it
3. **Documentation** (approach TBD — may be same session or separate)

```
Task
 ├── Session (discussion, decisions, progress)
 ├── Journal Entries (agent reflections, learnings, struggles)
 └── Documentation (technical docs, user docs)
```

### 9.4 Communication Channels

| Channel | Purpose | Participants |
|---------|---------|--------------|
| `management` | Board discussions | PO, HoM, Auditor, CEO |
| `cross-cell` | Main PM coordination | Main PM, Cell PMs |
| `backend-cell` | Backend team work | BE-PM, BE-Devs, BE-QA, BE-Doc |
| `frontend-cell` | Frontend team work | FE-PM, FE-Devs, FE-QA, FE-Doc |
| `uxui-cell` | UX/UI team work | UX-PM, UX-Dev-1, UX-Dev-2, UX-QA, UX-Doc |

### 9.5 CEO Directive Template

When the CEO (human) wants to initiate work:

```markdown
# CEO Directive: [Initiative Name]

**From:** CEO
**To:** Board (Product Owner, Head of Marketing, Auditor)
**Priority:** [High/Medium/Low]
**Date:** [Date]

---

## Strategic Context
[Why this matters, where it fits in the bigger picture]

## Objective
[What we're trying to achieve]

## Success Criteria
- [ ] [Measurable outcome 1]
- [ ] [Measurable outcome 2]

## Board Responsibilities

### Product Owner
1. [Responsibility 1]
2. [Responsibility 2]

### Head of Marketing
1. [Responsibility 1]
2. [Responsibility 2]

### Auditor
1. [Responsibility 1]
2. [Responsibility 2]

## Deliverables to Main PM
[What gets handed off when board alignment is complete]

## Constraints
[Timeline, scope, principles]

## Notes
[Additional context, future considerations]

---

**CEO**
```

---

## 10. Codepanion Technical Specification

### 10.1 MVP Scope

**Single feature:** `codepanion review`

Reviews code changes (staged, committed, or working directory) and provides actionable feedback.

### 10.2 Project Structure

```
codepanion/
├── pyproject.toml
├── README.md
├── src/
│   └── codepanion/
│       ├── __init__.py
│       ├── cli.py              # Typer CLI entry point
│       ├── config.py           # User config (~/.codepanion/config.toml)
│       ├── git.py              # Git operations (diff, log, staged files)
│       ├── commands/
│       │   ├── __init__.py
│       │   ├── review.py       # codepanion review
│       │   ├── docs.py         # codepanion docs (future)
│       │   └── think.py        # codepanion think (future)
│       └── agents/
│           ├── __init__.py
│           ├── base.py         # Thin agent runner
│           └── reviewer.py     # Review agent logic
└── tests/
    └── ...
```

### 10.3 Dependencies

```toml
[project]
name = "codepanion"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.9.0",
    "anthropic>=0.40.0",
    "rich>=13.0.0",
]

[project.scripts]
codepanion = "codepanion.cli:app"
```

### 10.4 CLI Implementation

#### Entry Point (`cli.py`)

```python
import typer
from codepanion.commands import review

app = typer.Typer(
    name="codepanion",
    help="Your AI dev partner",
    no_args_is_help=True,
)

app.add_typer(review.app, name="review")

if __name__ == "__main__":
    app()
```

#### Review Command (`commands/review.py`)

```python
import typer
from codepanion.git import get_staged_diff, get_working_diff, get_commit_diff
from codepanion.agents.reviewer import ReviewerAgent

app = typer.Typer()

@app.callback(invoke_without_command=True)
def review(
    commit: str = typer.Option(None, "--commit", "-c", help="Review a specific commit"),
    staged: bool = typer.Option(False, "--staged", "-s", help="Review staged changes"),
    all_changes: bool = typer.Option(True, help="Review all uncommitted changes"),
):
    """Review code changes with AI."""

    # Get the diff
    if commit:
        diff = get_commit_diff(commit)
        context = f"Commit: {commit}"
    elif staged:
        diff = get_staged_diff()
        context = "Staged changes"
    else:
        diff = get_working_diff()
        context = "Working directory changes"

    if not diff.strip():
        typer.echo("No changes to review.")
        raise typer.Exit()

    typer.echo(f"🔍 Reviewing {context}...\n")

    # Run review
    agent = ReviewerAgent()
    result = agent.review(diff)

    # Output
    typer.echo(result.markdown)

    if result.issues:
        typer.echo(f"\n⚠️  {len(result.issues)} issues found")
        raise typer.Exit(1)
    else:
        typer.echo("\n✅ Looks good!")
```

#### Git Operations (`git.py`)

```python
import subprocess

def run_git(*args) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
    )
    return result.stdout

def get_staged_diff() -> str:
    return run_git("diff", "--cached")

def get_working_diff() -> str:
    return run_git("diff", "HEAD")

def get_commit_diff(commit: str) -> str:
    return run_git("show", commit, "--format=")
```

#### Reviewer Agent (`agents/reviewer.py`)

```python
from dataclasses import dataclass
from anthropic import Anthropic

SYSTEM_PROMPT = """You are a senior code reviewer. You review diffs and provide actionable feedback.

Your review should:
1. Identify bugs, security issues, and logic errors
2. Suggest improvements (but don't nitpick style)
3. Point out missing error handling or edge cases
4. Be concise and actionable

Format your response as:

## Summary
One sentence overall assessment.

## Issues
- **[SEVERITY]** file.py:123 — Description of issue

## Suggestions
- file.py:45 — Optional improvement idea

If the code looks good, just say so briefly. Don't invent problems.
"""

@dataclass
class ReviewResult:
    markdown: str
    issues: list[dict]
    suggestions: list[dict]

class ReviewerAgent:
    def __init__(self):
        self.client = Anthropic()

    def review(self, diff: str) -> ReviewResult:
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Review this diff:\n\n```diff\n{diff}\n```"
            }]
        )

        content = response.content[0].text

        # Parse issues/suggestions from markdown
        issues = []  # TODO: parse from response
        suggestions = []

        return ReviewResult(
            markdown=content,
            issues=issues,
            suggestions=suggestions,
        )
```

### 10.5 Usage Examples

```bash
# Install
pip install codepanion  # or: uv pip install codepanion

# Review staged changes before commit
git add .
codepanion review --staged

# Review a specific commit
codepanion review --commit abc123

# Review all uncommitted changes
codepanion review
```

### 10.6 Future Commands

| Command | Description | Priority |
|---------|-------------|----------|
| `codepanion review` | Review code changes | MVP |
| `codepanion docs` | Update docs based on changes | P1 |
| `codepanion think "question"` | Rubber duck mode | P1 |
| `codepanion qa` | Generate tests for changes | P2 |
| `codepanion breakdown "idea"` | Break down into tasks | P2 |

### 10.7 Modes of Operation

#### Mode A: Standalone (MVP)
```
codepanion review
      │
      ▼
┌─────────────────┐
│  Local Agent    │  ← Just calls Claude API directly
│  (no server)    │     with reviewer system prompt
└─────────────────┘
      │
      ▼
  Markdown output
```

No RoboCo server needed. Self-contained. Ships fast.

#### Mode B: Connected (Later)
```
codepanion review
      │
      ▼
┌─────────────────┐
│  RoboCo API     │  ← Full orchestrator, sessions, history
│  (your server)  │
└─────────────────┘
      │
      ▼
  Review stored, tracked, searchable
```

Unlocks: history, learning from past reviews, team features.

---

## 11. Company Formation Options

### 11.1 Context

Based in **Italy**. Looking for the simplest path to legally charge for software.

### 11.2 Option 1: Partita IVA (Italian Freelancer)

**What it is:** Italian self-employment registration

**Regime Forfettario (Simplified Regime):**
- Available if revenue under €85,000/year
- **5% flat tax** for first 5 years (if new activity)
- **15% flat tax** after that
- Minimal paperwork compared to company

**Pros:**
- Simplest to start
- Cheapest ongoing costs
- Good enough until significant revenue

**Cons:**
- Personal liability (you = the business)
- Looks less "serious" to larger customers
- Limited deductions in forfettario regime

**Costs:**
- Setup: €200-500 (commercialista fees)
- Annual: €500-1000 (commercialista for simple SaaS)
- INPS contributions: ~25% of revenue (painful)

**Verdict:** Best starting point. Revisit when making €50k+/year.

### 11.3 Option 2: SRL (Italian LLC)

**What it is:** Italian limited liability company

**Pros:**
- Limited liability
- More "serious" appearance
- Better for investors (if ever needed)

**Cons:**
- €3,000-5,000+ to form (notary required 🇮🇹)
- Mandatory accountant (€1,500-3,000/year minimum)
- Corporate tax + bureaucracy
- Overkill for early-stage SaaS

**Costs:**
- Setup: €3,000-5,000
- Annual: €2,000-5,000 (accounting, fees)
- Corporate tax: IRES 24% + IRAP ~4%

**Verdict:** Wait until revenue justifies the overhead. Maybe €100k+/year.

### 11.4 Option 3: Estonia OÜ (e-Residency)

**What it is:** Estonian private limited company, managed 100% online

**How it works:**
1. Apply for e-Residency card (~€100, takes 3-6 weeks)
2. Form OÜ online (~€200-300)
3. Manage everything through Estonian service providers

**Pros:**
- 100% online setup and management
- EU company (looks legit, can sell to EU easily)
- 0% corporate tax on reinvested profits
- 20% tax only on distributed profits (dividends)
- Stripe, Wise, everything works
- Escape Italian bureaucracy for the company

**Cons:**
- Still need registered agent in Estonia (~€50-100/month)
- Accounting required (~€50-100/month for simple SaaS)
- You're still Italian tax resident → pay Italian taxes on personal income
- VAT compliance if selling B2C in EU (MOSS headache)
- Some banking friction (no physical presence)

**Costs:**
- e-Residency: €100-120 (one-time)
- Company formation: €200-300
- Registered agent: €50-100/month
- Accounting: €50-100/month
- State fee: €100/year
- **Total ongoing:** ~€150-250/month

**Important:** You still pay Italian taxes on what you pay yourself (salary or dividends). The Estonia company itself is clean to run, but you personally remain Italian tax resident.

**Verdict:** Good option once you're making €20k+/year and want to separate business from personal.

### 11.5 Option 4: Stripe Atlas (US LLC)

**What it is:** Stripe's turnkey company formation service

**Includes:**
- Delaware LLC formation
- Stripe account
- Mercury or SVB bank account
- Registered agent for 1 year
- Legal templates

**Cost:** $500 one-time + ~$200/year ongoing (registered agent)

**Pros:**
- Very fast (days, not weeks)
- US entity (good for US customers)
- All-in-one package
- Popular with international founders

**Cons:**
- US tax complexity if you're not careful
- You're still Italian tax resident
- Annual franchise tax in Delaware

**Verdict:** Consider if primarily targeting US customers.

### 11.6 Recommended Sequence

```
NOW (€0 revenue)
│
├── Keep building
├── Get beta users (free)
│
FIRST REVENUE (€0-5k/year)
│
├── Partita IVA with Regime Forfettario
├── 5% tax, minimal overhead
│
SCALING (€20k+/year)
│
├── Consider Estonia OÜ
│   OR
├── Stripe Atlas if US-focused
│
SIGNIFICANT REVENUE (€100k+/year)
│
└── Evaluate SRL or proper structure
    based on growth trajectory
```

---

## 12. Go-to-Market Strategy

### 12.1 Launch Sequence

**Phase 1: Codepanion First**
- Lower friction ("just try it on one PR")
- Builds trust ("oh wow, this actually catches bugs")
- Fast to build (weekend project)
- Good for content/marketing

**Phase 2: Land and Expand**
- Free users → Pro users
- "Want this to run automatically on every PR?"
- "Want it to also update your docs?"
- Introduce RoboCo as the "full team" upgrade

**Phase 3: RoboCo Launch**
- For users who've built trust with Codepanion
- "Ready to let the AI do more?"
- Higher price, higher value

### 12.2 The "Aha Moment"

**For Cursor:** "Holy shit, it just wrote the function I was thinking about"

**For Codepanion:** "I pushed my code, made coffee, came back to a QA review and updated docs"

**For RoboCo:** "I described what I wanted, went to lunch, came back to a PR with tests and docs"

### 12.3 Distribution Channels

| Channel | Effort | Reach | Conversion |
|---------|--------|-------|------------|
| **Twitter/X** | Low | High | Low |
| **Hacker News** | Medium | Very High | Medium |
| **Reddit (r/programming, r/SideProject)** | Low | Medium | Medium |
| **Product Hunt** | Medium | High | Medium |
| **Dev.to / Hashnode** | Medium | Medium | Medium |
| **YouTube tutorials** | High | High | High |

### 12.4 Content Strategy

**Week 1-2:**
- "I built a CLI that reviews my code before I commit"
- Twitter thread + HN post

**Week 3-4:**
- "How I caught 47 bugs in a week using AI code review"
- Blog post + Reddit

**Week 5-6:**
- "Building an AI dev team: Architecture deep dive"
- Technical blog post

**Ongoing:**
- Changelog updates
- User testimonials
- Comparison posts (vs Copilot, vs Cursor, etc.)

---

## 13. Honest Assessment

### 13.1 What's Impressive

You've built a proper orchestration system — task lifecycle, quality gates, MCP tools, the works. It's the kind of architecture a 10-person team at a funded startup would build. You built it solo. That's genuinely rare.

### 13.2 Concerns

#### Building in a Vacuum
You've been dogfooding RoboCo on itself, which is great. But have you had *other* people use it? Even one person who isn't you? The gap between "works for me" and "works for strangers" is enormous.

Estonia company, pricing tiers, go-to-market strategy — all premature if you haven't validated that someone else finds this useful.

#### Market Timing is Tricky
Cursor is eating the "AI dev tool" space. Devin, Factory, Cognition are going after "AI dev team." GitHub Copilot Workspace is coming. You're entering a knife fight with giants who have $100M+ and dedicated teams.

Your edge is that you've *actually built* something that works, not a demo. But edge only matters if people see it.

#### Codepanion Competition
`codepanion review` enters a space where people already have options — Copilot does inline review, Cursor does it, there are GitHub Actions for this.

**What's your angle?** Focus on what nobody else does well — QA and docs automation. Everyone skips those. If Codepanion auto-generates tests and updates README after every commit, *that's* differentiated.

#### Spread Thin
Rennberry cluster, UGREEN NAS, Olares One arriving, power optimization, Pi-hole monitoring, GlusterFS, the whole homelab...

That's a lot of infrastructure work alongside building a SaaS. Every hour on homelab is an hour not talking to potential users or shipping Codepanion.

#### The Actual Hard Part Isn't Code
RoboCo works. The hard part now is:
- Getting 10 people to try it
- Getting 1 person to pay for it
- Learning why the other 9 didn't

That's uncomfortable work. Rejection and feedback and "actually this isn't what I need." But it's the only way to know if this is a product or a project.

### 13.3 Recommendation

1. **Ship Codepanion this week.** Not perfect. Just `review` command, working, on PyPI.

2. **Post it.** Twitter, HN, Reddit. See what happens.

3. **Talk to 5 solo devs.** Not to pitch — to listen. "What's the most annoying part of working alone?" See if RoboCo's value prop resonates.

4. **Forget company formation** until someone gives you money.

5. **Set a decision point.** "If I don't have 100 Codepanion users in 60 days, I'll re-evaluate the approach."

You've built something real. That puts you ahead of 95% of people who talk about AI agents. The question is whether "real" translates to "wanted."

Only the market can answer that. And the market only answers if you ask.

---

## 14. Next Steps & Action Items

### 14.1 Immediate: API-First Initiative (This Week)

**First task for RoboCo itself:**

- [ ] **Auditor: Conduct backdoor audit** — Review all frontend → backend communication, flag every direct DB access, produce report
- [ ] **Product Owner: Define Codepanion API contract** — What endpoints does it need? Minimal surface area.
- [ ] **Product Owner: Prioritize cleanup work** — Which backdoors are blockers vs. nice-to-have?
- [ ] **Head of Marketing: Draft Codepanion positioning** — README content, value prop

### 14.2 Short-term (Next 2-4 Weeks)

- [ ] **Fix critical backdoors** — Route through API properly
- [ ] **Implement Codepanion endpoints** — `POST /sessions`, `POST /sessions/:id/messages`, `GET /sessions`
- [ ] **Ship Codepanion v0.1** — Publish to PyPI (standalone mode first)
- [ ] **Landing page** — Simple, one-page site
- [ ] **Twitter announcement** — Thread about the tool
- [ ] **HN post** — "Show HN: Codepanion – AI code reviewer CLI"

### 14.3 Medium-term (1-3 Months)

- [ ] **Add more Codepanion commands** — `docs`, `think`, `qa`
- [ ] **Implement connected mode** — Codepanion → RoboCo API for history/sync
- [ ] **Git integration in RoboCo** — Agents can commit, push, create PRs
- [ ] **Collect feedback** — Iterate based on early users
- [ ] **Set up Partita IVA** — When ready to charge
- [ ] **Stripe integration** — Payments infrastructure

### 14.4 Longer-term (3-6 Months)

- [ ] **VS Code extension** — Codepanion in the editor
- [ ] **GitHub App** — Auto-review PRs
- [ ] **Team features** — Shared RoboCo workspaces
- [ ] **Evaluate platform play** — Is there demand for the API directly?
- [ ] **Evaluate company structure** — Estonia vs staying with Partita IVA

### 14.5 Decision Point

**60 days from Codepanion launch:**
- If 100+ users → double down, add features, push toward Pro tier
- If <100 users → investigate why, pivot approach, or reconsider market

---

## Appendix A: Key Files Reference

### Backend (RoboCo)

| File | Purpose |
|------|---------|
| `roboco/agents/base.py` | Base agent class with lifecycle, LLM, MCP |
| `roboco/runtime/orchestrator.py` | Docker-based agent orchestrator |
| `roboco/models/base.py` | All enums and base model |
| `roboco/models/task.py` | Task model with full lifecycle |
| `roboco/db/tables.py` | SQLAlchemy table definitions |
| `roboco/mcp/task_server.py` | Task MCP tools |
| `roboco/api/routes/tasks.py` | Task API endpoints |
| `roboco/api/routes/orchestrator.py` | Orchestrator API endpoints |

### Frontend (RoboCo Panel)

| File | Purpose |
|------|---------|
| `src/types/index.ts` | TypeScript types matching backend |
| `src/app/(dashboard)/layout.tsx` | Dashboard layout |
| `src/components/kanban/` | Kanban board components |
| `src/store/` | Zustand state stores |

---

## Appendix B: Environment Variables

### RoboCo Backend

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `ROBOCO_HOST` | API host | `0.0.0.0` |
| `ROBOCO_PORT` | API port | `8000` |
| `ROBOCO_HOST_CLAUDE_DIR` | Host path to `.claude` | `~/.claude` |
| `ROBOCO_HOST_PROJECT_DIR` | Host path to project root | Required in Docker |
| `ROBOCO_HOST_DATA_DIR` | Host path to data directory | Required in Docker |

### Codepanion

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | Required |
| `CODEPANION_MODEL` | Model to use | `claude-sonnet-4-20250514` |

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| **Agent** | An AI entity with a specific role (dev, QA, PM, etc.) |
| **Blueprint** | System prompt that defines an agent's behavior |
| **Cell** | A team unit (backend, frontend, ux_ui) |
| **Channel** | Communication space for a group of agents |
| **Dispatcher** | Orchestrator component that checks for and assigns work |
| **Handoff** | Documentation package from dev to documenter |
| **Journal** | Agent's private reflection space |
| **MCP** | Model Context Protocol — tool access system |
| **Orchestrator** | Central controller that spawns/manages agents |
| **Session** | Scoped discussion within a channel group |
| **TOON** | Token-Oriented Object Notation — efficient data format |

---

## Appendix D: CEO Directive - API-First Architecture

**From:** CEO
**To:** Board (Product Owner, Head of Marketing, Auditor)
**Priority:** High
**Date:** 2024-12-23

---

### Strategic Context

We're positioning RoboCo for a two-phase growth strategy:

**Phase B (Now):** Two products, one backend. RoboCo Panel (full experience) and Codepanion (lightweight CLI, open source). Both consume the same API.

**Phase C (Future):** Platform play. The orchestration API becomes the product. Third parties build on us.

For this to work, **the API must be the only way in.** No backdoors. No direct DB access from frontends. No "just this once" shortcuts.

If we can't use our own API to build our own products, no one else can either.

---

### Objective

**Establish API-first architecture across the entire system.**

This means:
1. Every action the Panel takes goes through the public API
2. Every action Codepanion will take goes through the public API
3. Internal services communicate through well-defined interfaces
4. The API is documented, consistent, and pleasant to use

---

### Success Criteria

- [ ] Zero direct database access from Panel frontend
- [ ] Zero direct core imports that bypass API in frontend code
- [ ] Codepanion can connect and perform basic operations (create session, send message, retrieve history)
- [ ] API documentation covers all endpoints Codepanion needs
- [ ] Auditor has verified no backdoors remain in critical paths

---

### Board Responsibilities

#### Product Owner

1. **Define the API contract** for Codepanion integration
   - What endpoints does Codepanion need?
   - What's the minimal surface area?
   - What can wait for v2?

2. **Prioritize the cleanup work**
   - Which backdoors are blockers vs. nice-to-have?
   - What's the MVP for "API-first"?

3. **Approve the architecture decisions**
   - Review proposals from Main PM
   - Sign off on API design

#### Head of Marketing

1. **Prepare positioning for Codepanion**
   - Open source CLI angle
   - "Works standalone, better connected" messaging
   - Developer-first tone

2. **Draft initial README/landing content**
   - What does Codepanion do?
   - Why would a dev use it?
   - How does it connect to RoboCo?

3. **Identify launch channels**
   - Where do we announce?
   - What's the content calendar?

#### Auditor

1. **Conduct the backdoor audit**
   - Review all frontend → backend communication
   - Flag every direct DB access
   - Flag every import that bypasses API layer
   - Produce audit report with file, line, severity

2. **Verify fixes**
   - After cleanup, re-audit critical paths
   - Confirm API-first compliance

3. **Establish ongoing monitoring**
   - How do we prevent new backdoors?
   - What checks should be part of code review?

---

### Deliverables to Main PM

Once board alignment is complete, hand off to Main PM:

1. **Audit Report** (from Auditor)
   - List of all backdoors with severity ratings

2. **API Specification** (from Product Owner)
   - Endpoints needed for Codepanion MVP
   - Request/response schemas
   - Authentication approach

3. **Prioritized Task List** (from Product Owner)
   - Ordered by: blockers first, then high-value, then nice-to-have

4. **Marketing Brief** (from Head of Marketing)
   - Positioning document for Codepanion
   - README draft
   - Launch plan outline

---

### Constraints

- **Timeline:** Codepanion MVP should be shippable within 2 weeks of Main PM receiving handoff
- **Scope:** Fix what's necessary for Codepanion. Don't boil the ocean.
- **Principle:** If in doubt, expose it through API. We'd rather have a slightly larger API surface than hidden backdoors.

---

### Notes on Documentation

For now:
- Task documentation lives in the task's session (tied via session-task link)
- Technical documentation (API docs, architecture) should be markdown in the repo
- User-facing documentation (README, guides) prepared by Documenter roles, reviewed by PM

Future consideration: dedicated documentation system. But not now.

---

### Notes on Git Integration

Git integration is coming. For this initiative:
- All code changes go through PRs
- PRs link to task IDs in commit messages
- Once git integration lands, this becomes automated

For now, manual discipline.

---

### Communication

- Board discussions in `management` channel
- Main PM coordination in `cross-cell` channel
- Cell work in respective cell channels (`backend-cell`, `frontend-cell`, `uxui-cell`)
- Each task gets its own session within the appropriate channel
- Journals capture decisions, learnings, blockers

---

### Final Word

This is the foundation. If we get API-first right, everything else becomes easier — Codepanion, platform expansion, third-party integrations, even our own Panel development.

If we get it wrong, we're building on sand.

Make it solid.

---

**CEO**

---

*End of Summary*
