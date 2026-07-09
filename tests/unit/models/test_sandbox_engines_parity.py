"""Engine registry / allowlist parity + per-engine internal consistency.

The valid-service allowlist is derived from the registry
(``VALID_SANDBOX_SERVICES = frozenset(SANDBOX_ENGINES)``), so the two must stay
in lockstep — a drift guard against adding an engine class without registering
it (or vice versa). Each engine's emitted env must also reference only the
connection fields it actually populates (no ``None`` leaking into an env value).
"""

from __future__ import annotations

from roboco.models.sandbox import (
    SANDBOX_ENGINES,
    VALID_SANDBOX_SERVICES,
    SandboxInfo,
)

_ENV_HOST_PREFIX = {
    "postgres": "ROBOCO_TEST_DB_HOST",
    "redis": "ROBOCO_TEST_REDIS_HOST",
    "mongo": "ROBOCO_TEST_MONGO_HOST",
}


def test_allowlist_matches_registry() -> None:
    assert frozenset(SANDBOX_ENGINES) == VALID_SANDBOX_SERVICES
    assert set(SANDBOX_ENGINES) == {"postgres", "redis", "mongo"}


def test_each_engine_has_unique_container_slug_and_image() -> None:
    slugs = {e.container_slug for e in SANDBOX_ENGINES.values()}
    images = {e.image for e in SANDBOX_ENGINES.values()}
    assert len(slugs) == len(SANDBOX_ENGINES)
    assert len(images) == len(SANDBOX_ENGINES)


def test_each_engine_emit_env_references_only_populated_fields() -> None:
    # An engine that does not set `user`/`database` must not emit a `None` value.
    for engine in SANDBOX_ENGINES.values():
        conn = engine.connection(host=f"h-{engine.name}", password="pw")
        env = " ".join(engine.emit_env(conn))
        assert "None" not in env, f"{engine.name} leaked None into env: {env}"


def test_sandbox_info_emit_env_aggregates_every_engine() -> None:
    services = {
        name: engine.connection(host=f"h-{name}", password="pw")
        for name, engine in SANDBOX_ENGINES.items()
    }
    flat = " ".join(SandboxInfo(services=services).emit_env())
    for name in SANDBOX_ENGINES:
        assert _ENV_HOST_PREFIX[name] in flat
