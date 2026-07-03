# Sandboxed Dev DB/Redis

## What It Is

A per-agent-spawn throwaway Postgres/Redis pair, provisioned as **sibling containers** to the agent container (never docker-in-agent — the docker socket/CLI stay structurally absent from agent images). Implemented in `roboco/runtime/sandbox.py` (`SandboxProvisioner`), wired into the orchestrator's spawn path.

It replaces — never coexists with — the legacy `_append_gate_env` behavior that hands an agent RoboCo's own production Postgres credentials so its `make quality` gate can run the DB-backed test suite instead of a hollow unit-only subset.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_SANDBOX_DB_ENABLED` | `false` | Master switch. Off = spawning behaves exactly as today (the legacy prod-creds gate-env injection, itself gated by `ROBOCO_TOOLCHAIN_MATCH_ENABLED`). Panel-toggleable (Settings → Feature Flags). |

A second, per-project gate applies even when the flag is on: only a project with its `sandbox_services` column set (e.g. `["postgres", "redis"]`; migration `057`, nullable/additive) participates. Every other project's spawns are byte-for-byte unaffected.

## Provisioning

For an opted-in project's spawn, the orchestrator provisions before `docker run`:

- **Postgres**: `postgres:16-alpine`, named `roboco-sandbox-pg-{agent_id}`, `--tmpfs /var/lib/postgresql/data` (no disk persistence), `--memory 512m --cpus 1`, a random 32-hex-char password (`secrets.token_hex(16)`), user/db both `sandbox`. Readiness polled via `pg_isready` up to 60s.
- **Redis**: `redis:8-alpine`, named `roboco-sandbox-redis-{agent_id}`, same memory/cpu caps, `--requirepass` with its own random password. Readiness polled via `redis-cli ping` up to 15s.

Both are labeled `roboco.sandbox=1` plus an owner label (`roboco.sandbox.owner=roboco-agent-{agent_id}`) so the janitor can find them. A provisioning failure is **fail-loud**: the spawn is refused (`AgentReadinessError`) rather than starting an agent whose gate can't run against a broken DB. A stale same-named sandbox left by a crash-missed teardown is pre-cleared before provisioning, so a leftover container can't collide with a fresh `docker run`.

## Injected environment

Instead of the legacy `ROBOCO_TEST_DB_*` pointing at RoboCo's own production Postgres, the sandbox's own host/port/user/password are injected under the **same** `ROBOCO_TEST_DB_*` names (so an existing project's conftest needs no change) plus new `ROBOCO_TEST_REDIS_*` names. `_append_sandbox_env` runs **instead of** `_append_gate_env` whenever a sandbox was provisioned for that spawn.

## Lifetime and teardown

A sandbox's lifetime tracks its owning agent container 1:1: torn down (`stop` → `kill` fallback → `rm -f`, all best-effort and idempotent) at every container-removal path. An **orphan janitor** also runs at orchestrator startup and on each reaper tick: it lists every `roboco.sandbox=1` container, cross-references live agent containers, and removes any sandbox whose owner is gone.

The janitor has a **grace window** (`_JANITOR_GRACE_SECONDS`, 180s): a sandbox is provisioned *before* its agent container exists, so a sweep racing a mid-flight spawn would otherwise see "owner not live yet" and reap a fresh sandbox out from under a spawn still starting up. Owners provisioned within the grace window are skipped by that pass. The pre-spawn stale-clear (above) likewise never touches a just-provisioned sandbox.

## Related

- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/db-network-isolation.md` — the network-topology change this pairs with in a NAS deploy (agents structurally can't reach production Postgres/Redis at all; sandbox is the DB-needing project's alternative)
