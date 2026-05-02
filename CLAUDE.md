# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## IGNORING THESE WILL FORCE A COMPLETE SHUTDOWN OF CLAUDE CODE

**A GOOD FUNCTION NAME IS SELF-EXPLANATORY. IF THE FUNCTION ONLY TAKES CARE OF A SINGLE THINGS, THEN EVEN BETTER BECAUSE THE NAME IS GOING TO BE THE ONLY DOCUMENTATION YOU NEED.**
**DOING THINGS WRONGLY COST TWICE. DO THINGS RIGHT AT FIRST, AND YOU DON'T PAY THE PRICE**
**ASK QUESTIONS BEFORE YOU DO ANYTHING**
**YOU ARE NOT ALLOWED TO MAKE ASSUMPTIONS**
**IGNORING != FIXING**
**`# noqa` & `# type: ignore` != FIXING**
**PRE-EXISTING ERRORS ARE STILL EXISTING ERRORS. I DON'T CARE IF THEY ARE PRE-EXISTING, THEY SHOULDN'T EXIST**
**`uv run mypy ... --ignore-missing-imports` | ANY IGNORING AT ALL != GOOD PRACTICES**
**`http://192.168.50.111:8000/docs` IS THE API DOCS**
** You need `X-Agent-Id` and `X-Agent-Role` headers to be set as 'ceo' for all API calls **
**`ssh renzof@renzof-nas.local` SSH TO THE SERVER**

## Project Overview

**RoboCo** is an AI Agentic Company - a virtual organization of 18 AI agents + 1 human CEO, designed to operate as a complete software development workforce. The system implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls.

### Core Architecture

```
CEO (Renzo - Human)
    |
    +-- Board (3 agents)
         +-- Product Owner
         +-- Head of Marketing
         +-- Auditor (silent observer, reports to CEO)
              |
              +-- Main PM (coordinates all cells)
                   |
                   +-- Backend Cell (5 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter)
                   +-- Frontend Cell (5 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter)
                   +-- UX/UI Cell (4 agents: 1 Dev, 1 QA, 1 PM, 1 Documenter)
```

### Hardware Infrastructure

- **Olares One (Powerhouse)**: Intel Ultra 9 + RTX 5090, runs Claude Code instances and AI inference - NOT YET ARRIVED
- **UGREEN NAS (Warehouse)**: 36TB RAID6, 128GB RAM, hosts PostgreSQL, Redis
- **Pi Cluster (Operations)**: Monitoring, notifications, smart home

## Development Standards

### Python (Backend)
```bash
# Package manager
uv

# Before any commit
uv run ruff format .
uv run ruff check .
uv run mypy roboco/
uv run pytest

# Coverage target: 80%
```

### TypeScript (Frontend)
```bash
# Package manager
pnpm

# Before any commit
pnpm format
pnpm lint
pnpm typecheck
pnpm test

# Coverage target: 80%
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL + asyncpg |
| Vector Store | PostgreSQL + pgvector (via piragi) |
| RAG Engine | piragi (HyDE, hybrid search, BM25) |
| Cache/Queue | Redis |
| Container Runtime | Docker + Docker Compose |
| Cloud LLM | Claude API (claude-opus-4-6) |
| Local LLM | Ollama (glm-5:cloud for HyDE/RAG) |
| Embeddings | qwen3-embedding:0.6b (1024 dim) |
| Frontend | Next.js 16 + TypeScript + Tailwind + Radix UI (in `panel/`) |
| Edge / Proxy | nginx (single entry point on port 3000) |

## Multi-Agent Workspace Structure

Each agent gets their own git clone of a project, enabling parallel development without conflicts:

```
{ROBOCO_WORKSPACES_ROOT}/          # Default: /data/workspaces
+-- {project-slug}/
    +-- {team}/
        +-- {agent-slug}/
            +-- [git repository]
