"""Per-engine sandbox specs — image, run args, readiness probe, env emission.

Pure (no docker): the provisioner in ``roboco/runtime/sandbox.py`` consumes the
registry and runs the containers. Lives in the models layer so
``roboco/models/project.py`` can derive the valid-service allowlist from it
without importing the runtime layer (no cycle). Adding an engine = one class +
one registry line — no branch to edit in the provisioner or the env emitter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxConnection:
    """Connection info for one provisioned sandbox service.

    ``user`` / ``database`` are ``None`` for engines that don't expose them
    (redis). For postgres ``database`` is the admin db; for mongo it is the
    auth db (``admin``).
    """

    host: str
    port: int
    password: str
    user: str | None = None
    database: str | None = None


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
        """``docker exec`` probe cmd; rc 0 means ready.

        Some engines ignore ``password`` (probe without auth); see ``run_command``.
        """

    @abstractmethod
    def connection(self, host: str, password: str) -> SandboxConnection:
        """Connection info for the agent, given the container host + password."""

    @abstractmethod
    def emit_env(self, conn: SandboxConnection) -> list[str]:
        """``-e KEY=VAL`` args injecting this service's creds into the agent."""


class _PostgresEngine(SandboxEngine):
    name = "postgres"
    image = "postgres:16-alpine"
    container_port = 5432
    ready_deadline = 60.0
    tmpfs = ("/var/lib/postgresql/data",)
    container_slug = "pg"

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

    def connection(self, host: str, password: str) -> SandboxConnection:
        return SandboxConnection(
            host=host,
            port=self.container_port,
            password=password,
            user="sandbox",
            database="sandbox",
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
    container_port = 6379
    ready_deadline = 15.0
    tmpfs: tuple[str, ...] = ()
    container_slug = "redis"

    def run_env(self, _password: str) -> list[str]:
        return []

    def run_command(self, password: str) -> list[str]:
        return ["redis-server", "--requirepass", password]

    def ready_probe(self, password: str) -> list[str]:
        return ["redis-cli", "-a", password, "ping"]

    def connection(self, host: str, password: str) -> SandboxConnection:
        return SandboxConnection(host=host, port=self.container_port, password=password)

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
    image = "mongo:8-alpine"
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
            "-e",
            "MONGO_INITDB_DATABASE=sandbox",
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

    def connection(self, host: str, password: str) -> SandboxConnection:
        return SandboxConnection(
            host=host,
            port=self.container_port,
            password=password,
            user="sandbox",
            database="admin",
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
