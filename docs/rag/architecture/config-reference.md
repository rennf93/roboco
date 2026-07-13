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

Env-gated subsystems. Most are default-off; `ROBOCO_OVERLOAD_BREAK_ENABLED`, `ROBOCO_RESEARCH_ENABLED`, and `ROBOCO_PROVISIONING_ENABLED` ship default-**on**. Each takes effect on the next backend restart; the panel's Settings → Feature Flags card toggles the panel-exposed ones (`roboco/services/settings.py`'s `FEATURE_FLAGS`) without hand-editing env — a few security/topology flags below are env-only by design and are called out as such.

The PR-gate turn cut (when every child of an assembled parent is terminal, `_try_auto_submit` runs the real `submit_up` / `submit_root` system-side as the owning PM instead of spawning the PM for that turn) is **unconditional** — no flag, no kill-switch. A gate rejection (freshness/integrity/AC-coverage/a subtask-terminal race) or transport error falls back to the classic PM closure spawn with the rejection reason threaded into the PM's prompt; that fallback is the sole safety net. Each auto-submit leaves a `task.auto_submitted` audit row.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_CONVENTIONS_ENABLED` | `false` | Architectural Conventions Standard: auto-scaffold `.roboco/conventions.yml`, inject the architecture map, attach baseline constraints, and block `i_am_done` / `pr_pass` on block-level placement and hygiene violations. Off = fully inert. |
| `ROBOCO_TOOLCHAIN_MATCH_ENABLED` | `false` | Provision each agent workspace with the target project's Python (resolved from its `requires-python` / `.python-version`) and block delivery gates when the suite cannot be executed under it. Off = today's behavior. |
| `ROBOCO_OVERLOAD_BREAK_ENABLED` | `true` | Park a provider on a persistent model-API overload (HTTP 529 / 500 / 503) the same way a 429 is parked — queue its spawns and probe until it recovers — instead of crash-retrying into the overload. Off = crash-retry behavior. |
| `ROBOCO_SPAWN_PREFLIGHT_ENABLED` | `false` | Refuse to spawn a non-human delivery role absent from `GATEWAY_ENABLED_ROLES` (no manifest → can never claim → would respawn on the same task forever); refuse + alert the overseer once instead. Inert in practice (all delivery roles are gateway-enabled). Armed on the NAS composes. |
| `ROBOCO_NOTIFICATION_SPAWN_COOLDOWN_SECONDS` | `600` | Cross-tick damper for notification-triggered spawns (escalation/approval/audit/a2a — task-less, so the readiness gate and respawn breaker never see them): one spawn per (agent, notification) per window; the notification stays pending so the next window retries. `0` = legacy every-tick respawn. |
| `ROBOCO_SANDBOX_DB_ENABLED` | `false` | Sandboxed per-agent-spawn test DB/Redis/Mongo: throwaway sibling containers provisioned from the engine registry in `roboco/models/sandbox.py` (postgres:16-alpine / redis:8-alpine / mongo:8), per-project opt-in. The valid-service set is `VALID_SANDBOX_SERVICES` (registry-derived). See "Sandboxed Dev DB/Redis/Mongo" below and `docs/rag/architecture/sandbox-db.md`. |
| `ROBOCO_X_ENGINE_ENABLED` | `false` | The X (Twitter) engine: draft release/mention posts, ALL held for per-post CEO approval. See "X (Twitter) Engine" below and `docs/rag/architecture/x-engine.md`. |
| `ROBOCO_ROADMAP_ENGINE_ENABLED` | `false` | The board roadmap engine: weekly Product-Owner-authored cycle, CEO approves each item individually into BACKLOG. See "Board Roadmap Engine" below. |
| `ROBOCO_OBSIDIAN_VAULT_ENABLED` | `false` (both compose files set `true`) | The Obsidian vault projection: tasks/journals/A2A become wikilinked markdown, rebuildable from the DB. See `docs/rag/architecture/obsidian-vault.md`. |
| `ROBOCO_VAULT_INTAKE_ENABLED` | `false` (both compose files set `true`) | The vault's `#roboco`-tag inbox watcher — requires `ROBOCO_OBSIDIAN_VAULT_ENABLED` also on. See `docs/rag/architecture/obsidian-vault.md`. |
| `ROBOCO_VAULT_ARCHIVE_DAYS` | `30` (`0` disables) | Age (past its terminal timestamp) a completed/cancelled task's note must reach before the vault janitor moves it to `RoboCo/Archive/<year>/`. |
| `ROBOCO_VAULT_REPORT_ENABLED` | `true` | The vault janitor's weekly `RoboCo/Reports/<ISO-week>.md` org-report note + CEO notification (deterministic, no LLM). Needs `ROBOCO_OBSIDIAN_VAULT_ENABLED` also on. |
| `ROBOCO_VAULT_KB_ENABLED` | `false` (NAS compose sets `true`; registry compose leaves `false`) | Master switch for vault KB ingest: embeds `ROBOCO_VAULT_KB_DIRS` note folders (default `RoboCo/Notes`) into `IndexType.VAULT_NOTES`, screened for injection attempts before indexing. Requires `ROBOCO_OBSIDIAN_VAULT_ENABLED` also on. `ROBOCO_VAULT_KB_DIRS` (CSV, default `RoboCo/Notes`) and `ROBOCO_VAULT_KB_INTERVAL_SECONDS` (default `900`) tune scope and cadence. See `docs/rag/architecture/obsidian-vault.md`. |