```

**Example:**
```
/data/workspaces/
+-- roboco/
    +-- backend/
    |   +-- be-dev-1/     # be-dev-1's workspace
    |   +-- be-dev-2/     # be-dev-2's workspace
    +-- frontend/
        +-- fe-dev-1/
        +-- fe-dev-2/
```

Note: the Next.js control panel now lives at `roboco/panel/` inside this
repo (no longer a separate `roboco-panel` project or workspace).

**Key Configuration (roboco/config.py):**
- `ROBOCO_WORKSPACES_ROOT`: Root directory for workspaces (default: `/data/workspaces`)
- `ROBOCO_WORKSPACE_AUTO_CLONE`: Auto-clone repos on first access (default: `true`)
- `ROBOCO_WORKSPACE_CLONE_TIMEOUT`: Clone timeout in seconds (default: `300`)

## Git Workflow

### Branch Naming Convention

Branch names follow the pattern: `{type}/{team}/{task-hierarchy}`

**Types:** `feature`, `bug`, `chore`, `docs`, `hotfix`

**Task Hierarchy:** Uses `--` separator (not `/`) to avoid git ref conflicts.

**Examples:**
- Root task: `feature/backend/ABC12345`
- Subtask: `feature/backend/ABC12345--DEF67890`
- Sub-subtask: `feature/backend/ABC12345--DEF67890--GHI11111`

### Commit Format

Commits are automatically prefixed with the task ID:

```
[{task-id[:8]}] {message}
```

**Example:**
```
[ABC12345] Add user authentication endpoint
```

### Work Sessions

When a developer claims a task, a **WorkSession** is created that tracks:
- Branch name and base/target branches
- All commits made during the session
- Files modified
- PR number/URL when created
- Merge status and who merged

### Git Credentials

Git authentication is managed **per-project** through encrypted GitHub PATs:

- **Each project stores its own git token** - no global fallback
- **Tokens are encrypted at rest** using Fernet symmetric encryption
- **API never exposes tokens** - only returns `has_git_token: boolean`
- **Self-service via UI** - users set/update tokens in project settings

**Project fields:**
| Field | Description |
|-------|-------------|
| `git_token_encrypted` | Fernet-encrypted GitHub PAT (DB column) |
| `has_git_token` | Boolean indicator for API responses |

**Token flow:**
1. User creates project in UI, enters GitHub PAT
2. Token encrypted and stored in `projects.git_token_encrypted`
3. WorkspaceService decrypts token when cloning repos
4. GitService decrypts token for PR operations (gh CLI)

**HTTPS URLs require tokens** - attempting to clone without a token will raise `WorkspaceError`.

## Task Lifecycle

### Task States

The complete task lifecycle is defined in `roboco/enforcement/task_lifecycle.py`:

```
backlog -> pending -> claimed -> in_progress -> [blocked|paused] -> verifying
                                     |                                  |
                                     v                                  v
                                 awaiting_qa <------------------+   awaiting_documentation
                                     |         (needs_revision) |           |
                                     v                          |           v
                                 awaiting_documentation --------+   awaiting_pm_review
                                     |                                      |
                                     v                                      v
                                 awaiting_pm_review             awaiting_ceo_approval
                                     |                                      |
                                     v                                      v
                                 completed                              completed
