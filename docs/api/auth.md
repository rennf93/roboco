# Authentication

RoboCo's API identifies a caller by a small set of headers — `X-Agent-ID`, `X-Agent-Role`, and optionally `X-Agent-Team`. How much it *trusts* those headers depends on one flag. Out of the box the API runs in header-trust mode, which is fine on a private LAN and dangerous anywhere else. This page covers both modes and the WebSocket caveat.

## The identity headers

Every REST request carries:

| Header | Required | Meaning |
|--------|----------|---------|
| `X-Agent-ID` | Yes | The agent's UUID or slug (e.g. `be-dev-1`). |
| `X-Agent-Role` | Yes | The role the caller is acting as (e.g. `developer`, `cell_pm`, `ceo`). |
| `X-Agent-Team` | No | The team (`backend`, `frontend`, `uxui`), when relevant. |
| `X-Agent-Token` | Only in secure mode | The HMAC token that proves the headers above weren't forged. |

These are resolved in `roboco/api/deps.py` (`get_agent_context`), and the role gates the action — so a request claiming `X-Agent-Role: ceo` can do CEO-only things like approving and merging.

## Header-trust mode (default)

By default `ROBOCO_AGENT_AUTH_REQUIRED` is unset/false. In this mode the API **accepts the role headers without verifying any token**. There is no proof of identity: whoever sets `X-Agent-Role: ceo` *is* the CEO for that request.

!!! danger "Anyone who can reach the API can claim any role — including CEO"
    In header-trust mode there is no authentication. Any client that can open a connection to the orchestrator port can act as any agent, approve and merge work as the CEO, cancel tasks, or override task state. The app logs a loud startup warning to this effect. This is acceptable **only** on a trusted private network where nothing untrusted can reach the orchestrator — which is the default single-host LAN deployment behind nginx on `localhost:3000`. Do not expose the orchestrator to anything you don't control without first turning on secure mode.

## Secure mode (HMAC tokens)

Set `ROBOCO_AGENT_AUTH_REQUIRED=true` to require a signed token on every REST request. In this mode:

- `X-Agent-Token` becomes mandatory; a request without it is rejected with **401**.
- The token is an HMAC signed with `ROBOCO_AGENT_AUTH_SECRET` and **bound to the agent's id, role, and team**. The server recomputes the signature over the presented `X-Agent-ID` / `X-Agent-Role` / `X-Agent-Team` and compares it constant-time. If a caller swaps the role header to escalate to `ceo`, the signature no longer matches and the request is rejected with **401 — signature mismatch**.
- The orchestrator issues each agent its token at spawn time, so delivery agents are authenticated by construction.
- A presented token is **always** verified, even when auth isn't required — so you can roll out tokens before flipping the switch without breaking anything.

| Flag | Default | Purpose |
|------|---------|---------|
| `ROBOCO_AGENT_AUTH_REQUIRED` | `false` | When `true`, REST requires a valid HMAC `X-Agent-Token`. |
| `ROBOCO_AGENT_AUTH_SECRET` | (unset) | Shared secret the orchestrator uses to sign and the API uses to verify the per-agent token. |

!!! tip "How the panel authenticates as the CEO"
    The control panel acts as the CEO agent. In secure mode, nginx injects the panel's CEO `X-Agent-Token` so your browser session is authenticated without you handling the secret — you just use the panel as normal.

## The WebSocket caveat

Token enforcement is **REST-only**. The [WebSocket streams](./websockets.md) do not check the HMAC token:

- The per-resource sockets (`/ws/channels|agents|sessions|notifications/{id}`) validate their `agent_id`/`viewer_id` query param against the database and channel access, but not a token.
- `/ws/system` is fully unauthenticated.

The streams are read-only and carry no control surface or secrets, so this isn't a privilege-escalation path the way the REST headers are — but it does mean the orchestrator port should stay trusted-network-only until WebSocket auth lands, even when you've enabled secure-mode REST.

## What to do

- **Single-host LAN, nothing untrusted on the network** → header-trust is fine; that's the default.
- **Anything reachable beyond a trusted LAN** → set `ROBOCO_AGENT_AUTH_REQUIRED=true` and a strong `ROBOCO_AGENT_AUTH_SECRET`, and keep the orchestrator port off the public internet regardless.

For the full hardening checklist — network exposure, the GitHub PAT handling, and the prompt/bash guards — see [Security](../troubleshooting/security.md).

## Next

- [REST API](./rest-api.md) — the route surface these headers authorize.
- [WebSockets](./websockets.md) — the live streams and their separate auth model.
