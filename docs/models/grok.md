# Run on xAI Grok

RoboCo can run the entire workforce — or just some agents — on **xAI Grok**, using xAI's official `grok` CLI on a **SuperGrok subscription** rather than a metered API key. That matters operationally: a subscription can't run out of credits mid-task, so a Grok fleet won't stall halfway through a delivery the way a metered key can. Grok reaches full parity with the Claude path by construction: the same MCP gateway, the same per-role tool manifest, the same prompt-injection and bash guards, the same per-agent cost capture.

## Set it up once

Authenticate Grok on the host with the official CLI, then point RoboCo at the resulting directory:

```bash
grok login                     # once, on the host — creates ~/.grok/auth.json
```

Then in `.env`:

```bash
ROBOCO_HOST_GROK_DIR=/home/youruser/.grok   # the REAL host ~/.grok to mount in
```

The orchestrator mounts that directory's `auth.json` **read-only** into each Grok agent container. On a NAS or any docker-in-docker deploy the orchestrator's home is not the host's home, so `ROBOCO_HOST_GROK_DIR` must point at the actual host path where `grok login` wrote `~/.grok` — otherwise agents start with no credential.

Finally, on **Settings → AI Providers** set the routing mode to **Grok** (whole fleet) or pin individual agents to a Grok model in **Mix** mode. See [Choosing a provider](./provider-routing.md) for the modes and precedence. The [installation guide](../get-started/installation.md#optional-run-on-grok-instead) covers the same first-run steps inline.

## The token refreshes itself

The Grok access token has a fixed ~6-hour, server-set lifetime, and the CLI can't refresh it headlessly — on an expired token it would hang forever at an interactive login prompt. RoboCo handles this for you:

- Once per dispatch tick the orchestrator mints a fresh token from the offline-access refresh token (xAI's OIDC `refresh_token` grant) before expiry and atomically rewrites the shared `auth.json` in place. The refresh fires `ROBOCO_GROK_AUTH_REFRESH_SKEW` seconds (default `1800`) ahead of expiry.
- As a backstop, each agent's entrypoint runs `python -m roboco.llm.providers.grok_auth --check` and **refuses to start** on a missing or expired token instead of hanging.

!!! warning "The orchestrator's `~/.grok` mount must be writable"
    The orchestrator rewrites `auth.json` when it refreshes the token, so the orchestrator's own mount of `~/.grok` must be **read-write**. (The per-agent mount stays read-only — agents only read the credential.) If the orchestrator can't write it, the token will expire and Grok agents will fail their start-up `--check`. If the host `auth.json` is missing entirely at spawn time, the orchestrator logs a loud warning — a missing credential is the most common Grok misconfiguration, so it's surfaced early rather than as a fleet of failed starts.

## Per-fleet tuning

Three optional knobs let you trade cost against quality across the whole Grok fleet. All are env-set and apply uniformly (no per-role reduction — that's parity with the Claude path):

| Variable | Default | What it does |
|----------|---------|--------------|
| `ROBOCO_GROK_REASONING_EFFORT` | unset (model default) | The `grok --effort` level. Set `low` / `medium` / `high` / `xhigh` / `max` to dial cost vs quality; empty, `default`, or `full` keeps the model's own default. |
| `ROBOCO_GROK_MAX_TURNS` | `200` | The per-agent turn cap for a `grok -p` run. |
| `ROBOCO_GROK_MAX_COST_USD` | `0` (off) | A per-agent cost ceiling in USD. The grok CLI has no live usage hook, so the orchestrator reads each running container's captured cost and kills it once it crosses this ceiling — a backstop against a runaway loop. `0` disables the cap. |

Two more variables exist mostly for staged rollouts and tests:

| Variable | Default | What it does |
|----------|---------|--------------|
| `ROBOCO_GROK_AGENT_IMAGE` | `roboco-agent-grok:latest` | Override the Grok agent Docker image. |
| `ROBOCO_GROK_CLI_MODEL` | `grok-build` | The grok CLI model id passed to agents. |

!!! note "Usage and cost still show up"
    Each Grok agent gets a per-agent usage directory mounted read-write so its token/cost is read back when the session finalizes. Grok traffic therefore lands on the same [usage dashboard](../operations/cost-and-usage.md) as Claude, priced from the captured session totals.

## Next

- [Choosing a provider](./provider-routing.md) — set the Grok mode or pin agents in Mix.
- [What keeps a run alive](./resilience.md) — Grok agents park-and-resume on a rate limit like any other provider.
