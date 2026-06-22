# Choosing a provider

By default every agent runs on Anthropic Claude — no key, no config. The orchestrator mounts your host `~/.claude` session into each container and that's the whole setup. When you want something else — the whole fleet on Grok, a few agents on a self-hosted model, or a deliberate mix — you don't edit env files. You set it on **Settings → AI Providers** (`/settings/ai-providers`, linked from the sidebar footer), and the choice is stored server-side and resolved per agent at spawn.

## The routing modes

The page has one global **routing mode** that decides which backend every agent uses unless you override it per agent:

| Mode | What it does | Needs |
|------|--------------|-------|
| **Anthropic** | Every agent runs on Claude via the mounted `~/.claude` auth. The default. | Nothing — the orchestrator's `~/.claude` mount |
| **Grok** | Every agent runs on xAI Grok (`grok-build`). | A saved Grok key *or* a SuperGrok login — see [Run on Grok](./grok.md) |
| **Ollama** | Every agent runs on Ollama Cloud. | A saved Ollama Cloud key |
| **Self-Hosted** | Every agent runs against your own OpenAI-compatible endpoint. | A base URL with a successful **Test Connection** |
| **Mix** | Pin individual agents to specific models; everything else inherits the global default. | Whatever the pinned models need |

Anthropic, Ollama Cloud, and self-hosted models all speak the Anthropic Messages wire protocol, so they run *through* the same Claude Code runtime — the orchestrator just injects the endpoint and key. Only Grok speaks a different protocol and runs in its own agent image. You don't have to think about that distinction; it's just why Grok is the one mode with a separate setup page.

!!! note "The CEO seat is not an agent"
    Mix mode shows a per-agent table mirroring the org chart, but you (the CEO) are intentionally excluded — there's no model to assign to the human seat. The Intake (Prompter) and Secretary chats *are* in the table.

## How a route is resolved: agent > role > global

When the orchestrator spawns an agent, it picks the model by a three-rung precedence ladder, most specific first:

1. **Agent** — a model pinned to that exact agent (set in Mix mode).
2. **Role** — a model assigned to the agent's role.
3. **Global** — the global mode's default.

The first rung that has an assignment wins. So in Mix mode you can pin `be-dev-1` to one model, leave the rest of the backend cell on the global default, and the dispatcher resolves each one independently at spawn time. Switching the global mode **clears all per-agent overrides** (the panel asks you to confirm), so flip to Mix *after* you've chosen your baseline, not before.

## Saving keys and endpoints

Three secrets live on this page, all stored **Fernet-encrypted** server-side and **never returned by the API** — the panel only ever sees a `has_key` / `has_token` boolean, so you re-enter (you can't read back) a key:

- **Grok (xAI) key** — only needed if you authenticate Grok with a key rather than a SuperGrok login.
- **Ollama Cloud key** — required before any agent can route to Ollama Cloud.
- **Self-hosted endpoint** — a base URL plus an optional auth token. Hit **Test Connection** to confirm reachability and auto-discover the model list; **Refresh** re-discovers it. A self-hosted assignment won't save until the connection tests clean.

The panel guards you client-side: it blocks saving a Grok / Ollama / self-hosted assignment when the matching key or connection is missing, and warns when an Ollama-routed agent has no key (see below).

## Fail-soft: misconfig falls back to Anthropic

Routing is deliberately fail-soft. A *stalled* spawn is worse than a *wrong* model, so on any of these the orchestrator silently degrades that agent to Anthropic Claude rather than refusing to start it:

- no assignment found for the agent, its role, or the global default;
- a self-hosted or Ollama endpoint that's unreachable (the orchestrator probes `/api/tags` before committing to it);
- a stored key or token that fails to decrypt.

!!! warning "A quiet fallback can look like success"
    Because the fallback is silent, an agent you *think* is on your self-hosted model may actually be running on Claude — for example if the endpoint went unreachable after you tested it. The fallback is logged in the orchestrator (`docker compose logs orchestrator` shows the warning). If you're routing away from Anthropic, watch the per-model breakdown on the [usage dashboard](../operations/cost-and-usage.md) to confirm the traffic is landing where you intended.

!!! info "Providers are seeded by the migrations"
    The Anthropic, Grok, Ollama Cloud, and self-hosted provider rows are created by `alembic upgrade head`, which the stack runs for you on boot. The AI Providers page only sets keys, mode, and assignments — it never creates providers. If you skipped migrations you'll see "provider not seeded" errors here.

## Next

- [Run on Grok](./grok.md) — the SuperGrok path end to end.
- [What keeps a run alive](./resilience.md) — crash retry and provider park-and-resume.
- The same guardrails apply on every backend — see [how agents are sandboxed](../company/agent-gateway.md).
