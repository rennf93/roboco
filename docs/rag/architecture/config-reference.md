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
| `ROBOCO_RAG_USE_HYBRID_SEARCH` | `true` | BM25 + vector search |
| `ROBOCO_RAG_USE_CROSS_ENCODER` | `true` | Neural reranking |
| `ROBOCO_RAG_AUTO_UPDATE_ENABLED` | `true` | Auto-update indexes |
| `ROBOCO_RAG_AUTO_UPDATE_INTERVAL` | `300` | Update interval (seconds) |

## LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_LOCAL_LLM_MODEL` | `glm-5.2:cloud` | Local LLM for RAG |
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

## Feature flags

Env-gated subsystems, default-off except the overload break. Each takes effect on the next backend restart; the panel's Settings → Feature Flags card toggles them without hand-editing env.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_CONVENTIONS_ENABLED` | `false` | Architectural Conventions Standard: auto-scaffold `.roboco/conventions.yml`, inject the architecture map, attach baseline constraints, and block `i_am_done` / `pr_pass` on block-level placement and hygiene violations. Off = fully inert. |
| `ROBOCO_TOOLCHAIN_MATCH_ENABLED` | `false` | Provision each agent workspace with the target project's Python (resolved from its `requires-python` / `.python-version`) and block delivery gates when the suite cannot be executed under it. Off = today's behavior. |
| `ROBOCO_OVERLOAD_BREAK_ENABLED` | `true` | Park a provider on a persistent model-API overload (HTTP 529 / 500 / 503) the same way a 429 is parked — queue its spawns and probe until it recovers — instead of crash-retrying into the overload. Off = crash-retry behavior. |

The company-in-a-box subsystems toggle the same way and are all default-off: web research (`ROBOCO_RESEARCH_ENABLED`), the strategy engine (`ROBOCO_STRATEGY_ENGINE_ENABLED`), and pitch provisioning (`ROBOCO_PROVISIONING_ENABLED`).

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

## Autonomous maintenance

The fan-out generalizations of self-heal — they watch any opted-in project, not just RoboCo's own. All default-off; neither ever auto-merges (every task rides the normal delivery + PR-review gate). Per-project opt-in lives on the project row (set in the panel's edit-project dialog), not in env.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_CI_WATCH_ENABLED` | `false` | Master switch for multi-repo CI-watch; off = the loop never runs. Per-project opt-in via `projects.ci_watch_enabled` |
| `ROBOCO_CI_WATCH_DEFAULT_WORKFLOW` | `ci.yml` | Workflow file the CI signal is scoped to when a project sets no `ci_watch_workflow` |
| `ROBOCO_CI_WATCH_INTERVAL_SECONDS` | `1800` | Seconds between CI-watch passes |
| `ROBOCO_CI_WATCH_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open ci_watch tasks |
| `ROBOCO_CI_WATCH_MAX_PER_CYCLE` | `1` | Max ci_watch fix tasks originated per cycle |
| `ROBOCO_DEP_UPDATE_ENABLED` | `false` | Master switch for the dependency-update bot; off = the loop never runs. Per-project opt-in via `projects.dep_update_command` |
| `ROBOCO_DEP_UPDATE_INTERVAL_SECONDS` | `604800` | Seconds between dependency-update passes (default weekly) |
| `ROBOCO_DEP_UPDATE_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open dep_update tasks |
| `ROBOCO_DEP_UPDATE_MAX_PER_CYCLE` | `1` | Max dep_update tasks originated per cycle |
| `ROBOCO_RELEASE_MANAGER_ENABLED` | `false` | Master switch for the gated release manager; off = the loop never runs. Even on it only PROPOSES — the CEO approves before any publish |
| `ROBOCO_RELEASE_MIN_COMMITS` | `8` | Minimum unreleased commits since the last tag before a release is proposed (a feat/security change also qualifies) |
| `ROBOCO_RELEASE_MANAGER_INTERVAL_SECONDS` | `3600` | Seconds between release-readiness assessment passes |
| `ROBOCO_ORG_MEMORY_ENABLED` | `false` | Master switch for the org-memory loop (distill at completion, index journals, auto-inject lessons/playbooks); off = legacy capture, no inject |
| `ROBOCO_ORG_MEMORY_TOP_K` | `3` | Max institutional-memory items injected into a briefing on claim |
| `ROBOCO_ORG_MEMORY_MIN_SCORE` | `0.6` | Cosine-similarity floor for injected memory; below it nothing is injected |
| `ROBOCO_IMAGE_PRUNE_ENABLED` | `true` | Background sweep prunes dangling (`<none>`) Docker images from agent-image rebuilds (only dangling; ~6h throttle). Always-on safety net, not a feature flag |
| `ROBOCO_IMAGE_PRUNE_INTERVAL_SECONDS` | `21600` | Minimum seconds between dangling-image prune passes |

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_ENCRYPTION_KEY` | (required) | Fernet key that encrypts secrets at rest (e.g. per-project git tokens). Generate with `Fernet.generate_key()`. |
| `ROBOCO_AGENT_AUTH_SECRET` | (required) | HMAC secret the orchestrator signs each agent's `X-Agent-Token` with. |
| `ROBOCO_AGENT_AUTH_REQUIRED` | `false` | When `true`, every request must carry a valid agent token (secure mode). |
| `ROBOCO_PANEL_AGENT_TOKEN` | (unset) | The control panel's CEO token, injected by nginx in secure mode. Get it with `make panel-token`. |
