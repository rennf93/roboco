# RoboCo

AI Agents Company - A virtual organization of 25 AI agents + 1 human CEO, designed to operate as a complete software development workforce.

<table align="center">
<tr>
<td width="50%" align="center">
  <a href="https://www.youtube.com/watch?v=t1QNqJgBmkM">
    <img src="https://img.youtube.com/vi/t1QNqJgBmkM/maxresdefault.jpg" alt="Watch the 26-minute RoboCo intro on YouTube — what it is, a walkthrough, and how to use it" width="100%">
  </a>
  <br>
  <sub>▶ <b><a href="https://www.youtube.com/watch?v=t1QNqJgBmkM">Watch the 26-min intro</a></b><br>what it is, a walkthrough, and how to use it</sub>
</td>
<td width="50%" align="center">
  <a href="https://www.youtube.com/watch?v=xige_EUIjIA">
    <img src="https://img.youtube.com/vi/xige_EUIjIA/maxresdefault.jpg" alt="Watch the 2.5-hour Working with RoboCo build session on YouTube — taking a conversation all the way to a shipped feature" width="100%">
  </a>
  <br>
  <sub>▶ <b><a href="https://www.youtube.com/watch?v=xige_EUIjIA">Watch the 2.5-hour build session</a></b><br>a conversation → a shipped feature</sub>
</td>
</tr>
</table>

<p align="center">
  <img src="docs/videos/panel-teaser.gif" alt="Twelve-second looping preview of the RoboCo control panel — the org tree, a task in progress, and an approval queue." width="80%">
  <br>
  <sub><a href="docs/videos/panel-full-walkthrough.mp4">Watch the full 2:33 walkthrough (.mp4) →</a></sub>
</p>

> [!WARNING]
> **RoboCo is early-stage, work-in-progress software (v0).** It's under active development, runs in a homelab, and *will* have rough edges, breaking changes, and bugs. It is **not production-ready** and the API/database schema are not stable yet. Treat it as a working prototype to explore and build on — please  don't expose it to the public internet as-is. Issues and PRs very welcome.

## Overview

RoboCo implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls. The system enables a single human (CEO) to orchestrate complex multi-project development at scale.

```
CEO (You, the human)
    │
    ├── Intake (on-demand interviewer: chats only with you to draft a task)
    ├── Secretary (on-demand chief-of-staff: reads company state, runs gated directives)
    ├── PR Reviewer (read-only main reviewer: inbound external/fork + internal PRs, and the root→master in-path gate)
    │
    └── Board (3 agents)
         ├── Product Owner
         ├── Head of Marketing
         └── Auditor (silent observer, reports to you)
              │
              └── Main PM (coordinates all cells)
                   │
                   ├── Backend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   ├── Frontend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   └── UX/UI Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
```

The 25 agents = Intake + Secretary + PR Reviewer + the Board (3) + Main PM + the three 6-agent cells (18). Agents run on Anthropic Claude by default, or on xAI Grok (the official `grok` CLI on a SuperGrok subscription) — see the provider note under Configuration.

## How it works

You hand a task to the company; it runs through a real *build → review → document → merge* pipeline and comes back to you to approve.

One full loop, put simply:

1. **You give the Board a task — they review it.** The Product Owner and Head of Marketing turn your ask into requirements and acceptance criteria.
2. **You approve — the Main PM starts the work.** A notification asks for your *Approve & Start* decision; approve, and the Main PM breaks it into per-cell subtasks.
3. **Each cell's PM delegates, supports, and triages** its developers (UX/UI, Frontend, Backend).
4. **Developers build it, QA verifies and gates it, Documenters keep the books.**
5. **Cell PMs merge their PRs into the Main PM's branch.**
6. **The Main PM opens the final PR and notifies you "It's done!"** — you approve and merge, or send it back for rework. *(Only you ever merge to `master`.)*

**— Full circle —**

**[See the full walkthrough, with screenshots →](docs/how-to/README.md)**

**[Or watch the full panel walkthrough (video) →](docs/videos/panel-full-walkthrough.mp4)**

## Project Structure

```
roboco/
├── roboco/                      # Main Python package
│   ├── api/                     # FastAPI routes & schemas
│   │   ├── routes/              # API endpoints (tasks, git, agents, etc.)
│   │   └── schemas/             # Pydantic request/response models
│   ├── services/                # Business logic services
│   │   ├── task.py              # Task lifecycle management
│   │   ├── workspace.py         # Multi-agent workspace management
│   │   ├── messaging.py         # Agent communication
│   │   └── optimal_brain/       # RAG/Knowledge base (in-house pgvector)
│   ├── models/                  # Pydantic domain models
│   ├── db/                      # SQLAlchemy ORM & migrations
│   ├── enforcement/             # Task lifecycle state machine
│   ├── runtime/                 # Orchestrator for agent spawning
│   ├── agents/                  # Agent base classes
│   ├── mcp/                     # MCP server implementations
│   └── config.py                # Application configuration
├── agents/
│   └── prompts/                 # Agent system prompts (roles, teams, identities)
├── docs/
│   ├── how-to/                 # Visual walkthrough — 5-chapter guide (start at README.md)
│   └── rag/                     # Agent knowledge base (indexed into RAG)
├── alembic/                     # Database migrations
├── CLAUDE.md                    # Claude Code guidance
├── docker-compose.yml           # Full stack, built from source
└── docker-compose.registry.yml  # Full stack, pulled from the image registry
```

