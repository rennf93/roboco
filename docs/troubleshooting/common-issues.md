# Common issues

When something goes wrong, the failure is almost always one of a handful of things: a mount that didn't make it into the container, a model that isn't pulled, a token that expired, or a provider that's parked rather than hung. This page is the problem→cause→fix guide for the snags you'll actually hit, ordered by how often they bite on a fresh deployment.

## Quick reference

| Symptom | Most likely cause | Fix |
|---------|-------------------|-----|
| Agents spawn but do nothing useful (tool-discovery churn) | The role's tool-manifest didn't mount; the agent falls back to discovering verbs | Check the manifest mount (below) |
| Agent containers respawn in a loop, MCP servers stuck "pending" | MCP server launched without `--no-sync` against a workspace clone | Already fixed in the orchestrator; verify your image is current (below) |
| A provider's agents go quiet all at once | The provider is **parked-and-probing** after a 429 / overload / session-limit — not hung | Wait; it self-resumes. See [Resilience](../models/resilience.md) |
| A task sits **blocked: "branch behind base / needs rebase"** | The agent's branch fell behind its base while it worked; there is no agent-layer rebase verb, so the agent escalates instead of improvising | Rebase it yourself from the panel **Git** tab (below) |
| KB / `ask_mentor` returns nothing | Ollama unhealthy or models not pulled | Check `ollama-init` logs (below) |
| Agent containers exit immediately | `~/.claude` not mounted, or a Grok token expired | Check the mount / refresh the token (below) |
| Clone fails, agent can't reach the repo | Missing or invalid project PAT, or HTTPS URL with no token | Set the project token (below) |
| Orchestrator won't start | `ROBOCO_ENCRYPTION_KEY` unset, or a pending migration | First-run checklist (below) |

## Agents spawn but do nothing

If agents come up but burn turns "looking for tools" instead of claiming work, the cause is almost always the **tool manifest**. At spawn the orchestrator writes a per-agent manifest and mounts it read-only at `/app/tool-manifest.json` (the mount is built in `roboco/runtime/orchestrator.py`, pointing `ROBOCO_TOOL_MANIFEST_PATH` at that path). The manifest lists exactly the verbs the agent's role may call — see [the gateway](../company/agent-gateway.md).

If that file is missing inside the container, the agent has no allow-list to read and falls back to discovering the verb surface itself, which wastes turns and tokens. The mount source is `{DATA_HOST_PATH}/manifests/{agent_id}.json` on the host. Confirm the host directory exists and is writable, and that the data volume is mounted into the orchestrator so it can write manifests there in the first place.

!!! tip "Verify the mount inside the container"
    Exec into a running agent container and check that `/app/tool-manifest.json` exists and is non-empty. If it's absent, the orchestrator couldn't write or mount the per-agent manifest — fix the host `manifests/` directory and the orchestrator's data-volume mount, then respawn.

## Respawn loop with MCP servers stuck "pending"

A historical failure mode: each agent's MCP servers (`roboco-flow`, `roboco-do`, the read-only git and KB servers) launch via `uv run` from inside the agent's workspace clone. If `uv` re-syncs that clone on launch it can collide with the clone's lock, the gateway servers never come up, the agent has zero verbs, and the orchestrator respawns it in a loop.

The fix is already in the code: every MCP server is launched with `uv run --no-sync` (`roboco/runtime/orchestrator.py`), and the SDK startup hook pins `uv` to the pre-baked image venv. If you see this symptom, your running image predates the fix — rebuild and redeploy the agent images so the `--no-sync` launch is in effect.

## A "quiet" provider is parked, not hung

If every agent on one provider goes silent at the same moment, it is almost never a crash. On a provider 429, a persistent overload (HTTP 529/500/503), or a Claude session-limit (the rolling 5-hour usage window), RoboCo **parks** that provider's work and runs a background probe that resumes it the moment the provider recovers — it does not crash-retry into the wall and burn tokens. You'll see an amber banner in the panel; the work revives on its own.

!!! note "Don't restart to 'unstick' it"
    Restarting the orchestrator throws away the park-and-probe state and the parked agents' context. Leave it alone — it self-heals. The full mechanism, the banner, and the `ROBOCO_OVERLOAD_BREAK_ENABLED` flag are documented in [Resilience](../models/resilience.md).

## KB search / ask-mentor returns nothing

