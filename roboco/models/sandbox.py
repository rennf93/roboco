"""Per-engine sandbox specs — image, run args, readiness probe, env emission.

Pure (no docker): the provisioner in ``roboco/runtime/sandbox.py`` consumes the
registry and runs the containers. Lives in the models layer so
``roboco/models/project.py`` can derive the valid-service allowlist from it
without importing the runtime layer (no cycle). Adding an engine = one class +
one registry line — no branch to edit in the provisioner or the env emitter.

Extensions/modules are activated *post-ready* by a ``docker exec`` enable step
(the provisioner runs it after the base readiness probe passes), never via
bind-mounts or initdb scripts. The allowlists below are the *only* extensions /
modules the system will ever activate — they are the security containment (an
agent must not be able to ``CREATE EXTENSION plpython3u``, a superuser-RCE
vector). See ``docs/internal/specs/2026-07-13-sandbox-extensions-on-the-fly.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Per-family allowlists — the ONLY extensions/modules a sandbox may activate.
# Adding one = add it here + ship it in the kitchen-sink image. An untrusted /
# superuser-language extension (plpython3u, plperlu, …) must NEVER appear here.
SANDBOX_PG_EXTENSIONS: frozenset[str] = frozenset(
    {"vector", "postgis", "pg_trgm", "citext", "uuid-ossp"}
)
SANDBOX_REDIS_MODULES: frozenset[str] = frozenset({"search", "json", "bloom"})

# Friendly module key -> the .so path inside the redis-stack image.
SANDBOX_REDIS_MODULE_SO: dict[str, str] = {
    "search": "/opt/redis-stack/lib/redisearch.so",
    "json": "/opt/redis-stack/lib/rejson.so",
    "bloom": "/opt/redis-stack/lib/redisbloom.so",
}
# Friendly module key -> the name redis reports in MODULE LIST (for verification).
SANDBOX_REDIS_MODULE_NAME: dict[str, str] = {
    "search": "search",
    "json": "ReJSON",
    "bloom": "bf",
}

# The allowed features per engine family, for the provisioner's allowlist guard
# and for project-field validation. An engine with no activatable features omits
# itself from the map (mongo).
SANDBOX_ENGINE_FEATURES: dict[str, frozenset[str]] = {
    "postgres": SANDBOX_PG_EXTENSIONS,
    "redis": SANDBOX_REDIS_MODULES,
}


@dataclass(frozen=True)
class SandboxConnection:
    """Connection info for one provisioned sandbox service.

    ``user`` / ``database`` are ``None`` for engines that don't expose them
    (redis). For postgres ``database`` is the admin db; for mongo it is the
    auth db (``admin``). ``features`` records the extensions/modules activated
    in this container — for the cache-subset check and the evidence payload.
    """

    host: str
    port: int
    password: str
    user: str | None = None
    database: str | None = None
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxInfo:
    """Sandbox container(s) provisioned for one agent spawn (services opted-in)."""

    services: dict[str, SandboxConnection]

    def emit_env(self) -> list[str]:
        """Flattened ``-e KEY=VAL`` args for the agent container's ``docker run``.

        Iterates the engines so the orchestrator stays registry-agnostic.
        """
        env: list[str] = []
        for name, conn in self.services.items():
            env.extend(SANDBOX_ENGINES[name].emit_env(conn))
        return env

    def as_payload(self) -> dict[str, dict[str, Any]]:
        """Per-service creds dict for the ``request_sandbox`` verb's evidence.

        Same variable names as ``emit_env`` (docs/env parity) but keyed for
        direct agent consumption (JSON) rather than ``docker run -e`` args.
        ``available_extensions``/``available_modules`` surfaces what was
        activated so the agent doesn't guess.
        """
        out: dict[str, dict[str, Any]] = {}
        for name, conn in self.services.items():
            args = SANDBOX_ENGINES[name].emit_env(conn)
            env = dict(pair.split("=", 1) for pair in args[1::2])
            payload: dict[str, Any] = {
                "host": conn.host,
                "port": conn.port,
                "user": conn.user,
                "password": conn.password,
                "database": conn.database,
                "env": env,
            }
            if conn.features:
                key = (
                    "available_extensions"
                    if name == "postgres"
                    else "available_modules"
                )
                payload[key] = list(conn.features)
            out[name] = payload
        return out


class SandboxEngine(ABC):
    """One sandbox service kind: how to run it, probe it, and feed its creds."""

    name: str
    image: str
    container_port: int
    ready_deadline: float  # mutable — tests monkeypatch the instance attr
    tmpfs: tuple[str, ...]
    container_slug: str

    def container_name(self, agent_id: str) -> str:
        return f"roboco-sandbox-{self.container_slug}-{agent_id}"

    def image_for(self, _features: list[str]) -> str:
        """Image to run for this provision, given the features requested.

        Default: the bare ``image`` for every provision — bare projects pull
        only the light upstream image (no heavier kitchen-sink pull). An engine
        whose features need files the bare image lacks (pg extensions, redis
        modules) overrides this to return the kitchen-sink image when features
        is non-empty. The provisioner calls this (not ``image`` directly) so the
        bare path stays byte-for-byte unchanged.
        """
        return self.image

    @abstractmethod
    def run_env(self, password: str) -> list[str]:
        """``-e KEY=VAL`` pairs baked into the sandbox container's ``docker run``."""

    @abstractmethod
    def run_command(self, password: str) -> list[str]:
        """Args after the image (e.g. ``redis-server --requirepass pw``).

        Empty for engines that need no command tail. Some engines ignore
        ``password`` here; the param stays on the ABC so a password-bearing
        engine needn't special-case its call.
        """

    @abstractmethod
    def ready_probe(self, password: str) -> list[str]:
        """``docker exec`` probe cmd; rc 0 means the base service is up.

        Runs BEFORE ``enable_step``. Some engines ignore ``password`` (probe
        without auth); see ``run_command``.
        """

    @abstractmethod
    def enable_step(self, password: str, features: list[str]) -> list[list[str]] | None:
        """``docker exec`` argvs that activate the requested features in a
        running container (one inner argv per exec; the provisioner runs each).
        ``None`` when there is nothing to enable (no features, or an engine
        like mongo with no activatable features). The provisioner has already
        allowlist-validated ``features`` before this runs, so every name here
        is a known-safe identifier — the allowlist is the injection guard.
        """

    @abstractmethod
    def verify_step(self, password: str, features: list[str]) -> list[str] | None:
        """One ``docker exec`` argv whose output confirms the features are
        actually present, or ``None`` when there is nothing to verify. Runs
        after ``enable_step``; interpreted by ``verify_ok``."""

    @abstractmethod
    def verify_ok(self, features: list[str], stdout: bytes) -> bool:
        """Interpret ``verify_step``'s stdout: True iff every feature is
        confirmed present. A failed ``CREATE EXTENSION`` / ``MODULE LOAD``
        (e.g. the image is missing the extension files) surfaces here, not
        three steps later as a confusing query error."""

    @abstractmethod
    def connection(
        self, host: str, password: str, features: tuple[str, ...] = ()
    ) -> SandboxConnection:
        """Connection info for the agent, given the container host, password,
        and the features activated in this container."""

    @abstractmethod
    def emit_env(self, conn: SandboxConnection) -> list[str]:
        """``-e KEY=VAL`` args injecting this service's creds into the agent."""