## Running RoboCo

You need **Docker** + **Docker Compose** and a Claude Code auth directory on the host (`~/.claude`, mounted into the orchestrator so agents can reach the model). Copy `.env.example` to `.env` and set at least `ROBOCO_ENCRYPTION_KEY` and `ROBOCO_AGENT_AUTH_SECRET` (that file shows how to generate each). However you start it, the whole company is reachable at one origin: **http://localhost:3000**.

**Optional — run agents on xAI Grok instead of Claude.** RoboCo can spawn agents on xAI's official `grok` CLI authenticated by a SuperGrok subscription (no metered API key). Run `grok login` once on the host and point `ROBOCO_HOST_GROK_DIR` at the resulting `~/.grok` so it mounts into Grok agents; the orchestrator keeps the ~6h token refreshed for you. See the Grok block in `.env.example` (`ROBOCO_HOST_GROK_DIR`, `ROBOCO_GROK_AGENT_IMAGE`, `ROBOCO_GROK_CLI_MODEL`, `ROBOCO_GROK_REASONING_EFFORT`).

### Option 1 — Run the pre-built images (quickest)

Every release publishes all RoboCo images to both the GitHub Container Registry and Docker Hub, so you can run the full stack without building anything. Use the registry compose:

```bash
git clone https://github.com/rennf93/roboco.git && cd roboco
cp .env.example .env                                   # then edit in your secrets
docker compose -f docker-compose.registry.yml pull
docker compose -f docker-compose.registry.yml up -d
```

Choose the registry and version with two env vars (defaults shown):

```bash
ROBOCO_REGISTRY=ghcr.io/rennf93   # or docker.io/renzof93
ROBOCO_VERSION=latest             # or a pinned release, e.g. 0.7.0
```

The orchestrator spawns the matching pre-built agent images on demand — no build toolchain or source compile on your host.

### Option 2 — Build from source

The same full stack, built locally from the Dockerfiles instead of pulled:

```bash
git clone https://github.com/rennf93/roboco.git && cd roboco
cp .env.example .env              # then edit in your secrets
docker compose up -d              # builds images on first run, then starts everything
```

### Option 3 — Local development (no full stack)

For hacking on the code itself, run only the backing services in Docker and the API on your host:

```bash
uv sync
docker compose up -d postgres redis ollama   # backing services only
uv run alembic upgrade head                   # migrate the database
uv run python -m roboco.cli                   # API + orchestrator

# Or just the API without the orchestrator:
uv run uvicorn roboco.api.app:app --reload --host 0.0.0.0 --port 8000
```

## Configuration

Key environment variables (see `roboco/config.py` for all options):

```bash
# API Server
ROBOCO_HOST=0.0.0.0
ROBOCO_PORT=8000

# Database
ROBOCO_DATABASE_HOST=localhost
ROBOCO_DATABASE_PORT=5432
ROBOCO_DATABASE_NAME=roboco

# Workspaces (Multi-Agent Git)
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true

# RAG/LLM
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1
ROBOCO_LOCAL_LLM_MODEL=glm-5:cloud
```

## Multi-Agent Workspace Structure

Each agent gets their own git clone for parallel development:

```
{ROBOCO_WORKSPACES_ROOT}/
└── {project-slug}/
    └── {team}/
        └── {agent-slug}/
            └── [git repository]

Example:
/data/workspaces/roboco/backend/be-dev-1/
/data/workspaces/roboco/backend/be-dev-2/
```

## Task Lifecycle

```
backlog → pending → claimed → in_progress → verifying → awaiting_qa
    ↓                              ↓              ↓           ↓
cancelled                      blocked      needs_revision   awaiting_documentation
                               paused                              ↓
                                                           awaiting_pm_review
                                                                   ↓
                                                           awaiting_ceo_approval
                                                                   ↓
                                                              completed
```

Assembled, PR-bearing tasks pass through one extra stage — the in-path PR-review gate — before the PM merges:

```
in_progress → awaiting_pr_review → awaiting_pm_review
   (submit_up /      (pr_pass)
    submit_root)     (pr_fail → needs_revision)
```

The cell PM's `submit_up` (cell→root PR) and the Main PM's `submit_root` (root→master PR) open the assembled PR and enter the gate; a PR reviewer `pr_pass`es it on to the PM merge or `pr_fail`s it back. Leaf dev tasks (reviewed by QA) and branchless coordination roots skip the gate.

## API Endpoints

Domain routes are mounted under `/api`:

