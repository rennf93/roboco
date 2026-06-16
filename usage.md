# RoboCo Usage Guide

Operating the AI company after deployment.

## The Organization

22 AI agents organized as a company:

```
CEO (You)
├── Intake (on-demand interviewer: chats only with you to draft a task)
└── Board
    ├── Product Owner
    ├── Head of Marketing
    └── Auditor
        └── Main PM
            ├── Backend Cell (PM, 2 Devs, QA, Documenter)
            ├── Frontend Cell (PM, 2 Devs, QA, Documenter)
            └── UX/UI Cell (PM, 2 Devs, QA, Documenter)
```

## Agent IDs

| ID | Role | Team |
|----|------|------|
| `main-pm` | Main PM | Management |
| `be-pm` | Cell PM | Backend |
| `be-dev-1`, `be-dev-2` | Developers | Backend |
| `be-qa` | QA | Backend |
| `be-doc` | Documenter | Backend |
| `fe-pm` | Cell PM | Frontend |
| `fe-dev-1`, `fe-dev-2` | Developers | Frontend |
| `fe-qa` | QA | Frontend |
| `fe-doc` | Documenter | Frontend |
| `ux-pm` | Cell PM | UX/UI |
| `ux-dev-1`, `ux-dev-2` | Developers | UX/UI |
| `ux-qa` | QA | UX/UI |
| `ux-doc` | Documenter | UX/UI |
| `product-owner` | Product Owner | Board |
| `head-marketing` | Head of Marketing | Board |
| `auditor` | Auditor | Board |
| `intake-1` | Intake (interviewer) | Board |

## Spawning Agents

```bash
# Start with minimal team
uv run python -m roboco.cli --spawn main-pm be-dev-1 be-qa

# Add more agents
uv run python -m roboco.cli --spawn main-pm be-pm be-dev-1 be-dev-2 be-qa

# Full organization
uv run python -m roboco.cli --spawn \
  main-pm \
  be-pm be-dev-1 be-dev-2 be-qa be-doc \
  fe-pm fe-dev-1 fe-dev-2 fe-qa fe-doc \
  ux-pm ux-dev-1 ux-dev-2 ux-qa ux-doc \
  product-owner head-marketing auditor
```

## Monitoring Agents

### Check Status

```bash
# Via API
curl http://localhost:8000/api/orchestrator/status | jq

# Via Docker
docker ps --filter "name=roboco-agent"
```

### View Agent Logs

```bash
# Follow specific agent's output
docker logs -f roboco-agent-be-dev-1

# All agent containers
docker ps --filter "name=roboco-agent" --format "{{.Names}}"
```

### Container Management

```bash
# Stop one agent
docker stop roboco-agent-be-dev-1

# Restart an agent
docker restart roboco-agent-be-dev-1

# Stop all agents
docker ps --filter "name=roboco-agent" -q | xargs docker stop
```

## Creating Tasks

`POST /api/tasks` has no silent defaults — `title`, `description` (min 20
chars), `acceptance_criteria` (at least one), `team`, `task_type`, `nature`,
and `estimated_complexity` are all required, plus exactly one of `project_id`
(the repo this task targets) or `product_id` (a cell→project map for a fan-out
task). See the `TaskCreate` schema in `roboco/models/task.py` (or the Swagger
UI at `/docs`) for the full field list and enum values.

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement user authentication",
    "description": "Add JWT-based auth to the API endpoints",
    "team": "backend",
    "task_type": "code",
    "nature": "technical",
    "estimated_complexity": "medium",
    "project_id": "<project-uuid>",
    "acceptance_criteria": [
      "Users can register",
      "Users can login",
      "Protected routes require valid JWT"
    ]
  }'
```

Enum values: `task_type` ∈ {`code`, `documentation`, `research`, `planning`,
`design`, `administrative`}; `nature` ∈ {`technical`, `non_technical`};
`estimated_complexity` ∈ {`low`, `medium`, `high`}.

## Task Lifecycle

```
pending → claimed → in_progress → verifying → awaiting_qa → awaiting_documentation → completed
                         ↓
                    blocked/paused
```

Agents automatically:
1. Pull pending work via the gateway verb `give_me_work()`
2. Claim it with `i_will_work_on(task_id)` (auto-creates the feature branch)
3. Follow the workflow: UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES
4. Open a PR and submit for QA when done (`open_pr` / `i_am_done`)
5. Move to next task

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /docs` | Swagger UI |
| `GET /api/orchestrator/status` | Agent states |
| `GET /api/tasks` | List tasks |
| `POST /api/tasks` | Create task |
| `GET /api/tasks/{id}` | Task details |

## Viewing the API

Open http://localhost:8000/docs in your browser for the Swagger UI.

## Common Workflows

### Start a Development Session

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Run migrations (if needed)
uv run alembic upgrade head

# 3. Start with a small team
uv run python -m roboco.cli --spawn main-pm be-dev-1 be-qa
```

### Create and Monitor a Task

```bash
# Create task (all fields below are required — see POST /api/tasks schema)
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Fix login bug",
    "description": "Login fails on expired-token refresh path",
    "team": "backend",
    "task_type": "code",
    "nature": "technical",
    "estimated_complexity": "low",
    "project_id": "<project-uuid>",
    "acceptance_criteria": ["Expired token refreshes without 500"]
  }'

# Watch agent pick it up
docker logs -f roboco-agent-be-dev-1
```

### Shutdown

```bash
# Stop orchestrator (Ctrl+C in terminal)

# Stop agent containers
docker ps --filter "name=roboco-agent" -q | xargs docker stop

# Stop infrastructure
docker compose down
```

## Tips

### Start Small

Don't spawn the whole fleet at once. Start with:
1. `main-pm` alone - verify spawning works
2. Add `be-dev-1` - verify task claiming
3. Add `be-qa` - verify full workflow

### Check Agent Health

```bash
# Quick status
curl -s http://localhost:8000/api/orchestrator/status | jq '.agents'

# Detailed container info
docker inspect roboco-agent-be-dev-1
```

### Debug an Agent

```bash
# View full logs
docker logs roboco-agent-be-dev-1

# Attach to container (read-only)
docker logs -f roboco-agent-be-dev-1
```

### Resource Usage

RAM is modest. Agent containers are spawned on demand and torn down when
their work is done, so you rarely have more than a handful live at once — and
the on-demand Intake and Secretary only run while you're interacting with them.
Steady-state memory is dominated by the standing services (Postgres, Redis,
and especially Ollama with its models loaded), not by the agents.

Storage is the larger footprint: the built (or pulled) image set. The agent
images all share a common base layer, so on disk they cost far less than their
nominal sizes added together. `docker system prune` reclaims old image versions
and stopped agent containers.

Monitor with:
```bash
docker stats        # live RAM / CPU per running container
docker system df    # image / container / build-cache disk usage
```