```

**States:**
| State | Description |
|-------|-------------|
| `backlog` | PM setup phase - dependencies or session setup needed |
| `pending` | Ready for work - orchestrator can spawn agents |
| `claimed` | Agent has locked the task |
| `in_progress` | Active development |
| `blocked` | External dependency blocking progress |
| `paused` | Temporarily stopped (can resume) |
| `verifying` | Self-verification by developer |
| `needs_revision` | QA or CEO requested changes |
| `awaiting_qa` | Submitted for QA review — PR must already exist |
| `awaiting_documentation` | Documentation phase — PR already open from pre-QA; doc writes docs |
| `awaiting_pm_review` | Docs complete, PM reviews + merges |
| `awaiting_ceo_approval` | Major tasks escalated for CEO final approval |
| `completed` | Terminal state - work done and merged |
| `cancelled` | Terminal state - work cancelled |
| `quarantined` | Special state for problematic tasks (can return to pending) |

### Role-Based Transitions

All status transitions are validated through the enforcement layer. Key restrictions:

| Transition | Allowed Roles |
|------------|---------------|
| `backlog` → `pending` (activate) | PM roles only |
| `pending` → `claimed` (claim) | Role must match task type (QA for awaiting_qa, etc.) |
| `claimed` → `pending` (unclaim) | Assignee or PM |
| `awaiting_qa` → `awaiting_documentation` (pass) | QA only |
| `awaiting_qa` → `needs_revision` (fail) | QA only |
| `awaiting_documentation` → `awaiting_pm_review` | Documenter or Developer (parallel completion) |
| `awaiting_pm_review` → `completed` | PM roles only |
| `awaiting_pm_review` → `awaiting_ceo_approval` | PM roles only |
| `awaiting_ceo_approval` → `completed/needs_revision/cancelled` | CEO only |
| Any → `cancelled` | PM roles only |

**Unclaim Operation**: Agents can release claimed tasks back to the pool using `unclaim()`. This transitions `claimed` → `pending` and optionally reassigns to another agent.

### Git Integration Requirements

All tasks follow git workflow. PR is created BEFORE QA review (not after)
so QA can review the real PR diff on GitHub and downstream PM/CEO approval
chain off a PR that already exists:

1. **claimed -> in_progress**: `branch_name` is auto-set on claim (hierarchical branches)
2. **verifying -> awaiting_qa** (submit-qa): Requires `self_verified`, `commits`,
   `pr_number` (PR open), and at least one `progress_updates` entry
3. **awaiting_qa -> awaiting_documentation** (pass-qa): Requires `pr_number` and
   substantive QA notes
4. **awaiting_documentation -> awaiting_pm_review**: Requires `docs_complete=True`
   (PR already exists from step 2 above)
5. **awaiting_pm_review -> awaiting_ceo_approval**: Must have `pr_number` set
   and all subtasks in a terminal state

### CEO Approval Workflow

Major tasks are escalated to CEO for final approval:
1. PM reviews and approves, escalates to `awaiting_ceo_approval`
2. CEO can:
   - **Approve**: Merges PR, task -> `completed`
   - **Request changes**: Task -> `needs_revision`
   - **Cancel**: Task -> `cancelled`

## Data Models

### Core Models (roboco/models/)

| Model | Purpose |
|-------|---------|
| `Task` | Atomic unit of work with acceptance criteria |
| `Project` | Git repository configuration and CI/CD commands |
| `WorkSession` | Links agent work to task, tracks branch/commits/PR |
| `Agent` | AI agent with role, team, capabilities |
| `Session` | Communication session with messages |
| `Channel` | Team communication channel |
| `Message` | Extracted message from agent streams |
| `Notification` | Formal notification requiring acknowledgment |
| `Journal` | Agent personal log for reflections/learnings |

### Task Model Key Fields

```python
# Git configuration (all tasks follow git workflow)
task_type: TaskType      # code, documentation, research, planning, design, administrative
project_id: UUID         # Project this task works on (required)
branch_name: str         # Branch for this task (auto-created on claim)
work_session_id: UUID    # Active work session

# PR tracking (parallel execution in awaiting_documentation)
pr_number: int           # GitHub/GitLab PR number
pr_url: str              # Full URL to PR
docs_complete: bool      # Documenter has finished
pr_created: bool         # Developer has created PR

# Commits linked to task
commits: list[CommitRef] # All commits made for this task
```

## Communication Model

**Communication** = constant stream (always flowing, logged, observed)
**Notifications** = formal signals (require acknowledgment, sent by PMs/Board only)

### Channel Structure
- Cell channels: `#backend-cell`, `#frontend-cell`, `#uxui-cell`
- Cross-cell: `#dev-all`, `#qa-all`, `#pm-all`, `#doc-all`
- Management: `#main-pm-board`, `#board-private`
- Special: `#announcements` (read-only except Board/Main PM), `#all-hands`

