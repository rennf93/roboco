# Security

RoboCo runs a workforce of agents with access to your repositories, so its security model is worth understanding before you put it anywhere. The short version: it is built for a **trusted private network**, it ships in a permissive header-trust mode by default, and you harden it with one flag and one token. This page covers the trust model, how to lock it down, where secrets live, and the one rule that matters more than the rest.

!!! danger "Do not expose RoboCo to the public internet"
    RoboCo is designed to run on a trusted LAN behind nginx, which is the only externally-exposed service. The default authentication mode trusts request headers (below), and one WebSocket stream is unauthenticated by design. Treat the whole system as you would a database: reachable only from your own network, never from the open internet.

## The default is header-trust mode

By default the API authenticates an agent purely from request headers: `X-Agent-ID`, `X-Agent-Role`, and optionally `X-Agent-Team`. There is no signed token required, which means **any client that can reach the API can claim any role — including `ceo`.** This is intentional for a single-operator deployment on a trusted network (it keeps the panel and `curl`-for-debugging simple), and the API logs a loud warning at startup whenever it's in this mode (`roboco/api/app.py`):

> Agent auth is in HEADER-TRUST mode … the API accepts X-Agent-Id / X-Agent-Role without verifying a signed token, so any client that can reach it may act as any role, including 'ceo'. Acceptable only on a trusted private network.

If your network boundary is solid, header-trust is fine. If it isn't — or you just want defense in depth — turn on secure mode.

## Hardening: secure mode + the panel token

Set `ROBOCO_AGENT_AUTH_REQUIRED=true` to require a signed token on every request. In this mode:

- Every request must carry an `X-Agent-Token` that is an HMAC of `agent_id:role:team` signed with `ROBOCO_AGENT_AUTH_SECRET`. The orchestrator mints this token for each agent at spawn, so agent traffic keeps working transparently.
- A missing token is rejected with `401`. A **forged role won't help**: even in header-trust mode, any token that *is* presented is still verified, so you can't bypass auth by supplying an invalid token. The HMAC comparison is constant-time.
- The **panel** authenticates as the CEO, so it needs a CEO token to keep working in secure mode. Mint it with `make panel-token`, which prints the signed token (and refuses with an error if `ROBOCO_AGENT_AUTH_SECRET` is unset, since an unsigned token would be useless). Configure the panel with that token.

!!! tip "Set the secret before you flip the flag"
    `ROBOCO_AGENT_AUTH_SECRET` is the signing key for every agent and panel token. Set it first, then `make panel-token`, then set `ROBOCO_AGENT_AUTH_REQUIRED=true` — otherwise agents and the panel can't produce valid tokens and every request 401s. If the secret is unset, token minting fails closed to a literal `UNSIGNED` value rather than producing a usable token.

See [API authentication](../api/auth.md) for the exact headers and how the panel and external clients pass them.

## Per-agent token binding

The token isn't a single shared bearer secret — it's **bound to the specific agent**. Because the HMAC covers `agent_id:role:team`, a token issued for `be-dev-1` as a `developer` is only valid for those exact header values. An agent on the Docker network can't take its own valid token and replay it claiming to be `main_pm` or `ceo`; the signature won't match the forged role, and the request is rejected. This is what stops one agent from escalating its own privileges by editing headers.

## Secrets handling

Two secrets are load-bearing, and both stay out of the repo:

| Secret | What it protects | Rule |
|--------|------------------|------|
| `ROBOCO_ENCRYPTION_KEY` | Fernet key that encrypts every per-project GitHub PAT at rest | Set once at install; **never change it** or all stored tokens become undecryptable. Back it up. |
| `ROBOCO_AGENT_AUTH_SECRET` | HMAC signing key for agent and panel tokens | Required for secure mode; keep it off the repo and out of logs. |

Project GitHub tokens are **encrypted the moment you save them** (with `ROBOCO_ENCRYPTION_KEY`) and stored in the `projects.git_token_encrypted` column. The **API never returns a token** — responses only carry a `has_git_token` boolean, so the panel can show whether a token is set but never its value. Neither secret belongs in the repository; put them in your `.env` (which is gitignored) or your secret manager.

## The PAT never enters an agent container

This is the guarantee that makes it safe to hand RoboCo a private repo: **your GitHub PAT is never present inside an agent container.** The orchestrator decrypts the token only at the moment of a git operation, injects it for that operation, and immediately after cloning **scrubs the token out of the clone's git config** — then verifies no token byte survives anywhere under `.git/`, destroying the workspace if one did. A compromised or misbehaving agent has nothing to exfiltrate, because the credential was never on its disk. The clone scrub is described in [Register a project](../get-started/first-project.md#what-happens-under-the-hood), and the broader sandboxing model in [the gateway](../company/agent-gateway.md).

## WebSocket auth caveat

The per-resource WebSocket streams are keyed to a resource, but the operator stream is not authenticated. **`/ws/system` carries no per-agent keying and no token** even when `ROBOCO_AGENT_AUTH_REQUIRED=true` — secure mode does not extend to it. It is **read-only** (it carries system events like rate-limit lifecycle and usage snapshots; it accepts nothing from the client), so the exposure is limited to a reader seeing system telemetry. It is, however, one more reason the system must sit on a trusted network: anyone who can open that socket can watch the operator stream.

## Next

→ [API authentication](../api/auth.md) for the header/token contract, [the gateway](../company/agent-gateway.md) for how agent capability is constrained, or back to [common issues](./common-issues.md).
