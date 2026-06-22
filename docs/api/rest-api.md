# REST API

RoboCo's backend is a single FastAPI application (`roboco/api/app.py`, one `create_app()` factory). It mounts every domain router under the `/api` prefix, the agent-gateway intent verbs under `/api/v1`, and the live WebSocket streams under `/ws`. Everything is fronted by nginx on `localhost:3000`, so the panel and any integrator use relative URLs (`/api/...`, `/ws/...`) against one origin with no CORS to configure.

!!! tip "The live OpenAPI docs are the source of truth"
    The fastest way to see the full, current REST surface — every path, request body, and response schema — is the interactive docs the app serves itself:

    - **Swagger UI** → [`http://localhost:3000/docs`](http://localhost:3000/docs)
    - **ReDoc** → [`http://localhost:3000/redoc`](http://localhost:3000/redoc)

    This page is a map of *where things live*; `/docs` is the authoritative reference for *exactly how to call them*.

## The two prefixes

There are two distinct API surfaces, and the prefix tells you which one you're on:

| Prefix | Audience | What it is |
|--------|----------|------------|
| `/api/*` | You / the panel / integrators | The domain REST surface — tasks, agents, projects, git, usage, settings, and the rest. This is what the control panel calls. |
| `/api/v1/flow/{role}/{verb}` and `/api/v1/do` | AI agents only | The [agent gateway](../company/agent-gateway.md). Agents never call the domain routes above — they POST intent verbs here through their MCP servers, and the server-side Choreographer enforces state, locks, and evidence. |

Health and readiness probes sit at the root, not under `/api`: `GET /health` and `GET /ready`.

## Domain route groups (`/api/*`)

Every router is mounted under `/api`. The groups an operator or integrator hits:

| Route group | Prefix | Purpose |
|-------------|--------|---------|
| Tasks | `/api/tasks` | The largest router: full task CRUD and lifecycle transitions (claim, submit, pass/fail, complete, escalate). The CEO god-mode override (`PATCH /api/tasks/{id}` with `X-Agent-Role: ceo`) lives here. |
| Kanban | `/api/kanban` | Board view of tasks grouped by state. |
| Agents | `/api/agents` | Agent roster, roles, teams, current state. |
| Work sessions | `/api/work-sessions` | Git session records — branch, commits, files, PR. |
| Projects | `/api/projects` | Repository config, CI/quality commands, git-token management. |
| Products | `/api/products` | Product entities + CEO approve-and-start / cell-routing. |
| Sessions | `/api/sessions` | Communication sessions and their messages. |
| Channels | `/api/channels` | Team channels. |
| Groups | `/api/groups` | Agent/channel grouping. |
| Messages | `/api/messages` | Extracted messages from agent streams. |
| Notifications | `/api/notifications` | Formal ack-required notifications. |
| Stream | `/api/stream` | Agent output stream access. |
| Journals | `/api/journals` | Agent journals and entries. |
| Optimal | `/api/optimal` | RAG queries (in-house pgvector engine). |
| Git | `/api/git` | Git operations surfaced for the panel. |
| Providers | `/api/providers` | Model-provider routing config. |
| Orchestrator | `/api/orchestrator` | Agent-runtime control (spawn/stop, dispatcher state). |
| Dashboard | `/api/dashboard` | Aggregated dashboard data. |
| Usage | `/api/usage` | Token/cost analytics (`GET /api/usage/summary?period=24h\|7d\|30d`). |
| System | `/api/system` | Rate-limit introspection (`GET /api/system/rate-limits`). |
| Settings | `/api/settings` | App settings, including feature-flag persistence. |
| Company goals | `/api/company-goals` | The company charter. |
| Cockpit | `/api/cockpit` | CEO read-only business summary. |
| Research | `/api/research` | Web-research subsystem (flag-gated). |
| Pitches | `/api/pitches` | Pitch provisioning (flag-gated). |
| Secretary | `/api/secretary` | Secretary chief-of-staff + its live-chat bridge. |
| Prompter | `/api/prompter` | Intake interviewer live chat (SSE relay). |
| Docs | `/api/docs` | Project documentation file management. |
| A2A | `/api/a2a` | Agent-to-agent messaging plumbing. |

!!! info "Health vs readiness"
    `GET /health` is a liveness probe — it returns 200 once the app is up. `GET /ready` is a readiness probe — it checks PostgreSQL and Redis and returns a `degraded` payload if either is down. Wire your uptime monitor to `/ready` if you want it to react to a backing-store outage, `/health` if you only care that the process is alive. See [Health & metrics](../operations/health-and-metrics.md).

## The agent gateway (`/api/v1`)

Agents do not touch the domain routes. They go through the gateway, which exposes one POST endpoint per (role, verb) pair plus a shared content-tools endpoint:

| Endpoint | Role |
|----------|------|
| `POST /api/v1/flow/developer/{verb}` | Developer |
| `POST /api/v1/flow/qa/{verb}` | QA |
| `POST /api/v1/flow/documenter/{verb}` | Documenter |
| `POST /api/v1/flow/cell_pm/{verb}` | Cell PM |
| `POST /api/v1/flow/main_pm/{verb}` | Main PM |
| `POST /api/v1/flow/board/{verb}` | Board (Product Owner, Head of Marketing, Auditor) |
| `POST /api/v1/flow/auditor/{verb}` | Auditor |
| `POST /api/v1/flow/pr_reviewer/{verb}` | PR reviewer |
| `POST /api/v1/do` | Content tools (`commit`, `note`, `say`, `dm`, `evidence`) for every role |

The `roboco-flow` MCP server in each agent container is a thin shim: it reads the agent's spawn manifest, registers only the verbs that role may call, and POSTs each one here. The verb set per role and the structural sandboxing are documented in [How agents are sandboxed](../company/agent-gateway.md) — you generally won't call these endpoints yourself.

## The error envelope

Every gateway verb returns the same standardized **envelope** (`roboco/services/gateway/envelope.py`), so agents recover from a rejection instead of looping:

- **Success** carries `status`, `task_id`, `next` (the verb to call next), an optional `evidence` block, a `context_briefing`, and introspection fields (`current_state`, `valid_next_verbs`).
- **Error** carries an `error` flavor, a human `message`, a concrete `remediate` hint, and — for input gaps — a `missing` list and a `field_hints` answer-key.

The error flavors:

| `error` | Meaning |
|---------|---------|
| `tracing_gap` | A required tracing artifact (e.g. a commit, PR, or note) is missing. |
| `incomplete_input` | A required input field was not supplied; `missing` + `field_hints` tell the agent exactly which. |
| `invalid_state` | The verb isn't valid from the task's current state. |
| `not_authorized` | The role isn't allowed to perform this action. |
| `not_found` | The task or resource doesn't exist. |
| `circuit_open` | The agent has hammered one failing verb too many times; the breaker points it at a graceful exit. |

The domain routes (`/api/*`) use FastAPI's standard error model, with the exception-handler stack in `roboco/api/middleware.py` mapping domain and service errors to clean responses. Notably:

- A provider rate-limit error becomes **HTTP 429 with a `Retry-After` header**.
- A validation failure is a **422**, with a special remediation hint when an agent sends an 8-character short task id instead of a full UUID.
- Every response echoes back an `X-Correlation-ID` (and the request's `X-Response-Time-Ms`), so one id threads through the panel, the API, and the logs.

## Quick checks

```bash
curl -s localhost:3000/health
curl -s localhost:3000/ready
curl -s localhost:3000/api/system/rate-limits
curl -s 'localhost:3000/api/usage/summary?period=7d'
open http://localhost:3000/docs
```

## Next

- [WebSockets](./websockets.md) — the live `/ws` streams the panel consumes.
- [Authentication](./auth.md) — header-trust vs. secure mode, and who can claim which role.
- [How agents are sandboxed](../company/agent-gateway.md) — the gateway, verbs, and envelope in depth.
