# Web Research Tools

Two tools on the `roboco-search` MCP server: `web_search` and `web_fetch`. Mounted only for **Board + PM roles** — `cell_pm`, `main_pm`, `product_owner`, `head_marketing` — and only when `ROBOCO_RESEARCH_ENABLED` is true (default **on**; see `docs/rag/architecture/config-reference.md`). Every other role has no access.

Calls flow agent → `roboco-search` MCP → `/api/research/search` or `/api/research/fetch` → `ResearchService` → the configured provider. Any provider credentials live only in the server-side orchestrator process; they are never injected into an agent container, and the agent itself never egresses; the orchestrator's own outbound calls do (the provider's API, or, for SearXNG's `web_fetch`, a direct SSRF-guarded page download).

## `web_search`

```python
web_search(query="competitor pricing for AI agent platforms", max_results=5)
```

| Arg | Required | Description |
|-----|----------|--------------|
| `query` | yes | The search query. |
| `max_results` | no | Cap on results, clamped server-side to `ROBOCO_RESEARCH_MAX_RESULTS` (default 5, hard ceiling 20). |

Returns cited results (`title`, `url`, `snippet`, optional `score`) and, when the provider supports it (Tavily's synthesized answer, SearXNG instant answers), a short `answer`. A `guidance` field reminds you to cite the URL for anything you rely on and to persist key findings (see below).

## `web_fetch`

```python
web_fetch(url="https://example.com/pricing", max_chars=5000)
```

| Arg | Required | Description |
|-----|----------|--------------|
| `url` | yes | The page to extract readable content from. |
| `max_chars` | no | Cap on returned characters, clamped server-side to `ROBOCO_RESEARCH_FETCH_MAX_CHARS` (default 20000). |

Works with Tavily and Exa (remote extraction endpoints) and with SearXNG, which downloads the page server-side and extracts readable text locally (trafilatura); the response's `truncated` field tells you whether the content was cut at the cap. Brave has no extraction path; calling `web_fetch` against it returns a "does not support web_fetch" error (HTTP 501 at the route, surfaced as an error envelope to the tool). SearXNG's local fetch refuses URLs that are not plain http(s) resolving to a public address, so it can never be aimed at the deployment's internal services (HTTP 400, see [Errors](#errors-who-refused-and-why)).

## Errors: who refused and why

A failed `web_search` or `web_fetch` comes back as an error envelope: `details.status` is the HTTP status and `details.detail` carries the message. The message names the party that failed the call and why, and the status tells you which party it was:

| Status | Who failed the call | Message shape | What to do |
|--------|---------------------|---------------|------------|
| 424 | The target site of a SearXNG `web_fetch` (SearXNG downloads the page itself) | `Site <host> answered HTTP <code> <phrase> to the page fetch of <url> (<reason>).` or `No answer from site <host> to the page fetch of <url>: <ExceptionKind>: <detail>.` | The page cannot be fetched this way: find the information in another source. When the message says it is a bot-check (challenge) page, the site blocks automated fetches, so retrying the same URL will not help. |
| 502 | The research provider: SearXNG, Tavily, Brave or Exa | `Research provider <name> answered HTTP <code> <phrase> to <METHOD> <endpoint> (<reason>).`, `... with HTTP 200 OK but a body that is not a JSON object (...)`, or `No answer from research provider <name> to <METHOD> <endpoint>: <ExceptionKind>: <detail>.` | Nothing is wrong with your query or URL: retry later, or proceed without external sources. Tavily and Exa download pages through their own API, so a failed `web_fetch` through them is a provider failure too. |
| 400 | RoboCo's URL guard | `RoboCo's URL guard refused '<url>': <why>.` (or `request blocked by security policy` from the route's own filter) | The URL is not plain http(s) to a public host: fix the URL. |
| 501 | Nobody: the provider has no such operation | `brave does not support web_fetch` | Brave has no `web_fetch`; use `web_search` results instead. |
| 429 | Your daily quota | see [Daily quota](#daily-quota) | Wait for the UTC reset. |

`<reason>` is the page `<title>` when the response has one, else its first readable text cut to one short line, and never raw HTML. A response is a bot-check page when it carries a challenge header (Cloudflare's `cf-mitigated: challenge`, AWS WAF's `x-amzn-waf-action: challenge` or `captcha`, Vercel's `x-vercel-mitigated: challenge`) or the title "Vercel Security Checkpoint". A challenge page is a refusal even when its status is 2xx: AWS WAF's challenge answers 202.

A site that answered 403 with Vercel's Security Checkpoint page reads:

```text
Site app.example.com answered HTTP 403 Forbidden to the page fetch of https://app.example.com/pricing (page title: Vercel Security Checkpoint). This is Vercel's bot-check (challenge) page: the site blocks automated fetches, so get this information from another source.
```

## Self-hosted provider (SearXNG, the default)

`ROBOCO_RESEARCH_PROVIDER=searxng` (the **default**) uses the self-hosted SearXNG container (`roboco-searxng`, defined in the compose files) instead of a paid API. It needs **no API key**: the compose files point `ROBOCO_RESEARCH_SEARXNG_BASE_URL` at the bundled container (`http://roboco-searxng:8080`), so a stock stack is live out of the box. It is a metasearch aggregator (Google, Bing, DuckDuckGo, Brave, ...) run inside the deployment's own network: no external search account, no per-query cost. `web_fetch` works via the local extractor described above. Tavily/Brave/Exa remain opt-in paid fallbacks via `.env`. Without the base URL configured, the provider degrades to the same always-empty behavior as `null`.

## Cite-and-persist rules

Web research is external, unverified-by-the-org information — treat it accordingly:

1. **Always cite the URL** for any fact you rely on in a decision, a `delegate` description, or a `dm` message.
2. **Persist key findings** with `note(scope="reflect", ...)` so the source survives beyond your own context window and the team keeps it, not just you.
3. Do not treat a search `answer` or a fetched page as ground truth about RoboCo itself — it is about the outside world (competitors, libraries, market trends), not this codebase.

## Daily quota

Each agent gets `ROBOCO_RESEARCH_DAILY_QUOTA_PER_AGENT` (default 50) combined `web_search` + `web_fetch` calls per UTC day, tracked in Redis. Past the quota, calls return HTTP 429 with a message naming the limit and the UTC reset time. The quota check **fails open** — a Redis outage lets the call through rather than blocking research on an infra hiccup.

## Graceful degradation

With `ROBOCO_RESEARCH_PROVIDER=null`, or `searxng` selected with no base URL, or a paid adapter selected with no `ROBOCO_RESEARCH_API_KEY`, both tools still respond (never a hard failure): `web_search` returns zero results with `provider: "null"` and a guidance string telling you to proceed without external sources or ask the CEO to configure a provider; `web_fetch` returns empty content. Check the `provider` field in the response rather than assuming a provider is configured.

## CEO access

The CEO (as the human operator, via the panel) is also in the research-authorized role set at the API layer, alongside the four agent roles above — but the CEO does not call MCP tools; this is purely so a panel-driven research surface (if built) would not need a separate authorization path.
