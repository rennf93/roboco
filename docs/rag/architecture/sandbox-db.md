# Sandboxed Dev DB/Redis/Mongo

## What It Is

A per-agent-spawn throwaway Postgres/Redis/Mongo set, provisioned as **sibling containers** to the agent container (never docker-in-agent — the docker socket/CLI stay structurally absent from agent images). Implemented in `roboco/runtime/sandbox.py` (`SandboxProvisioner`), wired into the orchestrator's spawn path.

It replaces — never coexists with — the legacy `_append_gate_env` behavior that hands an agent RoboCo's own production Postgres credentials so its `make quality` gate can run the DB-backed test suite instead of a hollow unit-only subset.

## The engine registry

The service set is a **pluggable engine registry**, not a hardcoded postgres+redis pair. `roboco/models/sandbox.py` defines a `SandboxEngine` ABC (image, container port, readiness probe, tmpfs paths, env emission) and the concrete engines:

- `_PostgresEngine` — `postgres:16-alpine`, tmpfs `/var/lib/postgresql/data`, `pg_isready` probe (60s), env `ROBOCO_TEST_DB_*` (incl. `ROBOCO_TEST_DB_ADMIN_DB`).
- `_RedisEngine` — `redis:8-alpine`, no tmpfs, `redis-cli -a … ping` probe (15s), env `ROBOCO_TEST_REDIS_*`.
- `_MongoEngine` — `mongo:8` (MongoDB ships no Alpine variant), tmpfs `/data/db`, `mongosh` ping against auth db `admin` (60s), env `ROBOCO_TEST_MONGO_*` (incl. `ROBOCO_TEST_MONGO_AUTH_DB=admin`).

`SANDBOX_ENGINES: dict[str, SandboxEngine]` registers them by name; `VALID_SANDBOX_SERVICES = frozenset(SANDBOX_ENGINES)` is the single source of truth the provisioner, the orchestrator's env injection, and `projects.sandbox_services` validation all consult. **Adding an engine is one class + one registry line** — no branch edited in the provisioner or the env emitter, which both iterate the registry.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_SANDBOX_DB_ENABLED` | `false` | Master switch. Off = spawning behaves exactly as today (the legacy prod-creds gate-env injection, itself gated by `ROBOCO_TOOLCHAIN_MATCH_ENABLED`). Panel-toggleable (Settings → Feature Flags). |

A second, per-project gate applies even when the flag is on: only a project with its `sandbox_services` column set (e.g. `["postgres", "redis", "mongo"]`; migration `057`, nullable/additive) participates. Every other project's spawns are byte-for-byte unaffected. Mongo rides the same column — no new migration, no new feature flag; it is just another registry entry.

## Provisioning

For an opted-in project's spawn, the orchestrator provisions each requested service through a single generic `_provision_engine` (no per-engine branch): it generates a random 32-hex-char password (`secrets.token_hex(16)`), pre-pulls the image, `docker run`s the sibling container, and polls the engine's readiness probe up to its deadline.

- **Postgres**: named `roboco-sandbox-pg-{agent_id}`, `--tmpfs /var/lib/postgresql/data` (no disk persistence), `--memory 512m --cpus 1`, user/db both `sandbox`, readiness via `pg_isready` up to 60s.
- **Redis**: named `roboco-sandbox-redis-{agent_id}`, same memory/cpu caps, `redis-server --requirepass`, readiness via `redis-cli -a … ping` up to 15s.
- **Mongo**: named `roboco-sandbox-mongo-{agent_id}`, `--tmpfs /data/db`, same memory/cpu caps, root user/db `sandbox`/`sandbox`, readiness via `mongosh` ping (auth db `admin`) up to 60s.

All are labeled `roboco.sandbox=1` plus an owner label (`roboco.sandbox.owner=roboco-agent-{agent_id}`) so the janitor can find them. A provisioning failure is **fail-loud**: the spawn is refused (`AgentReadinessError`) rather than starting an agent whose gate can't run against a broken DB, and any already-provisioned sibling is torn down before re-raising. A stale same-named sandbox left by a crash-missed teardown is pre-cleared before provisioning, so a leftover container can't collide with a fresh `docker run`.

### Image pre-pull

`_ensure_image` `docker image inspect`s the engine's image and, on absence, `docker pull`s it (300s timeout) **before** `docker run`. Without this a NAS cold pull would hit the 20s run timeout, get killed, and re-pull forever on every respawn. The inspect-then-pull runs per service per spawn, so an already-present image short-circuits in milliseconds.

## Injected environment

Instead of the legacy `ROBOCO_TEST_DB_*` pointing at RoboCo's own production Postgres, the sandbox's own host/port/user/password are injected. Env var names are preserved per engine so an existing project's conftest needs no change: `ROBOCO_TEST_DB_*` (postgres, incl. `ROBOCO_TEST_DB_ADMIN_DB`), `ROBOCO_TEST_REDIS_*` (redis), and `ROBOCO_TEST_MONGO_*` (mongo, incl. `ROBOCO_TEST_MONGO_AUTH_DB=admin`). The orchestrator's `_append_sandbox_env` is a single `cmd.extend(info.emit_env())` over the registry — a new engine's env lands with no orchestrator edit. It runs **instead of** `_append_gate_env` whenever a sandbox was provisioned for that spawn.

## Lifetime and teardown

A sandbox's lifetime tracks its owning agent container 1:1: torn down (`stop` → `kill` fallback → `rm -f`, all best-effort and idempotent) at every container-removal path. Teardown iterates **all** registered engines (`SANDBOX_ENGINES.values()`) — a mongo sandbox is reaped by the same path that reaps postgres/redis, with no per-engine teardown branch. An **orphan janitor** also runs at orchestrator startup and on each reaper tick: it lists every `roboco.sandbox=1` container, cross-references live agent containers, and removes any sandbox whose owner is gone.

The janitor has a **grace window** (`_JANITOR_GRACE_SECONDS`, 180s): a sandbox is provisioned *before* its agent container exists, so a sweep racing a mid-flight spawn would otherwise see "owner not live yet" and reap a fresh sandbox out from under a spawn still starting up. Owners provisioned within the grace window are skipped by that pass. The pre-spawn stale-clear (above) likewise never touches a just-provisioned sandbox.

## Related

- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/db-network-isolation.md` — the network-topology change this pairs with in a NAS deploy (agents structurally can't reach production Postgres/Redis at all; sandbox is the DB-needing project's alternative)