| Route Group | Description |
|-------------|-------------|
| `/api/tasks` | Task CRUD, lifecycle, claiming |
| `/api/agents` | Agent management |
| `/api/git` | Git operations (status, commit, push, PR) |
| `/api/sessions` | Communication sessions |
| `/api/messages` | Agent messages |
| `/api/projects` | Project (repo) management |
| `/api/work-sessions` | Git work session tracking |
| `/api/optimal` | RAG/Knowledge base queries |
| `/api/journals` | Agent journals/reflections |
| `/api/orchestrator/status` | Orchestrator / dispatcher status |

The agent **gateway** verbs are served separately under `/api/v1/flow/{role}/{verb}` (intent verbs) and `/api/v1/do` (content tools) — see the [Agent Gateway](CLAUDE.md#agent-gateway).

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Format and lint
uv run ruff format .
uv run ruff check .
uv run mypy roboco/

# Type checking
uv run mypy roboco/
```

## Core Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **The Auditor sees all** - Quality monitored silently
7. **CEO approves major changes** - Human-in-the-loop for critical decisions

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL + SQLAlchemy (async) |
| Vector Store | PostgreSQL + pgvector (in-house engine) |
| Cache/Queue | Redis |
| RAG Engine | in-house (asyncpg + pgvector, hybrid retrieval) |
| Embeddings | qwen3-embedding:0.6b (Ollama) |
| Local LLM | Ollama (glm-5:cloud) |
| Cloud LLM | Claude API (Anthropic) + xAI Grok (official `grok` CLI, SuperGrok subscription) |
| Package Manager | uv |

## Status

**Core Infrastructure** (Complete)
- [x] Data models (Pydantic)
- [x] Database ORM (SQLAlchemy async)
- [x] Task lifecycle state machine
- [x] Multi-agent workspace management
- [x] Agent prompts (25 agents)
- [x] Messaging API
- [x] Task API with full lifecycle
- [x] Git operations API
- [x] RAG/Knowledge base (in-house pgvector engine)
- [x] Agent orchestrator
- [x] CEO approval workflow
- [x] Pluggable agent providers (Claude Code + xAI Grok on the official `grok` CLI)
- [x] Inbound PR review (read-only PR-reviewer + CEO supersede/dismiss queue)
- [x] Self-healing CI loop for RoboCo's own repo (default-off, CEO-gated)
- [x] Business Goals tab with a live Company Scorecard (delivery, spend-vs-budget, lead time)

**In Progress**
- [x] Frontend panel (vendored under `panel/`, served through nginx on :3000)
- [ ] Full agent autonomy testing

## Security

> [!IMPORTANT]
> **Do not expose RoboCo to the public internet as-is.** It is designed to run on a trusted private network (homelab / LAN).

**Agent authentication.** Requests identify the caller with `X-Agent-Id` / `X-Agent-Role` headers. The orchestrator issues each spawned agent an HMAC token (`X-Agent-Token`, signed with `ROBOCO_AGENT_AUTH_SECRET`) that binds its id, role and team. Token enforcement is gated by `ROBOCO_AGENT_AUTH_REQUIRED`:

- **`ROBOCO_AGENT_AUTH_REQUIRED` unset/false (default):** *header-trust mode* — the role headers are accepted without a token, so any client that can reach the API may claim any role (including `ceo`). The API logs a warning at startup in this mode. Acceptable only on a trusted network.
- **`ROBOCO_AGENT_AUTH_REQUIRED=true`:** every request must carry a valid token; an agent cannot spoof another agent's role. The control panel keeps working because **nginx** — the only trusted hop between the browser and the API — injects the CEO token (`X-Agent-Token`) on `/api` and `/ws`, so the browser never holds the signing secret. Generate that token with `make panel-token` and set it as `ROBOCO_PANEL_AGENT_TOKEN` in `.env` before enabling secure mode.

**WebSocket streams.** Token enforcement is currently REST-only. The `/ws/*` endpoints authenticate by `agent_id` query param at most and do not yet validate `X-Agent-Token`, even in secure mode — nginx injects the token so the panel works, but a direct WebSocket connection that bypasses nginx is not rejected. In particular the operator stream `/ws/system` (rate-limit lifecycle + token-usage snapshots for the dashboard) is unauthenticated. These streams are read-only — no control surface, secrets, or task content — but treat the orchestrator port as trusted-network-only until WebSocket auth lands.

**Secrets** (the Fernet `ROBOCO_ENCRYPTION_KEY`, GitHub PATs) live encrypted in the database and in gitignored env files — never in the repo. Per-project git tokens are Fernet-encrypted at rest and never returned by the API.

## License

Copyright (c) 2026 Renzo Franceschini

RoboCo is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0). See [`LICENSE`](./LICENSE) for the full text.

The AGPL's network-use clause (section 13) means that if you run a modified version of RoboCo as a network service, you must make your modified source available to its users. This keeps the project open while preventing closed, hosted re-distributions.

## Contributing

Contributions are welcome. All contributors must sign the Contributor License Agreement ([`CLA.md`](./CLA.md)) — this is automated on your first pull request. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the workflow and why the CLA exists.
