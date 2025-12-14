# RoboCo Usage Guide

Operating the AI company after deployment.

## The Organization

18 AI agents organized as a company:

```
CEO (You)
└── Board
    ├── Product Owner
    ├── Head of Marketing
    └── Auditor
        └── Main PM
            ├── Backend Cell (PM, 2 Devs, QA, Documenter)
            ├── Frontend Cell (PM, 2 Devs, QA, Documenter)
            └── UX/UI Cell (PM, Dev, QA, Documenter)
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
| `ux-dev` | Developer | UX/UI |
| `ux-qa` | QA | UX/UI |
| `ux-doc` | Documenter | UX/UI |
| `product-owner` | Product Owner | Board |
| `head-marketing` | Head of Marketing | Board |
| `auditor` | Auditor | Board |

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
  ux-pm ux-dev ux-qa ux-doc \
  product-owner head-marketing auditor
```

## Monitoring Agents

### Check Status

```bash
# Via API
curl http://localhost:8000/api/v1/orchestrator/status | jq

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

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement user authentication",
    "description": "Add JWT-based auth to the API",
    "team": "backend",
    "complexity": "medium",
    "acceptance_criteria": [
      "Users can register",
      "Users can login",
      "Protected routes require valid JWT"
    ]
  }'
```

## Task Lifecycle

```
pending → claimed → in_progress → verifying → awaiting_qa → awaiting_docs → completed
                         ↓
                    blocked/paused
```

Agents automatically:
1. Scan for pending tasks (`roboco_task_scan`)
2. Claim tasks they can work on
3. Follow the workflow: UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES
4. Submit for QA when done
5. Move to next task

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /docs` | Swagger UI |
| `GET /api/v1/orchestrator/status` | Agent states |
| `GET /api/v1/tasks` | List tasks |
| `POST /api/v1/tasks` | Create task |
| `GET /api/v1/tasks/{id}` | Task details |

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
# Create task
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix login bug", "team": "backend", "complexity": "trivial"}'

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

Don't spawn all 18 agents at once. Start with:
1. `main-pm` alone - verify spawning works
2. Add `be-dev-1` - verify task claiming
3. Add `be-qa` - verify full workflow

### Check Agent Health

```bash
# Quick status
curl -s http://localhost:8000/api/v1/orchestrator/status | jq '.agents'

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

Each agent container uses ~500MB-2GB RAM depending on context. With 128GB RAM:
- 3 agents: ~6GB
- 6 agents: ~12GB
- 18 agents: ~36GB

Monitor with:
```bash
docker stats --filter "name=roboco-agent"
```
