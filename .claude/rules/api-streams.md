---
paths:
  - "roboco/api/websocket*.py"
  - "panel/src/hooks/**"
---

# WebSocket streams

Migrated from the root CLAUDE.md so it loads only when the matching files are touched.

The orchestrator exposes WebSocket endpoints under `/ws` (router in `roboco/api/websocket.py`, `ConnectionManager` + `broadcast_*` helpers):

| Endpoint | Purpose |
|----------|---------|
| `/ws/agents/{id}`, `/ws/notifications/{id}` | Per-resource live streams |
| `/ws/system` | Operator/system-wide stream (no per-agent keying) - the rate-limit lifecycle (`RATE_LIMIT_HIT` / `RATE_LIMIT_LIFTED`), live usage (`USAGE_SNAPSHOT`, pushed to the usage dashboard), and A2A message events (`a2a.message` frames) |

Server-side events reach these sockets through `roboco/api/websocket_bridge.py`, which subscribes to the `StreamEventBus` and forwards each event to the matching connections. To add a new live event: define an `EventType` (dotted value), publish it to the bus, add a `_handle_*` forwarder in `websocket_bridge`, and consume it on the panel via the `useWebSocket("/<endpoint>", …)` hook - do not stand up a parallel endpoint or client stack. `A2A_MESSAGE_SENT` is the worked example: `A2AService.send` publishes it (excerpt-capped payload), the bridge forwards it to `/ws/system` as an `a2a.message` frame, and the panel's `useA2ALiveStream` hook (a second consumer of that same shared `/ws/system` connection) consumes it to invalidate-on-frame.

