# Environment reference

This is the canonical list of every `ROBOCO_*` setting. They are all read by a single Pydantic-Settings class (`roboco/config.py`), loaded from the process environment and `.env`, prefixed with `ROBOCO_`, and case-insensitive. Most have a working default; the few that don't, and the ones the orchestrator refuses to start without, are flagged below.

!!! tip "You rarely set most of these"
    For a working deploy you set the two required secrets, the host paths, and maybe a feature flag or two. The long tables here exist so that when you *do* need to tune a timeout or a window, you can find it. The defaults shown are RoboCo's config defaults; a few compose-only defaults differ and are called out.

A feature flag set in `.env` takes effect on the next backend restart. The env-gated subsystems can also be toggled from the panel's **Settings → Feature Flags** card, which persists to the settings store and overrides the env default; an unset toggle falls back to the env/config default. See the [Optional capabilities](../optional/index.md) section for what each subsystem does.

## Required secrets

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_ENCRYPTION_KEY` | *(empty — required)* | Fernet key encrypting every per-project git token at rest. The orchestrator **refuses to start** without it (compose `:?` guard). Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Keep it stable — losing it makes stored tokens undecryptable. |
| `ROBOCO_AGENT_AUTH_SECRET` | *(empty — required for compose)* | HMAC secret signing the per-agent `X-Agent-Token`. Generate with `python -c 'import secrets; print(secrets.token_hex(32))'`. |

## Security & auth

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_AGENT_AUTH_REQUIRED` | `false` | Fail-closed secure mode. When `true`, every API call must carry a valid token. Requires `ROBOCO_PANEL_AGENT_TOKEN` to keep the panel working. On a trusted LAN, leave `false` (header-trust mode). |
| `ROBOCO_PANEL_AGENT_TOKEN` | *(empty)* | The CEO token nginx injects as `X-Agent-Token` on `/api` and `/ws` in secure mode, so the panel works without the browser holding the signing secret. Generate with `make panel-token`. |

## Application & API server

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_APP_VERSION` | `0.9.0` | Reported app version. |
| `ROBOCO_DEBUG` | `false` | Debug mode. |
| `ROBOCO_ENVIRONMENT` | `development` | One of `development` / `staging` / `production`. Selects the JSON log renderer (prod) vs console renderer. The compose stack sets `production`. |
| `ROBOCO_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` in containers. |
| `ROBOCO_PORT` | `8000` | API port. |
| `ROBOCO_API_URL` | *(unset)* | Override base URL for containerized agents (e.g. `http://roboco-orchestrator:8000`); otherwise built from host/port. |
| `ROBOCO_CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | Allowed CORS origins. The single-origin nginx setup means you rarely change this. |
| `ROBOCO_CORS_ALLOW_CREDENTIALS` | `true` | Whether CORS allows credentials. |
| `ROBOCO_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Reachable base URL embedded in commit-trailer links — set to your LAN IP or domain so the links resolve. |

## Database

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_DATABASE_HOST` | `localhost` | Postgres host (`roboco-postgres` in compose). |
| `ROBOCO_DATABASE_PORT` | `5432` | Postgres port. |
| `ROBOCO_DATABASE_USER` | `roboco` | Postgres user. |
| `ROBOCO_DATABASE_PASSWORD` | `roboco` | Postgres password — change it for any real deployment. |
| `ROBOCO_DATABASE_NAME` | `roboco` | Database name. |
| `ROBOCO_DATABASE_ECHO` | `false` | Log every SQL statement. |
| `ROBOCO_DATABASE_POOL_SIZE` | `10` | Connection pool size. |
| `ROBOCO_DATABASE_MAX_OVERFLOW` | `20` | Extra connections beyond the pool. |
| `ROBOCO_DATABASE_POOL_TIMEOUT` | `10` | Seconds to wait for a pooled connection. |
| `ROBOCO_DATABASE_POOL_RECYCLE` | `1800` | Recycle a connection after this many seconds. |

## Redis

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_REDIS_HOST` | `localhost` | Redis host (`roboco-redis` in compose). |
| `ROBOCO_REDIS_PORT` | `6379` | Redis port. |
| `ROBOCO_REDIS_DB` | `0` | Redis logical DB. |
| `ROBOCO_REDIS_PASSWORD` | *(unset)* | Optional Redis password. |