The company-in-a-box subsystems toggle the same way: web research (`ROBOCO_RESEARCH_ENABLED`, default **on** — see "Web Research" below), the strategy engine (`ROBOCO_STRATEGY_ENGINE_ENABLED`, default off), and pitch provisioning (`ROBOCO_PROVISIONING_ENABLED`, default on but inert without a token/org configured).

## Web Research

Pluggable external search/fetch for the Board + PM roles (`cell_pm`, `main_pm`, `product_owner`, `head_marketing`). Calls flow agent → `roboco-search` MCP → `/api/research/*` → `ResearchService` → provider; the provider key lives only in the server-side process — it is never injected into an agent container, and agents never egress (the provider's own API does). See `docs/rag/tools/research-tools.md` for the `web_search` / `web_fetch` tool contract.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_RESEARCH_ENABLED` | `true` | Master switch. Ships default-**on** (unlike most feature flags): the `roboco-search` MCP server is mounted for the four research roles unless explicitly disabled. Panel-toggleable. Both NAS composes also set `ROBOCO_RESEARCH_ENABLED:-true` explicitly (redundant with the code default, but keeps the deploy's env self-documenting). |
| `ROBOCO_RESEARCH_PROVIDER` | `tavily` | Adapter: `tavily` (LLM-native cited results + extract), `brave` (independent index, no fetch endpoint), `exa` (neural search + contents), or `null` (always-empty stub). Swapping providers is a config change only. |
| `ROBOCO_RESEARCH_API_KEY` | (unset) | API key for the selected provider. Server-side only. Unset ⇒ graceful `NullProvider` (empty results, never a hard fail). |
| `ROBOCO_RESEARCH_MAX_RESULTS` | `5` | Hard cap (1-20) on `web_search` results per call. |
| `ROBOCO_RESEARCH_FETCH_MAX_CHARS` | `20000` | Hard cap on characters `web_fetch` returns; content past this is truncated. |
| `ROBOCO_RESEARCH_TIMEOUT_SECONDS` | `15.0` | Per-request timeout for outbound provider HTTP calls. |
| `ROBOCO_RESEARCH_DAILY_QUOTA_PER_AGENT` | `50` | Max `web_search` + `web_fetch` calls per agent per UTC day. Tracked in Redis; fails open (allows the call) if Redis is unreachable. |

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

## Sandboxed Dev DB/Redis/Mongo

Per-agent-spawn throwaway Postgres/Redis/Mongo, replacing (never coexisting with) the legacy prod-creds gate-env injection for an opted-in project. Default-off; see `docs/rag/architecture/sandbox-db.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_SANDBOX_DB_ENABLED` | `false` | Master switch. Off = spawning behaves exactly as today (the legacy `_append_gate_env` prod-creds injection, itself gated by `ROBOCO_TOOLCHAIN_MATCH_ENABLED`). Only projects with their `sandbox_services` column set (migration `057`) participate even when on. The valid service set is `VALID_SANDBOX_SERVICES` in `roboco/models/sandbox.py` (registry-derived: postgres / redis / mongo); adding an engine is one class + one registry line, no orchestrator edit. Env injected per engine: `ROBOCO_TEST_DB_*`, `ROBOCO_TEST_REDIS_*`, `ROBOCO_TEST_MONGO_*` (incl. `ROBOCO_TEST_MONGO_AUTH_DB=admin`). A project may also declare `sandbox_extensions` (migration `072`) — a per-service extension/module map activated post-ready via `docker exec`, bounded by a fixed allowlist (`SANDBOX_PG_EXTENSIONS` / `SANDBOX_REDIS_MODULES`; no `plpython3u`). See `docs/rag/architecture/sandbox-db.md`. |

## X (Twitter) Engine

Drafts release-announcement and mention-reply posts, ALL held for per-post CEO approval — nothing ever posts automatically. Default-off; see `docs/rag/architecture/x-engine.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_X_ENGINE_ENABLED` | `false` | Master switch. Off = no draft is originated and no X API call is ever made. Even on, posting requires stored credentials (panel-entered) AND an explicit per-post CEO approval. |
| `ROBOCO_X_REPLIES_ENABLED` | `false` | Sub-switch for the mention-reply half. Off (even with the engine on) = the engine only drafts release-announcement posts; it never polls mentions or drafts replies. Reading mentions needs a paid X API tier, so replies are a deliberate opt-in on top of release posting. |
| `ROBOCO_X_MENTIONS_INTERVAL_SECONDS` | `1800` | Seconds between mentions-poll passes (only when `X_REPLIES_ENABLED`). |
| `ROBOCO_X_MENTIONS_MAX_PER_CYCLE` | `5` | Max held reply proposals the mentions poll may originate in one cycle. |
| `ROBOCO_X_MENTIONS_MIN_ENGAGEMENT` | `0` | Minimum like+reply+retweet count for a mention to count as "meaningful" (the engagement half of the mention filter; the other half rejects bare retweets and near-empty text). |
| `ROBOCO_X_MAX_OPEN_POSTS` | `10` | Rolling cap on concurrently-open held X posts/replies (both sources combined); the engine originates nothing more past it. |
| `ROBOCO_X_ACCOUNT_USER_ID` | (empty) | Numeric X user id of the account's own account. Empty resolves it once per mentions cycle via `GET /2/users/me` (one extra call). |
| `ROBOCO_X_REQUEST_TIMEOUT_SECONDS` | `15.0` | Per-request timeout for outbound X API HTTP calls. |

