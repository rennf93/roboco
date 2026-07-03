# DB Network Isolation

## What It Is

A compose-topology hardening: two user-defined Docker bridges instead of one. `roboco_default` carries the agent mesh (panel, nginx, ollama, every spawned agent container, and their sandbox DB/Redis sidecars — see `docs/rag/architecture/sandbox-db.md`). `roboco_data` carries **only** postgres + redis. The orchestrator is the sole multi-homed service (both networks) — every agent container structurally cannot resolve or TCP-reach `roboco-postgres:5432` / `roboco-redis:6379` at all. This matters because redis has no auth in this deployment: network membership *is* the containment, not a password.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_DB_NETWORK_ISOLATED` | `false` | **Not a panel feature flag** — it must travel with the compose file's `networks:` stanzas (it describes topology, not a runtime-toggleable behavior), so it is deliberately absent from `roboco/services/settings.py`'s `FEATURE_FLAGS`. Set `true` only by the compose files that actually carry the two-bridge topology. |

## What flipping it changes

`ROBOCO_DB_NETWORK_ISOLATED=true` suppresses the legacy `_append_gate_env` prod-creds injection (`roboco/runtime/orchestrator.py`) — the one that would otherwise hand an agent `ROBOCO_TEST_DB_HOST=roboco-postgres` credentials for a host it cannot reach. A connect timeout is worse than no credentials at all (the test suite's DB-reachability check skips cleanly on a fast refusal, but hangs on a dead-end timeout), so the flag makes that injection a no-op rather than let it happen and fail slow. Projects that need a real DB for their gate opt into the sandboxed dev DB/Redis instead (`docs/rag/architecture/sandbox-db.md`) — sandbox replaces, never coexists with, the prod-creds path.

## What is unaffected

Agent↔agent A2A (`:9000`), orchestrator→agent SDK polls, MCP→orchestrator (`:8000`), and host-published ports (`15432`/`16379`/`11435` in this topology) are unaffected — those don't route through `roboco_data`. `docker exec` / `docker inspect` paths ride the daemon socket, not the network, so they're untouched too.

## Related

- `docs/rag/architecture/config-reference.md` — env var table
- `docs/rag/architecture/sandbox-db.md` — the alternative for a DB-needing project under this topology
- `docs/rag/architecture/cloud-auth.md` — a separate, unrelated hardening that ships alongside this on the NAS composes
