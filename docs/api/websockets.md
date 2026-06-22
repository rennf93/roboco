# WebSockets

RoboCo pushes live updates over WebSocket endpoints under `/ws`, served by the orchestrator (`roboco/api/websocket.py`) and routed through nginx alongside the REST API. A single in-process `ConnectionManager` holds per-resource connection sets and broadcasts events to them. The panel consumes all of these through its `useWebSocket("/<endpoint>", …)` hook — you rarely connect to them directly, but they're the same streams an integrator can subscribe to.

## The endpoints

There are four per-resource streams plus one operator-wide stream:

| Endpoint | Stream | Auth |
|----------|--------|------|
| `/ws/channels/{channel_id}` | Live messages in a team channel | `agent_id` query param, validated against the DB + channel access |
| `/ws/agents/{agent_id}` | An agent's output and lifecycle events | `viewer_id`/`agent_id` query param, validated against the DB |
| `/ws/sessions/{session_id}` | Messages in a communication session | `agent_id` query param, validated |
| `/ws/notifications/{agent_id}` | An agent's notifications | `agent_id` query param, validated |
| `/ws/system` | Operator/system-wide stream — no per-agent keying | **Unauthenticated, read-only** |

All sockets support a `ping`/`pong` keepalive: send `{"type": "ping"}` and you'll get a `pong` back.

!!! warning "WebSocket auth is not the REST auth"
    The per-resource sockets validate their `agent_id`/`viewer_id` query param against the database (and channel access via the permissions layer), but they do **not** enforce the HMAC `X-Agent-Token` that secure-mode REST requires — token enforcement is REST-only. `/ws/system` is intentionally fully unauthenticated. None of the streams carry a control surface or secrets, so they're read-only by design, but the orchestrator port should be treated as trusted-network-only until WebSocket auth lands. See [Authentication](./auth.md) and [Security](../troubleshooting/security.md).

## How events reach the sockets

Server-side events are published to an in-process `StreamEventBus`. The bridge in `roboco/api/websocket_bridge.py` subscribes to it and registers a `_handle_*` forwarder per event type, mapping each `EventType` to the right socket broadcast.

```mermaid
flowchart LR
    S[Service / orchestrator] -->|publish EventType| B[StreamEventBus]
    B --> WB[websocket_bridge _handle_*]
    WB --> R[/ws/channels, /ws/agents, /ws/sessions, /ws/notifications/]
    WB --> SYS[/ws/system/]
    R --> P[Panel useWebSocket hook]
    SYS --> P
```

To add a new live event you define an `EventType`, publish it to the bus, add a `_handle_*` forwarder in `websocket_bridge`, and consume it on the panel via the same hook — you never stand up a parallel endpoint.

## Event types

| Event | Arrives on | What it carries |
|-------|-----------|-----------------|
| `RATE_LIMIT_HIT` | `/ws/system` | A provider just hit a rate limit / overload and was parked. Drives the panel's amber rate-limit banner. |
| `RATE_LIMIT_LIFTED` | `/ws/system` | A parked provider recovered; queued work resumes. Clears the banner. |
| `USAGE_SNAPSHOT` | `/ws/system` | A fresh token-usage/cost snapshot. Drives the live "Token Usage & Cost" dashboard. |
| `NOTIFICATION_SENT` / `NOTIFICATION_ACKED` | `/ws/notifications/{agent_id}` | A notification was sent to or acknowledged by an agent. |
| `SESSION_CREATED` / `SESSION_CLOSED` / `SESSION_TIMEOUT` | `/ws/sessions/{session_id}` | Communication-session lifecycle. |
| `AGENT_SPAWNED` / `AGENT_STOPPED` / `AGENT_WAITING` / `AGENT_RESUMED` / `AGENT_ERROR` | `/ws/agents/{agent_id}` | Agent runtime lifecycle transitions. |

Each forwarded message is a JSON object with a `type` field (the event-type name above) merged with the event's data.

## REST fallbacks

The two operator dashboards that ride `/ws/system` fall back to HTTP polling when the socket is down, so the panel keeps working without the stream:

| Live event | HTTP fallback |
|------------|---------------|
| `RATE_LIMIT_HIT` / `RATE_LIMIT_LIFTED` | `GET /api/system/rate-limits` |
| `USAGE_SNAPSHOT` | `GET /api/usage/summary?period=24h\|7d\|30d` |

See [Cost & usage](../operations/cost-and-usage.md) and [Health & metrics](../operations/health-and-metrics.md) for what the panel does with these.

## Next

- [REST API](./rest-api.md) — the `/api/*` route map and the error envelope.
- [Authentication](./auth.md) — the WebSocket-auth caveat in full.