The knowledge base and `roboco_ask_mentor` run on the in-house pgvector RAG, which depends on Ollama for embeddings and the local LLM. If the KB is empty or `ask` comes back blank, Ollama is the usual culprit:

- **Models not pulled.** `ollama-init` pulls `qwen3-embedding:0.6b` (the embedder, ~30s) and the local LLM (~2min) on startup. Check `docker logs roboco-ollama-init` — if the pull failed or is still running, embeddings can't be produced and indexing/retrieval returns nothing.
- **Ollama unhealthy.** The healthcheck is `ollama list`, not a curl. If the `ollama` service is unhealthy the orchestrator's document indexing (run during FastAPI lifespan) never completes.
- **A `404 /api/embed`** in the logs means the embedding model isn't present — re-check `ollama-init`.

See [Health and metrics](../operations/health-and-metrics.md) for where these surface in the panel.

## Agent containers exit immediately

A container that starts and dies within seconds is missing something it needs at boot:

- **`~/.claude` not mounted (Claude agents).** Claude-backed agents mount the host's `~/.claude` (overridable via `ROBOCO_HOST_CLAUDE_DIR`) into the container so they can authenticate. If that directory is absent or unreadable on the host, the agent can't start. Confirm the host directory exists and the orchestrator user can read it.
- **Grok token expired (Grok agents).** Grok's access token has a fixed ~6h TTL and the CLI can't refresh it headlessly — on an expired token it would otherwise hang forever at an interactive login prompt. The entrypoint runs `python -m roboco.llm.providers.grok_auth --check` as a preflight and **exits 78** immediately rather than hang (`docker/scripts/grok-cli-agent-entrypoint.sh`). An exit code of 78 means the Grok token is missing or expired. The orchestrator mints a fresh token before expiry on each dispatch tick; if you keep hitting 78, re-run `grok login` on the host to repopulate the shared `auth.json`.

## Clone fails / agent can't reach the repo

Cloning is the first thing an agent does on a project, and it fails for one reason in practice: the **project's GitHub token**. Git auth is per-project, not global.

- An **HTTPS Git URL with no token fails** — the agent can't authenticate. Set the token when you create the project.
- An **invalid or under-scoped token** fails the same way. The token needs repository contents access (clone/push) and pull-request access. See [Register a project](../get-started/first-project.md#the-github-token) for the exact scopes.

Remember the API never returns a stored token, so the panel only shows whether one is set, not its value — if a clone fails, re-enter the token rather than assuming the stored one is good.

!!! warning "If you rotated ROBOCO_ENCRYPTION_KEY"
    Every project PAT is Fernet-encrypted with `ROBOCO_ENCRYPTION_KEY`. Change that key and all stored tokens become undecryptable — clones will fail until you re-enter every token. See [Security](./security.md).

## First-run and migration checks

When the orchestrator won't come up at all on a fresh deploy:

- **`ROBOCO_ENCRYPTION_KEY` must be set.** It defaults to empty in config, but the orchestrator refuses to start without it. Generate one with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` and set it in your `.env`. It is load-bearing and must never change after first use.
- **Migrations.** Schema changes ship as Alembic migrations under `alembic/versions/`. The API applies them on startup (and falls back to `create_all` on a fresh DB), but after pulling a change that adds a migration you can apply it explicitly with `docker compose exec orchestrator alembic upgrade head`.
- **Startup is slow on purpose.** The FastAPI lifespan does ~30–60s of document indexing before the API answers, and the orchestrator polls `/health` for up to 120s before starting its dispatch loop. An "All connection attempts failed" early in the logs usually just means a dependent service hadn't finished its healthcheck yet — give the startup sequence time before treating it as an error.

## A task is stuck on a branch behind its base

Agents have no rebase, pull, or merge verb — a task branch is brought current with its base only at claim. If the base (a cell branch, or master) moves forward while the agent works, the branch falls behind, and the agent escalates rather than improvising git surgery: the task surfaces **blocked** with a reason like *"branch behind base — needs rebase."* That escalation is by design — bringing the branch current is your call, not a unit of work the company decomposes. Rebase it from the panel **Git** tab — select the branch and **Rebase** it onto its base (or master) — and the task resumes on the next dispatch. (Automatic rebase-at-spawn, so a stale branch never reaches you at all, is on the roadmap.)

## Next

→ [Security](./security.md) — the trust model and how to harden a deployment, or back to the [troubleshooting index](./index.md).