## Board Roadmap Engine

Weekly, the Product Owner explores the company's projects and proposes a themed cycle of roadmap item drafts; the CEO approves or rejects each one individually. Default-off; approved items land in BACKLOG and nothing auto-starts. See `docs/rag/architecture/company-layer.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_ROADMAP_ENGINE_ENABLED` | `false` | Master switch. Off = no exploration cycle is originated and the Product Owner is never spawned for this. |
| `ROBOCO_ROADMAP_INTERVAL_SECONDS` | `604800` | Seconds between roadmap-exploration cycles (default weekly). |
| `ROBOCO_ROADMAP_MIN_ITEMS_PER_CYCLE` | `3` | Minimum roadmap item drafts a themed cycle must propose. |
| `ROBOCO_ROADMAP_MAX_ITEMS_PER_CYCLE` | `7` | Maximum roadmap item drafts a themed cycle may propose. |

No dedicated migration — a cycle is marker-backed (`orchestration_markers` on the held exploration task), not a new table.

## Cloud Auth

**Not a panel feature flag** — unlike the flags above, `ROBOCO_CLOUD_AUTH_ENABLED` is env-only (deliberately absent from `roboco/services/settings.py`'s `FEATURE_FLAGS`, so it can't be flipped on for a deployment that isn't behind TLS). Lets the panel/API be exposed beyond localhost without changing the CEO's local no-login flow while off. See `docs/rag/architecture/cloud-auth.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_CLOUD_AUTH_ENABLED` | `false` | Master switch. Off: `get_agent_context` behaves byte-for-byte as today (header-trust). On: no registration router is mounted — exactly one user, seeded from `cloud_auth_email` / `cloud_auth_password`. **Fails loud at startup** (raises before the app boots) if `true` without `ROBOCO_CLOUD_AUTH_SECRET` set. |
| `ROBOCO_CLOUD_AUTH_EMAIL` | (unset) | Email for the single seeded CEO login user. |
| `ROBOCO_CLOUD_AUTH_PASSWORD` | (unset) | Password for the single seeded user. Hashed at startup; never stored in plain text. |
| `ROBOCO_CLOUD_AUTH_SECRET` | (unset) | Session-signing secret for the login cookie's JWT. Required when enabled — generate with `python -c 'import secrets; print(secrets.token_hex(32))'`. |
| `ROBOCO_CLOUD_AUTH_COOKIE_MAX_AGE` | `2592000` | Session cookie lifetime in seconds (30 days). Sliding: every authenticated request re-mints + re-sets the cookie, so an active session never expires — only genuine inactivity past this window logs out. |

The session cookie is `secure`-only (`cookie_secure=True` in `roboco/api/auth/backend.py`) — arm this flag only behind TLS, or the browser will silently refuse to send the cookie and login will appear to fail.

## DB Network Isolation

**Not a panel feature flag** — `ROBOCO_DB_NETWORK_ISOLATED` must travel with the compose file's `networks:` stanzas (it describes topology, not a runtime-toggleable behavior), so it is env-only like `ROBOCO_CLOUD_AUTH_ENABLED`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_DB_NETWORK_ISOLATED` | `false` | Set `true` by the compose files that put postgres/redis on the data-only `roboco_data` network agents never join. Suppresses the legacy `_append_gate_env` prod-creds injection (unreachable creds are worse than none) — DB-needing projects use the sandbox opt-in instead. |

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_ENCRYPTION_KEY` | (required) | Fernet key that encrypts secrets at rest (e.g. per-project git tokens, the `x_credentials` OAuth 1.0a singleton row). Generate with `Fernet.generate_key()`. |
| `ROBOCO_AGENT_AUTH_SECRET` | (required) | HMAC secret the orchestrator signs each agent's `X-Agent-Token` with. |
| `ROBOCO_AGENT_AUTH_REQUIRED` | `false` | When `true`, every request must carry a valid agent token (secure mode). |
| `ROBOCO_PANEL_AGENT_TOKEN` | (unset) | The control panel's CEO token, injected by nginx in secure mode. Get it with `make panel-token`. |

`ROBOCO_ENCRYPTION_KEY` also encrypts the `x_credentials` row (the four X/Twitter OAuth 1.0a secrets) — the same key, one more consumer.
