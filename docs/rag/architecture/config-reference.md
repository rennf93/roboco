# Configuration Reference

Environment variables for RoboCo (prefix: `ROBOCO_`).

## API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_HOST` | `127.0.0.1` | API host (0.0.0.0 for containers) |
| `ROBOCO_PORT` | `8000` | API port |
| `ROBOCO_DEBUG` | `false` | Debug mode |
| `ROBOCO_ENVIRONMENT` | `development` | development/staging/production |

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_DATABASE_HOST` | `localhost` | PostgreSQL host |
| `ROBOCO_DATABASE_PORT` | `5432` | PostgreSQL port |
| `ROBOCO_DATABASE_USER` | `roboco` | Database user |
| `ROBOCO_DATABASE_PASSWORD` | `roboco` | Database password |
| `ROBOCO_DATABASE_NAME` | `roboco` | Database name |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_REDIS_HOST` | `localhost` | Redis host |
| `ROBOCO_REDIS_PORT` | `6379` | Redis port |
| `ROBOCO_REDIS_DB` | `0` | Redis database |

## Workspaces

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_WORKSPACES_ROOT` | `/data/workspaces` | Root for agent workspaces |
| `ROBOCO_WORKSPACE_AUTO_CLONE` | `true` | Auto-clone on first access |
| `ROBOCO_WORKSPACE_CLONE_TIMEOUT` | `300` | Clone timeout (seconds) |

## RAG/Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_DEFAULT_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model |
| `ROBOCO_EMBEDDING_DIMENSIONS` | `1024` | Embedding dimensions |
| `ROBOCO_RAG_CHUNK_STRATEGY` | `fixed` | fixed/semantic/hierarchical/contextual |
| `ROBOCO_RAG_CHUNK_SIZE` | `512` | Base chunk size |
| `ROBOCO_RAG_CHUNK_SIZE_DOCS` | `1536` | Chunk size for docs |
| `ROBOCO_RAG_CHUNK_SIZE_JOURNALS` | `1024` | Chunk size for journals |
| `ROBOCO_RAG_CHUNK_OVERLAP` | `128` | Chunk overlap |
| `ROBOCO_RAG_USE_HYDE` | `true` | Use HyDE for queries |
| `ROBOCO_RAG_USE_HYBRID_SEARCH` | `true` | BM25 + vector search |
| `ROBOCO_RAG_USE_CROSS_ENCODER` | `true` | Neural reranking |
| `ROBOCO_RAG_AUTO_UPDATE_ENABLED` | `true` | Auto-update indexes |
| `ROBOCO_RAG_AUTO_UPDATE_INTERVAL` | `300` | Update interval (seconds) |

## LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_LOCAL_LLM_MODEL` | `glm-5:cloud` | Local LLM for RAG |
| `ROBOCO_LOCAL_LLM_BASE_URL` | `http://roboco-ollama:11434/v1` | OpenAI-compat API |
| `ROBOCO_OLLAMA_BASE_URL` | `http://roboco-ollama:11434` | Native Ollama API |

## Grok provider (xAI)

Agents whose provider is `GROK` run xAI's official `grok` CLI. Auth is the host SuperGrok subscription (mounted `~/.grok`), not a metered API key.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_HOST_GROK_DIR` | `~/.grok` | Host dir holding `auth.json`, mounted into Grok agents; the orchestrator auto-refreshes the ~6h token in place. Set up once with `grok login`. |
| `ROBOCO_GROK_CLI_MODEL` | `grok-build` | Grok CLI model id |
| `ROBOCO_GROK_AGENT_IMAGE` | `roboco-agent-grok:latest` | Image for Grok delivery agents |
| `ROBOCO_GROK_REASONING_EFFORT` | (blank) | Per-run reasoning-effort override; blank = the grok CLI default |
| `ROBOCO_GROK_IDLE_KILL_SECONDS` | `900` | Kill + evict a Grok container that has been ACTIVE-yet-idle (no gateway verb) this long |
| `ROBOCO_GROK_MAX_COST_USD` | `0.0` | Per-agent Grok cost ceiling (USD); `0` disables |

## Self-Healing CI loop

RoboCo watching its own repo's CI. All default-off / dormant.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_SELF_HEAL_ENABLED` | `false` | Master switch (detect + notify the CEO); off = the loop never runs and no CI is fetched |
| `ROBOCO_SELF_HEAL_ORIGINATE_ENABLED` | `false` | Second opt-in: also open a PENDING fix task on a regression — CEO-gated, never auto-started/merged/deployed |
| `ROBOCO_SELF_HEAL_PROJECT_SLUG` | (empty) | The registered project that IS RoboCo itself — the only repo it watches and fixes; empty = no-op |
| `ROBOCO_SELF_HEAL_CI_WORKFLOW` | (empty) | GitHub Actions workflow to scope the CI signal to (e.g. `ci.yml`); empty = latest across all workflows |
| `ROBOCO_SELF_HEAL_INTERVAL_SECONDS` | `1800` | Seconds between assessment passes |
| `ROBOCO_SELF_HEAL_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open self-heal tasks |
| `ROBOCO_SELF_HEAL_MAX_PER_CYCLE` | `1` | Max fix tasks originated per cycle |

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_ENCRYPTION_KEY` | (required) | Fernet key that encrypts secrets at rest (e.g. per-project git tokens). Generate with `Fernet.generate_key()`. |
| `ROBOCO_AGENT_AUTH_SECRET` | (required) | HMAC secret the orchestrator signs each agent's `X-Agent-Token` with. |
| `ROBOCO_AGENT_AUTH_REQUIRED` | `false` | When `true`, every request must carry a valid agent token (secure mode). |
| `ROBOCO_PANEL_AGENT_TOKEN` | (unset) | The control panel's CEO token, injected by nginx in secure mode. Get it with `make panel-token`. |