## RAG, embeddings & Ollama

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_RAG_PERSIST_DIR` | `.roboco` | Local RAG persistence dir. |
| `ROBOCO_RAG_CHUNK_STRATEGY` | `fixed` | One of `fixed` / `semantic` / `hierarchical` / `contextual`. `fixed` recommended; `semantic` loads an extra model. |
| `ROBOCO_RAG_CHUNK_SIZE` | `512` | Base chunk size. |
| `ROBOCO_RAG_CHUNK_SIZE_DOCS` | `1536` | Chunk size for docs. |
| `ROBOCO_RAG_CHUNK_SIZE_JOURNALS` | `1024` | Chunk size for journals/reflections. |
| `ROBOCO_RAG_CHUNK_OVERLAP` | `128` | Chunk overlap. |
| `ROBOCO_RAG_AUTO_UPDATE_ENABLED` | `true` | Whether the RAG index auto-refreshes. |
| `ROBOCO_RAG_AUTO_UPDATE_INTERVAL` | `300` | Seconds between auto-updates. |
| `ROBOCO_ANTHROPIC_API_KEY` | *(unset)* | Optional Anthropic key. Agents use the mounted Claude Code auth, not a metered key. |
| `ROBOCO_DEFAULT_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model (1024-dim). |
| `ROBOCO_EMBEDDING_DIMENSIONS` | `1024` | Embedding dimensions — must match the model. |
| `ROBOCO_LOCAL_LLM_MODEL` | `glm-5:cloud` | Local LLM for RAG answer synthesis. |
| `ROBOCO_LOCAL_LLM_BASE_URL` | `http://roboco-ollama:11434/v1` | Ollama OpenAI-compatible endpoint. |
| `ROBOCO_OLLAMA_BASE_URL` | `http://roboco-ollama:11434` | Ollama native endpoint (embeddings, model management). |

## Workspaces & git timeouts

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_WORKSPACES_ROOT` | `/data/workspaces` | Root for all agent git clones. |
| `ROBOCO_WORKSPACE_AUTO_CLONE` | `true` | Auto-clone a repo on first workspace access. |
| `ROBOCO_WORKSPACE_CLONE_TIMEOUT` | `300` | Seconds for a `git clone`. |
| `ROBOCO_WORKSPACE_REFRESH_FETCH_TIMEOUT_SECONDS` | `60` | Timeout for the best-effort `git fetch` on re-entry into a healthy clone. |
| `ROBOCO_WORKSPACE_INSTALL_DEV_DEPS` | `true` | After cloning, install the project's dev dependencies into the workspace so `make quality` runs without re-downloading tooling. |
| `ROBOCO_WORKSPACE_DEP_INSTALL_TIMEOUT_SECONDS` | `600` | Timeout for that post-clone dependency install. |
| `ROBOCO_GIT_COMMAND_TIMEOUT_SECONDS` | `30` | Timeout for a single local git subprocess (status, log, checkout). |
| `ROBOCO_GIT_COMMIT_TIMEOUT_SECONDS` | `180` | Timeout for staging + committing a changeset. |
| `ROBOCO_GIT_NETWORK_TIMEOUT_SECONDS` | `120` | Timeout for git ops that talk to origin (fetch / pull / push). |
| `ROBOCO_PROTECTED_GIT_URLS` | *(empty)* | Repo URL substrings a project may not point at — blocks agent commits/merges from reaching a protected repo. |
| `ROBOCO_SESSION_IDLE_TIMEOUT_SECONDS` | `3600` | Idle seconds before a messaging session is swept closed. |

## Agent images (spawn source)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_AGENT_IMAGE_REGISTRY` | *(empty)* | Registry namespace for pre-built agent images (e.g. `ghcr.io/rennf93`). Empty = build locally. The registry compose wires this to `ROBOCO_REGISTRY`. |
| `ROBOCO_AGENT_IMAGE_TAG` | *(empty)* | Tag for pre-built agent images (e.g. `0.9.0`). Empty = implicit `:latest`. The registry compose wires this to `ROBOCO_VERSION`. |