The Auditor has silent read access to ALL channels.

## Key Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **State is sacred** - If interrupted, state must be recoverable
7. **The Auditor sees all** - Quality monitored silently
8. **Commits linked to tasks** - Every commit references its task ID
9. **CEO approves major changes** - Escalation path for important work

## Agent Gateway

Agents do not call the API or per-domain MCP tools directly. They go through
two thin MCP servers (`roboco-flow`, `roboco-do`) backed by the server-side
**Choreographer** in `roboco/services/gateway/`. The Choreographer composes
the existing services (TaskService, JournalService, GitService, etc.) into
intent-verb sequences. Tracing, claim-locking, evidence assembly, and
remediation hints are all centralized there.

Each agent gets a **spawn manifest** at `/app/tool-manifest.json` listing
the verbs its role is allowed to call. The orchestrator builds the
manifest from `roboco/services/gateway/role_config.py` and mounts it
read-only into the agent container.

### Verb surface (all roles get `i_am_idle`; the rest are role-scoped)

| Role          | Flow verbs                                                                                       |
|---------------|--------------------------------------------------------------------------------------------------|
| developer     | `give_me_work`, `i_will_work_on`, `i_have_committed`, `i_am_done`, `i_am_blocked`                |
| qa            | `claim_review`, `pass`, `fail`                                                                   |
| documenter    | `claim_doc_task`, `i_documented`                                                                 |
| cell_pm       | `triage`, `unblock`, `complete`, `escalate_up`                                                   |
| main_pm       | `triage_all`, `unblock`, `complete`, `escalate_up`, `escalate_to_ceo`                            |
| product_owner | `triage`, `escalate_to_ceo`                                                                      |
| head_marketing| `triage`, `escalate_to_ceo`                                                                      |
| auditor       | `triage` (read-only — no `say`/`dm`)                                                             |

Content tools (do_server) — most roles: `commit`, `note`, `say`, `dm`, `evidence`.
Auditor is restricted to `note` (scope=reflect) + `evidence`.

### MCP servers running per agent container

| Server               | Purpose                                                              |
|----------------------|----------------------------------------------------------------------|
| `roboco-flow`        | Intent verbs (give_me_work, i_am_done, claim_review, complete, ...) |
| `roboco-do`          | Content tools (commit, note, say, dm, evidence)                      |
| `roboco-git-readonly`| Read-only git: status, log, diff, branches                           |
| `roboco-optimal`     | RAG: `roboco_ask_mentor`, `roboco_kb_search`                         |
| `roboco-docs`        | Project docs file management (selected roles)                        |

Every verb returns a standardized **Envelope**:
- ok: `{status, task_id, next, evidence?, context_briefing}`
- error: `{error, message, remediate, missing}`

The `next` field tells the agent what to call next; the `remediate` field
on errors tells them exactly how to fix and retry. Agents should not guess
state — trust the response.

## Services

Core services in `roboco/services/`:

| Service | Purpose |
|---------|---------|
| `TaskService` | Task CRUD and state transitions |
| `WorkSessionService` | Git session management, PR lifecycle |
| `WorkspaceService` | Multi-agent workspace resolution and cloning |
| `ProjectService` | Project/repository management |
| `MessagingService` | Channels, sessions, messages |
| `NotificationService` | Formal notifications |
| `JournalService` | Agent journals and entries |
| `OptimalService` | RAG queries using piragi |
| `PermissionsService` | Role-based access control |

## Configuration

Key settings in `roboco/config.py` (env prefix: `ROBOCO_`):