class _PostgresEngine(SandboxEngine):
    name = "postgres"
    image = "postgres:16-alpine"
    # Kitchen-sink image: pgvector base + postgis + contrib (pg_trgm/citext/
    # uuid-ossp). Only pulled when a venture requests extensions — bare
    # provisions stay on the light `image` above (no heavier pull).
    kitchen_sink_image = "roboco-sandbox-pg:latest"
    container_port = 5432
    ready_deadline = 60.0
    tmpfs = ("/var/lib/postgresql/data",)
    container_slug = "pg"

    def image_for(self, features: list[str]) -> str:
        return self.kitchen_sink_image if features else self.image

    def run_env(self, password: str) -> list[str]:
        return [
            "-e",
            "POSTGRES_USER=sandbox",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=sandbox",
        ]

    def run_command(self, _password: str) -> list[str]:
        return []

    def ready_probe(self, _password: str) -> list[str]:
        return ["pg_isready", "-U", "sandbox"]

    def enable_step(
        self, _password: str, features: list[str]
    ) -> list[list[str]] | None:
        if not features:
            return None
        # Every name is allowlist-validated upstream; safe to interpolate as an
        # identifier. IF NOT EXISTS so a re-provision of a warm container is a
        # no-op rather than a not-error-but-noisy notice.
        stmts = "; ".join(f"CREATE EXTENSION IF NOT EXISTS {f}" for f in features)
        return [
            [
                "psql",
                "-U",
                "sandbox",
                "-d",
                "sandbox",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                stmts,
            ]
        ]

    def verify_step(self, _password: str, features: list[str]) -> list[str] | None:
        if not features:
            return None
        # Static query — no interpolation, so no string-built-SQL surface; the
        # feature membership check happens in verify_ok against the installed
        # extname set. Every requested name is allowlist-validated upstream.
        return [
            "psql",
            "-U",
            "sandbox",
            "-d",
            "sandbox",
            "-tAc",
            "SELECT extname FROM pg_extension",
        ]

    def verify_ok(self, features: list[str], stdout: bytes) -> bool:
        if not features:
            return True
        try:
            installed = {
                line.strip() for line in stdout.decode().splitlines() if line.strip()
            }
        except (ValueError, AttributeError):
            return False
        return set(features).issubset(installed)

    def connection(
        self, host: str, password: str, features: tuple[str, ...] = ()
    ) -> SandboxConnection:
        return SandboxConnection(
            host=host,
            port=self.container_port,
            password=password,
            user="sandbox",
            database="sandbox",
            features=features,
        )

    def emit_env(self, conn: SandboxConnection) -> list[str]:
        return [
            "-e",
            f"ROBOCO_TEST_DB_HOST={conn.host}",
            "-e",
            f"ROBOCO_TEST_DB_PORT={conn.port}",
            "-e",
            f"ROBOCO_TEST_DB_USER={conn.user}",
            "-e",
            f"ROBOCO_TEST_DB_PASSWORD={conn.password}",
            "-e",
            f"ROBOCO_TEST_DB_ADMIN_DB={conn.database}",
        ]


