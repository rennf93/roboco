# Sandboxed Dev DB/Redis/Mongo

## What It Is

A throwaway Postgres/Redis/Mongo set, provisioned as **sibling containers** to the agent container (never docker-in-agent — the docker socket/CLI stay structurally absent from agent images). Implemented in `roboco/runtime/sandbox.py` (`SandboxProvisioner`) and `AgentOrchestrator.ensure_sandbox`.

**On-demand since 2026-07-08**: a sandbox is provisioned only when an agent actually asks for one, via the `request_sandbox` do-verb (developer + QA roles) — not eagerly at spawn. See "The `request_sandbox` verb" below; the design rationale lives in `docs/internal/specs/2026-07-08-sandbox-on-demand.md`.

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
| `ROBOCO_SANDBOX_DB_ENABLED` | `false` | Master switch. Off = spawning behaves exactly as today (the legacy prod-creds gate-env injection, itself gated by `ROBOCO_TOOLCHAIN_MATCH_ENABLED`), and `request_sandbox` refuses. Panel-toggleable (Settings → Feature Flags). |

A second, per-project gate applies even when the flag is on: only a project with its `sandbox_services` column set (e.g. `["postgres", "redis", "mongo"]`; migration `057`, nullable/additive) participates. Every other project's spawns are byte-for-byte unaffected. Mongo rides the same column — no new migration, no new feature flag; it is just another registry entry. `request_sandbox` may request any subset of the opted-in set (or omit `services` for the whole set); anything outside it is rejected naming the allowed set.

## The `request_sandbox` verb

Provisioning is **on-demand**: nothing is provisioned at spawn. A developer or QA agent calls the `request_sandbox` content tool (on `roboco-do`) when it actually needs a sandboxed DB, and the orchestrator provisions it inline. Wiring: `roboco/api/schemas/v1/do.py` `RequestSandboxRequest` → `POST /api/v1/do/request_sandbox` → `ContentActions.request_sandbox` (`roboco/services/gateway/content_actions.py`) → `AgentOrchestrator.ensure_sandbox` → `roboco/mcp/do_server.py`'s `request_sandbox()` tool (1080s timeout — `ensure_sandbox` always provisions the project's full opted-in set on first call, so an all-three-engines-cold first request is the norm the timeout must cover, not a rare edge case).

```python
request_sandbox(services: list[str] | None = None)
```

`services` omitted means the project's whole opted-in set. Guards fire in order, each with a clean `invalid_state` envelope + `remediate`:

1. `ROBOCO_SANDBOX_DB_ENABLED` off → refused before any DB lookup.
2. No active, project-bound task (agent hasn't `give_me_work`'d) → refused.
3. Project has no `sandbox_services` opted in → refused.
4. A requested service outside the project's opted set → refused, remediate **names the allowed set**.
5. Orchestrator handle unavailable (e.g. mid-restart) → refused, but **retryable** — the one guard that isn't a permanent no.

Only past all five does it call `ensure_sandbox` and provision. A genuine provisioning failure (image pull, readiness timeout) also surfaces as a retryable `invalid_state`, never a spawn refusal — sandbox trouble can no longer block a spawn or an agent's turn.

On success, creds return in the ok-envelope's `evidence`, one entry per service — no env injection, no schema change:

```json
{
  "postgres": {
    "host": "roboco-sandbox-pg-be-dev-1",
    "port": 5432,
    "user": "sandbox",
    "password": "<random>",
    "database": "sandbox",
    "env": {
      "ROBOCO_TEST_DB_HOST": "roboco-sandbox-pg-be-dev-1",
      "ROBOCO_TEST_DB_PORT": "5432",
      "ROBOCO_TEST_DB_USER": "sandbox",
      "ROBOCO_TEST_DB_PASSWORD": "<random>",
      "ROBOCO_TEST_DB_ADMIN_DB": "sandbox"
    }
  }
}
```

The `env` sub-dict (`SandboxInfo.as_payload()`, `roboco/models/sandbox.py`) carries the exact same variable names the legacy env-injection path used, so an agent can `export` them verbatim for gate tooling that reads `ROBOCO_TEST_*`. Networking needs no extra step: sandboxes join `roboco_default` at `docker run`, the same network every agent is on, so DNS resolves the moment the sibling starts.

`ensure_sandbox` always provisions the project's **whole opted-in set** on first call, regardless of what a given `request_sandbox` call named — so calling it again for any subset or superset of that opted set is a guaranteed cache hit (same creds, no docker calls), and a live container is never torn down mid-session by a later, broader request. `services` only scopes what comes back in this call's `evidence`; the response payload is filtered down to that subset even though the full set was provisioned. A cache hit is also re-verified live (`SandboxProvisioner.is_live`) before being trusted — a container OOM-killed or removed out-of-band evicts the stale entry and triggers a fresh full-set provision with new creds. Concurrent calls for the same agent (e.g. a client timeout + retry) are serialized behind a per-agent-slug `asyncio.Lock` so they can't race `provision()`/`teardown()` against each other. `ensure_sandbox` is always called with the **caller's own** authenticated agent slug — a caller can never reach another agent's sandbox.

Only `developer` and `qa` roles carry `request_sandbox` in their spawn manifest (`roboco/services/gateway/role_config.py` `_DEV_DO` / `_QA_DO`) — the DB-needing gate roles. It is carried unconditionally on those manifests (declarative); the real gating is the project opt-in check inside the verb itself.

## Provisioning

