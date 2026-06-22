# Web Research

Web research lets your Board and PM agents ground their scoping in the live web instead of working from training data alone. When it is on, the Board (Product Owner, Head of Marketing) and the PM roles gain two tools — `web_search` and `web_fetch` — exposed through the `roboco-search` MCP server mounted into those agents. The provider's API key lives only on the server; it never enters an agent container, so the agent never sees or can exfiltrate it.

## Default state

Web research is **flag-gated by `ROBOCO_RESEARCH_ENABLED`**. The config default in `roboco/config.py` is `true`, but the Settings → Feature Flags card presents it as an operator toggle you turn on explicitly. The flag governs the mount: when it resolves to false, the `roboco-search` MCP server is not mounted into any agent and the `web_search` / `web_fetch` tools are simply absent.

!!! info "Config default vs. the Feature Flags toggle"
    The two surfaces can disagree, and the resolution rule is simple: a flag you set in **Settings → Feature Flags** wins, and an *unset* flag falls back to the environment / config default. Because the config default for `ROBOCO_RESEARCH_ENABLED` is `true`, a fresh deploy that has never touched the toggle has research **on**. If you want it off, set the toggle off (or set `ROBOCO_RESEARCH_ENABLED=false` in your environment) — don't assume the panel's switch position alone reflects the live state until you've set it once.

## Enable it

You need both the capability on *and* a provider configured, or agents get an empty stub.

1. **Set the flag.** Either set `ROBOCO_RESEARCH_ENABLED=true` in your deploy environment, or flip **Settings → Feature Flags → "Let the Board and PMs run web research"** on.
2. **Choose a provider and supply its key** in your environment (see below).
3. **Restart the backend.** Feature-flag changes take effect on the next backend restart — the panel toast says exactly this.

## Pick a provider and supply a key

| Env var | Default | Purpose |
|---------|---------|---------|
| `ROBOCO_RESEARCH_PROVIDER` | `tavily` | Search adapter: `tavily` \| `brave` \| `exa` \| `null`. Swapping providers is a config-only change. |
| `ROBOCO_RESEARCH_API_KEY` | (unset) | API key for the selected provider. **Server-side only — never reaches an agent container.** Unset → the `null` provider is used (empty results, no error). |

!!! warning "Brave has no `web_fetch`"
    Only `tavily` and `exa` support fetching page contents. The `brave` adapter is search-only — a `web_fetch` call against it raises "unsupported". If your agents need to read the pages they find, choose `tavily` or `exa`. The `null` provider always returns empty results and is the graceful fallback when no key is set, so a missing key degrades quietly instead of erroring mid-task.

The provider's own API does the outbound web egress, not the agent — the agent calls the in-container MCP tool, the orchestrator-side `ResearchService` calls the provider with the server-held key, and only the cleaned results flow back.

## What changes when it's on

Board and PM agents can call `web_search` (a query → ranked results) and, on Tavily/Exa, `web_fetch` (a URL → extracted text). The service clamps every call so a provider can't run away with cost or context:

| Env var | Default | Purpose |
|---------|---------|---------|
| `ROBOCO_RESEARCH_MAX_RESULTS` | `5` | Hard cap on `web_search` results per call (1–20). |
| `ROBOCO_RESEARCH_FETCH_MAX_CHARS` | `20000` | Hard cap on characters returned by `web_fetch`. |
| `ROBOCO_RESEARCH_TIMEOUT_SECONDS` | `15.0` | Per-request timeout for outbound provider calls. |
| `ROBOCO_RESEARCH_DAILY_QUOTA_PER_AGENT` | `50` | Max `web_search` + `web_fetch` calls per agent per UTC day. |

The daily quota is your main cost lever. It is tracked per agent per UTC day in Redis and is **fail-open**: if Redis is unreachable the call is allowed through, because the quota is cost-control, not a security boundary.

## Required extra config

The only hard requirement beyond the flag is a real provider key. With `ROBOCO_RESEARCH_API_KEY` unset (or `ROBOCO_RESEARCH_PROVIDER=null`), the tools mount but every search returns empty — useful for a dry run, useless for actual grounding. Nothing else (no migration, no panel setup) is needed.

## Next

→ **[Strategy engine](./strategy-engine.md)** — the other notify-only steering loop. → **[The business workflow](../how-to/05-the-business-workflow.md)** — where the Board and PMs use research in practice.