```bash
# Database
ROBOCO_DATABASE_HOST=localhost
ROBOCO_DATABASE_PORT=5432
ROBOCO_DATABASE_USER=roboco
ROBOCO_DATABASE_PASSWORD=roboco
ROBOCO_DATABASE_NAME=roboco

# Redis
ROBOCO_REDIS_HOST=localhost
ROBOCO_REDIS_PORT=6379

# Security (REQUIRED)
# Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
ROBOCO_ENCRYPTION_KEY=<your-fernet-key>

# Workspaces
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true
ROBOCO_WORKSPACE_CLONE_TIMEOUT=300

# RAG (piragi + pgvector)
ROBOCO_RAG_CHUNK_STRATEGY=fixed
ROBOCO_RAG_CHUNK_SIZE=512
ROBOCO_RAG_USE_HYDE=true
ROBOCO_RAG_USE_HYBRID_SEARCH=true

# AI/LLM
ROBOCO_DEFAULT_EMBEDDING_MODEL=qwen3-embedding:0.6b
ROBOCO_LOCAL_LLM_MODEL=glm-5:cloud
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1
ROBOCO_OLLAMA_BASE_URL=http://roboco-ollama:11434
```

## Docker Deployment

### Container Architecture

The system runs as Docker Compose services. All Dockerfiles live under
`docker/` at the project root; every service uses `context: .` plus
`dockerfile: docker/<name>.Dockerfile`.

| Service | Purpose | Healthcheck |
|---------|---------|-------------|
| `postgres` | PostgreSQL + pgvector | `pg_isready` |
| `redis` | Cache, sessions, event bus | `redis-cli ping` |
| `ollama` | Local LLM + embeddings | `ollama list` |
| `ollama-init` | Pulls models on startup | One-shot |
| `agent-base-image` / `agent-*-image` | Pre-built images spawned per agent | One-shot |
| `orchestrator` | API + agent spawner | Depends on all above |
| `panel` | Next.js control panel (internal, port 3000) | — |
| `nginx` | Reverse proxy fronting panel + orchestrator | — |

### Single Entry Point

`nginx` is the only externally-exposed service. It listens on `localhost:3000` and routes:

- `/api/*` and `/ws/*` → `orchestrator:8000`
- everything else → `panel:3000`

This avoids CORS since the browser sees one origin. The Next.js code uses
relative URLs (`/api`, `/ws`) and lets nginx do the dispatch.

### Startup Sequence

The startup order is critical due to dependencies:

```
postgres ──┐
redis ─────┼──> ollama ──> ollama-init ──> orchestrator ──> panel ──> nginx
           │        │            │
           │        │            └── Pulls qwen3-embedding:0.6b, glm-5:cloud
           │        └── Healthcheck: ollama list
           └── Healthcheck: pg_isready, redis-cli ping
```

**Important timing notes:**
1. `ollama-init` pulls models (~30s for embedding model, ~2min for LLM)
2. Orchestrator waits for models before starting
3. FastAPI lifespan indexes documents using Ollama (~30-60s)
4. Orchestrator polls `/health` until API is ready before starting dispatcher
5. After orchestrator is up, `panel` (Next.js) builds/starts, then `nginx`

### Database migrations

Schema changes ship as Alembic migrations under `alembic/versions/`. Run:

```bash
docker compose exec orchestrator alembic upgrade head
```

after pulling any change that adds a new migration.

### Ollama Configuration

Ollama provides two APIs:
- `/v1/*` - OpenAI-compatible API (for LLM chat/completion)
- `/api/*` - Native Ollama API (for embeddings, model management)

The embedder uses `/api/embed` endpoint with the `qwen3-embedding:0.6b` model.

**Environment variables for Docker:**
```bash
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1    # OpenAI-compat
ROBOCO_OLLAMA_BASE_URL=http://roboco-ollama:11434          # Native API
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `404 /api/embed` | Model not pulled | Check `docker logs roboco-ollama-init` |
| `All connection attempts failed` | API not ready | Orchestrator starts before FastAPI lifespan completes |
| Healthcheck failing | Wrong endpoint | Use `ollama list` not `curl` |

## Blueprint Reference

The complete system design is documented in `HOMELAB_TEAM_V0.md`, which contains:
- Organizational structure and role descriptions
- Communication matrix and notification permissions
- API endpoint specifications
- Security and access control model
- Configuration templates