class _RedisEngine(SandboxEngine):
    name = "redis"
    image = "redis:8-alpine"
    # redis-stack-server ships search/json/bloom as loadable-but-unloaded
    # modules — no custom build. Headless (-server) variant: no RedisInsight
    # web UI, appropriate for an ephemeral dev sandbox. Only pulled when a
    # venture requests modules; bare provisions stay on the light `image`.
    kitchen_sink_image = "redis/redis-stack-server:latest"
    container_port = 6379
    ready_deadline = 15.0
    tmpfs: tuple[str, ...] = ()
    container_slug = "redis"

    def image_for(self, features: list[str]) -> str:
        return self.kitchen_sink_image if features else self.image

    def run_env(self, _password: str) -> list[str]:
        return []

    def run_command(self, password: str) -> list[str]:
        return ["redis-server", "--requirepass", password]

    def ready_probe(self, password: str) -> list[str]:
        return ["redis-cli", "-a", password, "ping"]

    def enable_step(self, password: str, features: list[str]) -> list[list[str]] | None:
        if not features:
            return None
        # One MODULE LOAD per module — redis-cli loads one module per call. The
        # password is a per-sandbox ephemeral token (not a real secret), mirroring
        # run_command's own --requirepass usage.
        return [
            ["redis-cli", "-a", password, "--no-auth-warning", "MODULE", "LOAD", so]
            for so in (SANDBOX_REDIS_MODULE_SO[f] for f in features)
        ]

    def verify_step(self, password: str, features: list[str]) -> list[str] | None:
        if not features:
            return None
        return [
            "redis-cli",
            "-a",
            password,
            "--no-auth-warning",
            "--raw",
            "MODULE",
            "LIST",
        ]

    def verify_ok(self, features: list[str], stdout: bytes) -> bool:
        if not features:
            return True
        tokens = set(stdout.decode(errors="replace").split())
        return all(SANDBOX_REDIS_MODULE_NAME[f] in tokens for f in features)

    def connection(
        self, host: str, password: str, features: tuple[str, ...] = ()
    ) -> SandboxConnection:
        return SandboxConnection(
            host=host, port=self.container_port, password=password, features=features
        )

    def emit_env(self, conn: SandboxConnection) -> list[str]:
        return [
            "-e",
            f"ROBOCO_TEST_REDIS_HOST={conn.host}",
            "-e",
            f"ROBOCO_TEST_REDIS_PORT={conn.port}",
            "-e",
            f"ROBOCO_TEST_REDIS_PASSWORD={conn.password}",
        ]


class _MongoEngine(SandboxEngine):
    name = "mongo"
    image = "mongo:8"
    container_port = 27017
    ready_deadline = 60.0
    tmpfs = ("/data/db",)
    container_slug = "mongo"

    def run_env(self, password: str) -> list[str]:
        return [
            "-e",
            "MONGO_INITDB_ROOT_USERNAME=sandbox",
            "-e",
            f"MONGO_INITDB_ROOT_PASSWORD={password}",
        ]

    def run_command(self, _password: str) -> list[str]:
        return []

    def ready_probe(self, password: str) -> list[str]:
        return [
            "mongosh",
            "--quiet",
            "-u",
            "sandbox",
            "-p",
            password,
            "--authenticationDatabase",
            "admin",
            "--eval",
            "db.runCommand({ping:1}).ok",
        ]

    def enable_step(
        self, _password: str, _features: list[str]
    ) -> list[list[str]] | None:
        # The mongo server is batteries-included (text search, change streams
        # built in); nothing to activate post-ready.
        return None

    def verify_step(self, _password: str, _features: list[str]) -> list[str] | None:
        return None

    def verify_ok(self, features: list[str], _stdout: bytes) -> bool:
        return not features

    def connection(
        self, host: str, password: str, features: tuple[str, ...] = ()
    ) -> SandboxConnection:
        return SandboxConnection(
            host=host,
            port=self.container_port,
            password=password,
            user="sandbox",
            database="admin",
            features=features,
        )

    def emit_env(self, conn: SandboxConnection) -> list[str]:
        return [
            "-e",
            f"ROBOCO_TEST_MONGO_HOST={conn.host}",
            "-e",
            f"ROBOCO_TEST_MONGO_PORT={conn.port}",
            "-e",
            f"ROBOCO_TEST_MONGO_USER={conn.user}",
            "-e",
            f"ROBOCO_TEST_MONGO_PASSWORD={conn.password}",
            "-e",
            f"ROBOCO_TEST_MONGO_AUTH_DB={conn.database}",
        ]


SANDBOX_ENGINES: dict[str, SandboxEngine] = {
    e.name: e for e in (_PostgresEngine(), _RedisEngine(), _MongoEngine())
}
VALID_SANDBOX_SERVICES: frozenset[str] = frozenset(SANDBOX_ENGINES)