`ensure_sandbox(agent_slug, requested, opted)` provisions `requested | opted` (in practice the project's whole opted-in set) through a single generic `_provision_engine` (no per-engine branch): it generates a random 32-hex-char password (`secrets.token_hex(16)`), pre-pulls the image, `docker run`s the sibling container, and polls the engine's readiness probe up to its deadline.

- **Postgres**: named `roboco-sandbox-pg-{agent_id}`, `--tmpfs /var/lib/postgresql/data` (no disk persistence), `--memory 512m --cpus 1`, user/db both `sandbox`, readiness via `pg_isready` up to 60s.
- **Redis**: named `roboco-sandbox-redis-{agent_id}`, same memory/cpu caps, `redis-server --requirepass`, readiness via `redis-cli -a … ping` up to 15s.
- **Mongo**: named `roboco-sandbox-mongo-{agent_id}`, `--tmpfs /data/db`, same memory/cpu caps, root user/db `sandbox`/`sandbox`, readiness via `mongosh` ping (auth db `admin`) up to 60s.

All are labeled `roboco.sandbox=1` plus an owner label (`roboco.sandbox.owner=roboco-agent-{agent_id}`) so the janitor can find them. A stale same-named sandbox left by a crash-missed teardown is pre-cleared before provisioning, so a leftover container can't collide with a fresh `docker run`. A provisioning failure now surfaces as a retryable envelope on the verb (see above) rather than refusing anything — there is no spawn to refuse, since the sandbox is requested well after the agent is already running.

### Image pre-pull

`_ensure_image` `docker image inspect`s the engine's image and, on absence, `docker pull`s it (300s timeout) **before** `docker run`. Without this a NAS cold pull would hit the 20s run timeout, get killed, and re-pull forever on every retry. The inspect-then-pull runs per service per call, so an already-present image short-circuits in milliseconds.

## Spawn-time availability probe

Spawn itself no longer provisions anything. For an opted-in project, `AgentOrchestrator._sandbox_available_services` is a cheap DB lookup (best-effort — a hiccup degrades to "no sandbox" rather than blocking the spawn) that returns the project's opted-in service list, and the spawn path injects a marker env `ROBOCO_SANDBOX_SERVICES_AVAILABLE=postgres,redis` (never creds) plus one line in the agent's session briefing naming `request_sandbox()` explicitly — cheap and kills a discovery failure mode where an agent doesn't know the tool exists. `AgentConfig.sandbox_info` and the old eager `_append_sandbox_env` are gone; `_append_sandbox_marker_env` runs **instead of** `_append_gate_env` for an opted-in project's spawn (never both).

## Lifetime and teardown

A sandbox no longer only dies with its container: `AgentOrchestrator.release_sandbox(agent_slug)` tears it down at the end of the caller's own engagement with its work — the Choreographer calls it (best-effort, never failing the verb) on the SUCCESSFUL exit of `i_am_done`, `unclaim`, `i_am_idle`, `pass_review`/`fail_review`, and `i_documented`, so an agent that finishes then idles or picks up unrelated work doesn't leave a sidecar running for the rest of its session. `release_sandbox` is a fast no-op for the common case of no cached sandbox (a single dict check, before any lock or docker call) and otherwise reuses the same teardown + cache-eviction pairing as container removal. A sandbox's lifetime STILL tracks its owning agent container 1:1 as the backstop: torn down (`stop` → `kill` fallback → `rm -f`, all best-effort and idempotent) at every container-removal path. Teardown iterates **all** registered engines (`SANDBOX_ENGINES.values()`) — a mongo sandbox is reaped by the same path that reaps postgres/redis, with no per-engine teardown branch. An **orphan janitor** also runs at orchestrator startup and on each reaper tick: it lists every `roboco.sandbox=1` container, cross-references live agent containers, and removes any sandbox whose owner is gone.

The janitor has a **grace window** (`_JANITOR_GRACE_SECONDS`, 180s): a sweep racing a mid-flight `request_sandbox` call would otherwise see "owner not live yet" and reap a sandbox out from under a request still in progress. Owners provisioned within the grace window are skipped by that pass. The pre-spawn stale-clear likewise never touches a sandbox just requested via the verb.

**Creds cache.** `AgentOrchestrator._sandbox_info` (agent slug → last-provisioned `SandboxInfo`, always covering the project's full opted-in set) is what makes repeat `request_sandbox` calls cheap. It is evicted at every teardown path and by the janitor sweep whenever it reaps an orphan — so a torn-down sandbox's cached creds never outlive the container — and also on a failed on-demand liveness check at cache-hit time (`SandboxProvisioner.is_live`), which catches a container that died without going through any of those explicit teardown paths (OOM-killed, manually removed). A per-agent-slug `asyncio.Lock` (`AgentOrchestrator._sandbox_locks`) wraps the whole check-cache → provision → store section so two concurrent `ensure_sandbox` calls for one agent can't race each other's `provision()`/`teardown()`. **Known ceiling:** the cache is in-memory only; an orchestrator restart forgets it. The next `request_sandbox` call re-provisions (the pre-clear tears down any still-running stale container) and returns fresh creds — agents already treat creds as session-scoped, and a connection to the torn-down sandbox simply fails loudly rather than silently.

## Related

- `docs/internal/specs/2026-07-08-sandbox-on-demand.md` — the on-demand design spec and rationale
- `docs/rag/tools/task-tools.md` — `request_sandbox` alongside the rest of the dev/QA verb surface
- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/db-network-isolation.md` — the network-topology change this pairs with in a NAS deploy (agents structurally can't reach production Postgres/Redis at all; sandbox is the DB-needing project's alternative)