!!! note "Deploy-time variables (compose, not config.py)"
    A few variables are consumed by the compose files and host-mount wiring rather than by `config.py`: `ROBOCO_REGISTRY`, `ROBOCO_VERSION`, `ROBOCO_DATA_DIR`, `ROBOCO_HOST_PROJECT_DIR`, `ROBOCO_HOST_CLAUDE_DIR` / `CLAUDE_AUTH_DIR`, `ROBOCO_HOST_DATA_DIR`, and `ROBOCO_HOST_GROK_DIR`. They are documented in the [production deploy reference](./deployment.md#required-host-path-mounts).

## Transcript retention

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_TRANSCRIPT_RETENTION_DAYS` | `14` | Days to keep agent Claude Code transcripts. A stored panel setting overrides this default. |
| `ROBOCO_TRANSCRIPT_PRUNE_ENABLED` | `true` | Whether the background sweep prunes old transcripts. |
| `ROBOCO_TRANSCRIPT_PRUNE_INTERVAL_SECONDS` | `3600` | Minimum seconds between prune passes. |

## Spawn pacing, SLAs & reaper windows

The orchestrator's dispatcher uses these to pace spawns, detect loops, and reclaim stuck work. Defaults are tuned for real LLM latency — raise the reaper windows (not lower) if long agent tasks are being reaped mid-work.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_AGENT_TOOL_CALL_WARN` | `50` | Soft warning threshold for per-session tool calls. |
| `ROBOCO_AGENT_TOOL_CALL_HALT` | `150` | Hard cap on per-session tool calls; the orchestrator stops the container. |
| `ROBOCO_AGENT_LOOP_THRESHOLD` | `3` | Identical tool+args repeats in the window that flag a loop. |
| `ROBOCO_AGENT_LOOP_WINDOW` | `10` | How many recent tool calls to inspect for loop detection. |
| `ROBOCO_AGENT_STOP_ATTEMPT_ALLOWANCE` | `1` | Stop-without-terminal attempts before auto-substitute. |
| `ROBOCO_AGENT_SLA_DEVELOPER_IN_PROGRESS` | `7200` | SLA (s) for a developer in `in_progress`. |
| `ROBOCO_AGENT_SLA_DEVELOPER_VERIFYING` | `1800` | SLA (s) for a developer in `verifying`. |
| `ROBOCO_AGENT_SLA_QA_CLAIMED` | `1800` | SLA (s) for QA on a claimed review. |
| `ROBOCO_AGENT_SLA_DOCUMENTER_CLAIMED` | `3600` | SLA (s) for a documenter on a claimed task. |
| `ROBOCO_AGENT_SLA_CELL_PM_CLAIMED` | `14400` | SLA (s) for a cell PM on a claimed task. |
| `ROBOCO_CLAIM_STALE_SECONDS` | `180` | Claim-heartbeat staleness used by the spawn trigger filter. |
| `ROBOCO_STALE_CLAIM_REAP_SECONDS` | `600` | Reaper-only stale-claim threshold before releasing a claim back to pending. |
| `ROBOCO_PM_CLOSURE_RECENTLY_PAUSED_SECONDS` | `45` | Debounce before respawning a PM to close a recently paused parent. |
| `ROBOCO_GROK_IDLE_KILL_SECONDS` | `900` | Idle-container kill threshold for Grok agents (they emit no SDK heartbeat). |
| `ROBOCO_GROK_MAX_COST_USD` | `0.0` | Per-agent Grok cost ceiling (USD) before kill; `0` disables. |
| `ROBOCO_INTERACTIVE_IDLE_REAP_SECONDS` | `1800` | Idle-reap threshold for live intake/secretary chats; `0` disables. |
| `ROBOCO_CLAIMED_NO_AGENT_GRACE_SECONDS` | `120` | Grace window before respawning/releasing a claimed task with no running agent. |
| `ROBOCO_PM_DECISION_WINDOW_SECONDS` | `300` | Recency window for a PM `journal:decision` to satisfy gating verbs. |
| `ROBOCO_SPAWN_COOLDOWN_SECONDS` | `60` | Per-task spawn-rate cooldown. |
| `ROBOCO_ROLE_SPAWN_RATE_PER_MINUTE` | `6` | Per-role spawn-rate limit per minute. |

## Gateway: manifests & tracing-gate minimums

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_MANIFEST_HOST_DIR` | `/app/manifests` | Orchestrator dir where per-agent tool manifests are written; must be a host-bind-mounted path so the daemon can mount each manifest into its agent. |
| `ROBOCO_QA_NOTES_MIN_CHARS` | `80` | Minimum characters for QA notes. |
| `ROBOCO_DOCS_NOTES_MIN_CHARS` | `20` | Minimum characters for docs notes. |
| `ROBOCO_DEV_NOTES_MIN_CHARS` | `40` | Minimum characters for a developer's `dev_notes`. |
| `ROBOCO_PR_REVIEWER_NOTES_MIN_CHARS` | `40` | Minimum characters for a PR reviewer's notes. |
| `ROBOCO_QUICK_CONTEXT_MIN_CHARS` | `30` | Minimum characters for a PM's `quick_context` resumption section. |
| `ROBOCO_COMMIT_SUBJECT_MIN_CHARS` | `20` | Minimum characters for a commit subject. |
| `ROBOCO_COMMIT_BANNED_WORDS` | `wip,tmp,asdf,oops,fix,update,change,stuff,things` | Banned single-word commit subjects. |

## Grok runtime

Only relevant if you run any agent on Grok. See the [models section](../models/grok.md) for the full runtime.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_HOST_GROK_DIR` | `/home/renzof/.grok` (compose) | Host `~/.grok` SuperGrok auth dir; mounted read-write into the orchestrator (token auto-refresh) and read-only into Grok agents. The same value is both the source and target path. |
| `ROBOCO_GROK_AGENT_IMAGE` | `roboco-agent-grok:latest` | Image the orchestrator spawns for Grok agents. |
| `ROBOCO_GROK_CLI_MODEL` | `grok-build` | Grok CLI model id. |
| `ROBOCO_GROK_REASONING_EFFORT` | *(empty)* | `low`/`medium`/`high`/`xhigh`/`max` for all Grok agents; empty keeps the model default. |
| `ROBOCO_GROK_MAX_TURNS` | `200` | Hard ceiling on agentic turns per Grok run (loop guard). |
| `ROBOCO_GROK_IDLE_KILL_SECONDS` | `900` | (see reaper table) Idle-kill window for a wedged Grok container. |
| `ROBOCO_GROK_MAX_COST_USD` | `0.0` | (see reaper table) Per-agent Grok cost ceiling. |

## Optional subsystem flags (default-off unless noted)

These gate the env-toggled capabilities. Each is inert when off. See [Optional capabilities](../optional/index.md).

### Web research — default **on**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_RESEARCH_ENABLED` | `true` | Master switch for web research. When `false`, the search MCP is not mounted into any agent. |
| `ROBOCO_RESEARCH_PROVIDER` | `tavily` | `tavily` / `brave` / `exa` / `null`. |
| `ROBOCO_RESEARCH_API_KEY` | *(unset)* | Provider key — **server-side only**, never reaches an agent. Unset = empty-result null provider. |
| `ROBOCO_RESEARCH_MAX_RESULTS` | `5` | Cap on results per search (1–20). |
| `ROBOCO_RESEARCH_FETCH_MAX_CHARS` | `20000` | Cap on extracted characters per fetch. |
| `ROBOCO_RESEARCH_TIMEOUT_SECONDS` | `15.0` | Per-request outbound timeout. |
| `ROBOCO_RESEARCH_DAILY_QUOTA_PER_AGENT` | `50` | Search+fetch calls per agent per UTC day. |

### GitHub repo provisioning — default **on** (inert without token/org)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_PROVISIONING_ENABLED` | `true` | Master switch for pitch auto-provisioning. Inert with no token/org regardless. |
| `ROBOCO_PROVISIONING_TOKEN` | *(empty)* | GitHub PAT (repo + org admin) used to create repos — server-side only. |
| `ROBOCO_PROVISIONING_ORG` | *(empty)* | GitHub org where new repos are created. |
| `ROBOCO_GITHUB_API_BASE_URL` | `https://api.github.com` | Override for GitHub Enterprise. |
| `ROBOCO_PROVISIONING_TIMEOUT_SECONDS` | `30.0` | Per-request provisioning timeout. |
| `ROBOCO_PROVISIONING_REPO_PRIVATE` | `true` | Whether provisioned repos are private. |

### Architectural conventions — **off** (config) / **on** (compose)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_CONVENTIONS_ENABLED` | `false` (config) / `true` (compose) | Master switch for the per-project conventions standard (scaffold, ambient injection, baseline constraints, gate enforcement). The compose orchestrator block defaults this **on** (left off in `docker-compose.registry.yml`); fully inert when off. |

### Toolchain matching — default **off**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_TOOLCHAIN_MATCH_ENABLED` | `false` (config) / `true` (compose) | Provision the agent workspace with the target project's Python and block delivery gates when the suite can't run. The compose orchestrator block defaults this **on**. |

### Provider overload break — default **on**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_OVERLOAD_BREAK_ENABLED` | `true` | Park a provider on a persistent overload (HTTP 529/500/503) the way a 429 is parked, instead of crash-retrying. |
| `ROBOCO_GATEWAY_HEALTH_ENABLED` | `true` | Probe a stale-heartbeat-but-live agent's gateway and kill + respawn it when the gateway is broken (a corrupted `/app` venv firing no verb), instead of the reaper protecting it forever. Off => spare live containers on verb-heartbeat liveness alone. |
| `ROBOCO_GATEWAY_HEALTH_GRACE_SECONDS` | `180` | How long an agent gateway may probe as broken before recovery — tolerates a transient probe miss. |
| `ROBOCO_IMAGE_PRUNE_ENABLED` | `true` | Background sweep prunes dangling (`<none>`) Docker images left by agent-image rebuilds, throttled ~6h. Only dangling images are removed — a tagged image or one backing a running container is never touched. Not a feature flag; disable to manage image cleanup yourself. |

### Strategy engine — default **off**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_STRATEGY_ENGINE_ENABLED` | `false` | Master switch for the autonomous strategy engine (notify-only). When off the loop never runs. |
| `ROBOCO_STRATEGY_ENGINE_INTERVAL_SECONDS` | `1800` | Seconds between assessment passes. |
| `ROBOCO_STRATEGY_STRANDED_BLOCKED_MINUTES` | `120` | A task blocked longer than this is surfaced as stranded. |

### External / internal PR review — default **off**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_EXTERNAL_PR_ENABLED` | `false` (config) / `true` (compose) | Master switch for inbound external/fork PR review. The compose orchestrator block defaults this on. |
| `ROBOCO_EXTERNAL_PR_POLL_INTERVAL_SECONDS` | `300` | Seconds between inbound external-PR discovery passes. |
| `ROBOCO_EXTERNAL_PR_AUTHOR_ALLOWLIST` | *(empty)* | GitHub usernames auto-trusted. Empty = every external PR needs human confirmation. |
| `ROBOCO_EXTERNAL_PR_REQUIRE_HUMAN_CONFIRM` | `true` | Require explicit human confirmation before any agent fetches/checks-out/executes external code. |
| `ROBOCO_INTERNAL_PR_ENABLED` | `false` | Also review org-repo (non-fork) PRs not tied to an active task. |

### Self-healing CI loop — default **off**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_SELF_HEAL_ENABLED` | `false` | Master switch for the self-heal loop (detect + notify the CEO). When off the loop never runs. |
| `ROBOCO_SELF_HEAL_PROJECT_SLUG` | *(empty)* / `roboco-api` (compose) | The registered project that *is* RoboCo itself — the only repo the loop watches/originates into. |
| `ROBOCO_SELF_HEAL_CI_WORKFLOW` | `ci.yml` | GitHub Actions workflow file to scope the CI signal to. |
| `ROBOCO_SELF_HEAL_ORIGINATE_ENABLED` | `false` | Second opt-in: on a regression, also open a fix task and dispatch it to the Main PM automatically (no manual start). The loop never merges or deploys — the fix ships through the normal gates (QA, PR review, your merge). |
| `ROBOCO_SELF_HEAL_INTERVAL_SECONDS` | `1800` | Seconds between telemetry passes. |
| `ROBOCO_SELF_HEAL_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open self-heal tasks. |
| `ROBOCO_SELF_HEAL_MAX_PER_CYCLE` | `1` | Max self-heal tasks originated in one cycle. |

### Multi-repo CI-watch — default **off**

The global switch arms the engine; each project opts in via `ci_watch_enabled` (+ optional `ci_watch_workflow`) in the edit-project dialog.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_CI_WATCH_ENABLED` | `false` | Master switch for watching opted-in projects' CI. When off the engine never runs and no CI telemetry is fetched. |
| `ROBOCO_CI_WATCH_DEFAULT_WORKFLOW` | `ci.yml` | Workflow file to scope the CI signal to when a project sets no `ci_watch_workflow` of its own. |
| `ROBOCO_CI_WATCH_INTERVAL_SECONDS` | `1800` | Seconds between CI-watch passes. |
| `ROBOCO_CI_WATCH_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open CI-watch fix tasks per repo. |
| `ROBOCO_CI_WATCH_MAX_PER_CYCLE` | `1` | Max CI-watch fix tasks opened in one cycle. |

### Dependency-update bot — default **off**

The global switch arms the engine; each project opts in via `dep_update_command` (+ optional `dep_update_paths`) in the edit-project dialog. Detection is read-only — the command runs in a throwaway clone and only the lockfiles are diffed; the real repo is never mutated.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_DEP_UPDATE_ENABLED` | `false` | Master switch for the dependency-update bot. When off nothing runs and no throwaway clone is made. |
| `ROBOCO_DEP_UPDATE_INTERVAL_SECONDS` | `604800` | Seconds between dependency-update passes (default weekly). |
| `ROBOCO_DEP_UPDATE_MAX_OPEN_TASKS` | `3` | Rolling cap on concurrently-open update-dependencies tasks per repo. |
| `ROBOCO_DEP_UPDATE_MAX_PER_CYCLE` | `1` | Max update-dependencies tasks opened in one cycle. |

### Gated release manager (default-off)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_RELEASE_MANAGER_ENABLED` | `false` | Master switch for the gated release manager. When off the loop never runs and no release is proposed. Even on it only PROPOSES — the CEO approves before any publish. |
| `ROBOCO_RELEASE_MIN_COMMITS` | `8` | Minimum unreleased commits since the last tag before a release is proposed (a feat/security change also qualifies). |
| `ROBOCO_RELEASE_MANAGER_INTERVAL_SECONDS` | `3600` | Seconds between release-readiness assessment passes. |

### Organizational memory loop (default-off)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROBOCO_ORG_MEMORY_ENABLED` | `false` | Master switch for the org-memory loop. When off: legacy completion capture, no auto-inject, no playbook curation verbs. |
| `ROBOCO_ORG_MEMORY_TOP_K` | `3` | Max institutional-memory items injected into a briefing on claim. |
| `ROBOCO_ORG_MEMORY_MIN_SCORE` | `0.6` | Cosine-similarity floor for injected memory; below it, nothing is injected. |

## Next

- **[Production deploy](./deployment.md)** — compose files, host mounts, secure mode, startup.
- **[Optional capabilities](../optional/index.md)** — what each flag above turns on.
- **[Settings panel](../panel/settings.md)** — toggling flags from the UI instead of `.env`.
