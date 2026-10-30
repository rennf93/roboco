"""RoboCo's Docker gate: the orchestrator reaches Docker only through here.

The orchestrator drives Docker through the ``docker`` CLI
(``roboco/utils/docker_subcommands.py`` lists every subcommand it runs).
Holding the raw ``/var/run/docker.sock`` is root on the host, so the gate sits
between them: it listens on an owner-only Unix socket that the orchestrator's
``DOCKER_HOST`` points at, checks every Engine API request against the calls
RoboCo makes, and forwards only those to the real socket.

Stage 1 checks the method and the path, plus the container name on
``POST /containers/create``. Stage 2 checks what a call asks for: the JSON
body of a container create (image, privileges, namespaces, binds, user) and
of an exec, which image a pull may fetch, that a call naming a container by id
reaches one of RoboCo's, and, on every start, the binds again, on disk.

Binds are checked on disk because dockerd follows symlinks in a bind source
and resolves them when the container starts, not when it is created. The
gate has every bind root mounted read-only at the same path, and refuses a
bind whose path goes through a symlink below its root, at create and again at
start. What it cannot close is the moment between its check at start and
dockerd's mount; a restart policy, which would let dockerd remount on its
own, is refused.

Requests are checked one by one, on every keep-alive connection. A denied
request gets ``403`` with ``{"message": ...}``, which the docker CLI prints,
and one log line. A ``101`` upgrade (``attach``, ``exec`` start) is spliced in
both directions once allowed. Bodies stream in both directions, except the
create and exec bodies the policy reads, which are read whole first.

Environment:

- ``GATE_LISTEN``: the Unix socket to create (mode 0600).
- ``GATE_SOCKET_UID`` / ``GATE_SOCKET_GID``: its owner (default 0:0).
- ``GATE_UPSTREAM``: the Docker socket (default ``/var/run/docker.sock``).
- ``GATE_ALLOW_BUILD``: ``true`` admits image builds (local mode only).
- ``GATE_MODE``: ``enforce`` (default), or ``record``, which forwards every
  request and logs what enforce mode would have decided.
- ``GATE_BIND_ROOTS``: the host paths bind sources may lie under,
  ``:``-separated, each mounted read-only into this container at the same
  path. Unset, no bind passes.
- ``GATE_NETWORKS``: the networks a container may join, comma-separated
  (default ``roboco_default,bridge,none``).
- ``GATE_REGISTRY``: the registry RoboCo's images are pulled from, when they
  are (``docker-compose.registry.yml``).
- ``GATE_IMAGES``: more image patterns, comma-separated (an operator's own
  ``ROBOCO_WORKSPACE_CONTAINER_IMAGE``, say).

``python gate.py --health`` exits 0 when the listener is an owner-only
socket with the configured owner and Docker answers a ping on the upstream
socket, for the compose health check. It does not connect through the
listener: the gate runs as root without CAP_DAC_OVERRIDE, so it cannot open a
0600 socket owned by another uid, which is exactly what the listener is once
the orchestrator stops running as root.

Standard library and h11 only: no roboco imports.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote, urlsplit

import h11

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

log = logging.getLogger("docker-gate")

_VERSION_PREFIX = re.compile(r"^/v1\.\d+(?=/)")
# Every container RoboCo names: agents (and the chats and sandboxes reached
# by agent id), throwaway workspace containers, sandboxes.
NAME = r"roboco-(?:agent|sandbox|wsx)-[A-Za-z0-9_.-]+"
ID = r"[0-9a-f]{12,64}"
_NAME_RE = re.compile(rf"{NAME}\Z")
_READ_CHUNK = 65536
_MAX_HEADER = 1 << 20
_MAX_BODY = 1 << 20
_OK = 200
_FORBIDDEN = 403
_SWITCHING_PROTOCOLS = 101
_OWNER_ONLY = 0o600


@dataclass(frozen=True)
class Rule:
    """One allowed call: methods, a full-path pattern, an optional check."""

    methods: frozenset[str]
    path: re.Pattern[str]
    check: Callable[[dict[str, list[str]]], str | None] | None = None
    needs_build: bool = False


def _create_names_a_roboco_container(query: dict[str, list[str]]) -> str | None:
    names = query.get("name", [])
    if len(names) != 1 or not _NAME_RE.match(names[0]):
        return "container create must name a roboco-agent/-sandbox/-wsx container"
    return None


def _prune_is_dangling_only(query: dict[str, list[str]]) -> str | None:
    try:
        filters = json.loads(query.get("filters", ["{}"])[0])
    except ValueError:
        return "image prune filters are not JSON"
    dangling = filters.get("dangling") if isinstance(filters, dict) else None
    if dangling not in (["true"], {"true": True}):
        return "image prune must be limited to dangling images"
    return None


def _rule(
    methods: str,
    path: str,
    check: Callable[[dict[str, list[str]]], str | None] | None = None,
    *,
    needs_build: bool = False,
) -> Rule:
    return Rule(
        frozenset(methods.split()), re.compile(rf"{path}\Z"), check, needs_build
    )


# Stage 1 allowlist (trail #34). Paths are matched after the /v1.NN prefix.
RULES: tuple[Rule, ...] = (
    _rule("HEAD GET", "/_ping"),
    _rule("GET", "/version"),
    _rule("GET", "/containers/json"),
    _rule("GET", rf"/containers/(?:{NAME}|{ID})/json"),
    # The CLI resolves the name, then reads logs by id.
    _rule("GET", rf"/containers/(?:{NAME}|{ID})/logs"),
    _rule("POST", "/containers/create", _create_names_a_roboco_container),
    _rule("POST", rf"/containers/(?:{NAME}|{ID})/(?:start|attach|wait|stop|kill)"),
    _rule("DELETE", rf"/containers/{NAME}"),
    _rule("POST", rf"/containers/{NAME}/exec"),
    _rule("POST", rf"/exec/{ID}/start"),
    _rule("GET", rf"/exec/{ID}/json"),
    _rule("GET", r"/images/[^?#]+/json"),
    _rule("POST", "/images/create"),
    _rule("POST", "/images/prune", _prune_is_dangling_only),
    # CLI 29 builds through BuildKit: a /session and /grpc tunnel, not /build.
    _rule("POST", "/build", needs_build=True),
    _rule("POST", "/session", needs_build=True),
    _rule("POST", "/grpc", needs_build=True),
)


def api_path(target: str) -> tuple[str, dict[str, list[str]]]:
    """The request path without its ``/v1.NN`` prefix, and its query."""
    parts = urlsplit(target)
    path = _VERSION_PREFIX.sub("", parts.path, count=1)
    return path, parse_qs(parts.query, keep_blank_values=True)


def decide(method: str, target: str, *, allow_build: bool) -> str | None:
    """Why ``method target`` is refused; None when the allowlist admits it."""
    path, query = api_path(target)
    for rule in RULES:
        if method not in rule.methods or not rule.path.match(path):
            continue
        if rule.needs_build and not allow_build:
            return "image builds are off for this gate (GATE_ALLOW_BUILD)"
        return rule.check(query) if rule.check else None
    return "not a call RoboCo makes"


# ---------------------------------------------------------------------------
# Stage 2: what a call asks for (trail #34). Built from the bodies RoboCo's
# calls send (tests/unit/docker_gate/recorded/), not from reading the code.
# ---------------------------------------------------------------------------

# The sandbox engines' images (roboco/models/sandbox.py; a test keeps the two
# equal). RoboCo's own images match _LOCAL_IMAGES, or the registry patterns.
SANDBOX_IMAGES = (
    "postgres:16-alpine",
    "roboco-sandbox-pg:latest",
    "redis:8-alpine",
    "redis/redis-stack-server:latest",
    "mongo:8",
)
# Agents and chats; the orchestrator's own image, which the throwaway
# workspace container runs (by id: the gate checks the id's tags).
_LOCAL_IMAGES = ("roboco-agent-*", "roboco-orchestrator-*")
_DEFAULT_NETWORKS = "roboco_default,bridge,none"
_HOST_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
_ROOT_USER = re.compile(r"(?:0|root)(?::.*)?\Z")
_IMAGE_ID = re.compile(r"(?:sha256:)?[0-9a-f]{12,64}\Z")
_DOCKER_HUB = (
    "docker.io/library/",
    "index.docker.io/library/",
    "docker.io/",
    "index.docker.io/",
)
_NO_NEW_PRIVILEGES = frozenset({"no-new-privileges", "no-new-privileges:true"})
_HOST_GATEWAY = frozenset({"host.docker.internal:host-gateway"})
_BIND_PARTS = (2, 3)
# HostConfig fields RoboCo leaves empty: each would give a container more of
# the host than a bind does (devices, another container's volumes, extra
# groups, a cgroup or volume plugin, another runtime, kernel settings,
# unmasked /proc paths), or mount outside the bind check.
_EMPTY_FIELDS = (
    "CapAdd",
    "Devices",
    "DeviceRequests",
    "DeviceCgroupRules",
    "Mounts",
    "VolumesFrom",
    "GroupAdd",
    "Cgroup",
    "VolumeDriver",
    "Runtime",
    "Sysctls",
)
# Namespace modes: only the container's own. ipc and cgroupns may say so.
_OWN_NAMESPACE = {
    "PidMode": frozenset({""}),
    "UTSMode": frozenset({""}),
    "UsernsMode": frozenset({""}),
    "IpcMode": frozenset({"", "private"}),
    "CgroupnsMode": frozenset({"", "private"}),
}


@dataclass(frozen=True)
class Policy:
    """What stage 2 lets a call ask for."""

    images: tuple[str, ...] = _LOCAL_IMAGES + SANDBOX_IMAGES
    networks: frozenset[str] = frozenset(_DEFAULT_NETWORKS.split(","))
    bind_roots: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Policy:
        images = list(_LOCAL_IMAGES + SANDBOX_IMAGES)
        registry = os.environ.get("GATE_REGISTRY", "").rstrip("/")
        if registry:
            images += [
                f"{registry}/roboco-agent-*",
                f"{registry}/roboco-orchestrator:*",
            ]
        images += _split(os.environ.get("GATE_IMAGES", ""), ",")
        roots = tuple(_split(os.environ.get("GATE_BIND_ROOTS", ""), ":"))
        for root in roots:
            problem = bind_root_problem(root)
            if problem:
                raise ValueError(f"GATE_BIND_ROOTS: {problem}")
        networks = os.environ.get("GATE_NETWORKS", _DEFAULT_NETWORKS)
        return cls(tuple(images), frozenset(_split(networks, ",")), roots)


def _split(value: str, separator: str) -> list[str]:
    return [item.strip() for item in value.split(separator) if item.strip()]


def bind_root_problem(root: str) -> str | None:
    """Why ``root`` cannot hold bind sources; None if it can."""
    if not root.startswith("/") or os.path.normpath(root) != root:
        return f"{root!r} is not a normalized absolute path"
    if root == "/" or any(_overlaps(root, sock) for sock in _HOST_SOCKETS):
        return f"{root!r} would let a container bind the Docker socket"
    return None


def _overlaps(path: str, other: str) -> bool:
    return path == other or _under(path, other) or _under(other, path)


def _under(path: str, root: str) -> bool:
    return path.startswith(root.rstrip("/") + "/")


def familiar_name(ref: str) -> str:
    """``ref`` in Docker Hub's short form. The CLI pulls ``postgres`` as
    ``docker.io/library/postgres``; a create names it as it was given."""
    for prefix in _DOCKER_HUB:
        if ref.startswith(prefix):
            return ref.removeprefix(prefix)
    return ref


def image_allowed(names: Iterable[str], policy: Policy) -> bool:
    return any(
        fnmatch.fnmatchcase(familiar_name(name), pattern)
        for name in names
        for pattern in policy.images
    )


def is_root_user(user: str) -> bool:
    """True for root by name or uid, and for no user at all (the image's
    default, when the image itself sets none)."""
    return user == "" or bool(_ROOT_USER.match(user))


def create_problem(body: object, policy: Policy) -> str | None:
    """Why a container create body is refused, from the body alone."""
    if not isinstance(body, dict):
        return "the create body is not a JSON object"
    host = body.get("HostConfig") or {}
    if not isinstance(host, dict):
        return "HostConfig is not an object"
    checks = (
        _privilege_problem(host),
        _namespace_problem(host),
        _network_problem(host, body.get("NetworkingConfig"), policy),
        _host_mapping_problem(host),
        _user_problem(body.get("User") or ""),
    )
    problem = next((p for p in checks if p), None)
    if problem:
        return problem
    for bind in host.get("Binds") or []:
        problem = bind_problem(bind, policy.bind_roots)
        if problem:
            return problem
    return None


def _privilege_problem(host: dict[str, Any]) -> str | None:
    if host.get("Privileged"):
        return "privileged containers are refused"
    for name in _EMPTY_FIELDS:
        if host.get(name):
            return f"HostConfig.{name} must be empty"
    if not set(host.get("SecurityOpt") or []) <= _NO_NEW_PRIVILEGES:
        return "SecurityOpt may only hold no-new-privileges"
    for name in ("MaskedPaths", "ReadonlyPaths"):
        if host.get(name) is not None:
            return f"HostConfig.{name} must be left to Docker"
    restart = (host.get("RestartPolicy") or {}).get("Name") or "no"
    if restart != "no":
        return "a restart policy would let Docker remount binds unchecked"
    return None


def _namespace_problem(host: dict[str, Any]) -> str | None:
    for name, allowed in _OWN_NAMESPACE.items():
        if (host.get(name) or "") not in allowed:
            return f"HostConfig.{name} {host.get(name)!r} is not the container's own"
    return None


def _network_problem(
    host: dict[str, Any], networking: object, policy: Policy
) -> str | None:
    mode = host.get("NetworkMode") or ""
    if mode not in policy.networks:
        return f"network {mode!r} is not one RoboCo uses"
    endpoints = (
        networking.get("EndpointsConfig") if isinstance(networking, dict) else None
    )
    extra = set(endpoints or {}) - policy.networks
    if extra:
        return f"networks {sorted(extra)} are not ones RoboCo uses"
    return None


def _host_mapping_problem(host: dict[str, Any]) -> str | None:
    if not set(host.get("ExtraHosts") or []) <= _HOST_GATEWAY:
        return "ExtraHosts may only map host.docker.internal to the host gateway"
    return None


def _user_problem(user: str) -> str | None:
    if user and is_root_user(user):
        return "containers may not run as root"
    return None


def bind_problem(bind: object, roots: tuple[str, ...]) -> str | None:
    """Why a ``source:target[:mode]`` bind is refused, from the string alone."""
    if not isinstance(bind, str) or len(bind.split(":")) not in _BIND_PARTS:
        return f"bind {bind!r} is not source:target[:mode]"
    source = bind.split(":", 1)[0]
    if not source.startswith("/"):
        return f"bind {bind!r} names a volume; only host paths are bound"
    if os.path.normpath(source) != source or source == "/":
        return f"bind source {source!r} is not a normalized path below /"
    if any(_overlaps(source, sock) for sock in _HOST_SOCKETS):
        return f"bind source {source!r} would expose the Docker socket"
    if not any(source == root or _under(source, root) for root in roots):
        return f"bind source {source!r} is outside the bind roots"
    return None


def root_of(source: str, roots: tuple[str, ...]) -> str | None:
    """The longest bind root ``source`` lies under, if any."""
    under = [r for r in roots if source == r or _under(source, r)]
    return max(under, key=len) if under else None


def symlink_problem(source: str, root: str) -> str | None:
    """Why ``source``, below ``root``, is not a plain path on disk.

    Each component below the root is opened with ``O_NOFOLLOW`` one at a
    time from the root's descriptor; a symlink anywhere, or a component that
    is missing or unreadable, refuses the bind.
    """
    try:
        fd = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        return f"bind root {root} cannot be opened here: {exc.strerror}"
    try:
        for part in PurePosixPath(source).relative_to(root).parts:
            child = os.open(part, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            if stat.S_ISLNK(os.fstat(fd).st_mode):
                return f"bind source {source} goes through a symlink ({part})"
    except OSError as exc:
        return f"bind source {source} cannot be checked: {exc.strerror}"
    finally:
        os.close(fd)
    return None


def binds_on_disk_problem(
    binds: Iterable[object], roots: tuple[str, ...]
) -> str | None:
    """The first bind refused by its string or by what is on disk."""
    for bind in binds:
        problem = bind_problem(bind, roots)
        if problem:
            return problem
        source = str(bind).split(":", 1)[0]
        root = root_of(source, roots)
        problem = symlink_problem(source, root) if root else None
        if problem:
            return problem
    return None


def start_problem(info: dict[str, Any], policy: Policy) -> str | None:
    """Why a container (as ``GET /containers/{id}/json`` shows it) may not
    start: root with binds, or a bind that is not a plain path on disk now.
    ``Config.User`` there is the image's default when the create set none."""
    binds = (info.get("HostConfig") or {}).get("Binds") or []
    user = str((info.get("Config") or {}).get("User") or "")
    if binds and is_root_user(user):
        return "a container running as root may not have binds"
    return binds_on_disk_problem(binds, policy.bind_roots)


def exec_problem(body: object) -> str | None:
    """Why an exec create body is refused."""
    if not isinstance(body, dict):
        return "the exec body is not a JSON object"
    if body.get("Privileged"):
        return "privileged execs are refused"
    if body.get("User"):
        return "execs run as the container's own user"
    return None


def pull_problem(query: dict[str, list[str]], policy: Policy) -> str | None:
    """Why an image pull (``POST /images/create``) is refused."""
    names = query.get("fromImage", [])
    if len(names) != 1 or query.get("fromSrc"):
        return "only a pull of one named image is allowed"
    tag = query.get("tag", [""])[0]
    separator = "@" if tag.startswith("sha256:") else ":"
    ref = f"{names[0]}{separator}{tag}" if tag else names[0]
    if not image_allowed([ref], policy):
        return f"image {ref!r} is not one RoboCo runs"
    return None


def container_is_robocos(info: dict[str, Any]) -> bool:
    """A container RoboCo made: one of its names, or a ``roboco.*`` label."""
    name = str(info.get("Name") or "").lstrip("/")
    labels = (info.get("Config") or {}).get("Labels") or {}
    return bool(_NAME_RE.match(name)) or any(k.startswith("roboco.") for k in labels)


_CREATE = re.compile(r"/containers/create\Z")
_EXEC = re.compile(rf"/containers/{NAME}/exec\Z")
_PULL = re.compile(r"/images/create\Z")
_BY_ID = re.compile(rf"/containers/({ID})/(?:start|attach|wait|stop|kill|logs)\Z")
_START = re.compile(rf"/containers/({NAME}|{ID})/start\Z")


@dataclass(frozen=True)
class Settings:
    listen: str
    upstream: str
    socket_uid: int
    socket_gid: int
    allow_build: bool
    record: bool
    policy: Policy = field(default_factory=Policy)

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.environ.get("GATE_MODE", "enforce")
        if mode not in {"enforce", "record"}:
            msg = f"GATE_MODE must be enforce or record, not {mode!r}"
            raise ValueError(msg)
        return cls(
            listen=os.environ["GATE_LISTEN"],
            upstream=os.environ.get("GATE_UPSTREAM", "/var/run/docker.sock"),
            socket_uid=int(os.environ.get("GATE_SOCKET_UID", "0")),
            socket_gid=int(os.environ.get("GATE_SOCKET_GID", "0")),
            allow_build=os.environ.get("GATE_ALLOW_BUILD", "false") == "true",
            record=mode == "record",
            policy=Policy.from_env(),
        )


class _Peer:
    """One side of the proxy: an h11 connection over an asyncio stream."""

    def __init__(
        self,
        role: type[h11.CLIENT] | type[h11.SERVER],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.conn = h11.Connection(our_role=role, max_incomplete_event_size=_MAX_HEADER)
        self.reader = reader
        self.writer = writer

    async def next_event(self) -> h11.Event | type[h11.NEED_DATA] | type[h11.PAUSED]:
        while True:
            event = self.conn.next_event()
            if event is not h11.NEED_DATA:
                return event
            data = await self.reader.read(_READ_CHUNK)
            self.conn.receive_data(data)

    async def send(self, event: h11.Event) -> None:
        data = self.conn.send(event)
        if data:
            self.writer.write(data)
            await self.writer.drain()

    def close(self) -> None:
        self.writer.close()


def _log_decision(
    decision: str, method: str, target: str, reason: str | None, peer: str
) -> None:
    log.info(
        json.dumps(
            {
                "decision": decision,
                "method": method,
                "target": target,
                "reason": reason,
                "peer": peer,
            },
            separators=(",", ":"),
        )
    )


async def _read_body(client: _Peer) -> tuple[bytes, str | None]:
    """The request body, whole; a reason when it is over the size limit."""
    body = bytearray()
    while True:
        event = await client.next_event()
        if isinstance(event, h11.EndOfMessage):
            break
        if not isinstance(event, h11.Data):
            msg = f"unexpected {type(event).__name__} in a request body"
            raise h11.RemoteProtocolError(msg)
        if len(body) <= _MAX_BODY:
            body += event.data
    if len(body) > _MAX_BODY:
        return bytes(body), "the request body is over 1 MiB"
    return bytes(body), None


def _json(body: bytes) -> tuple[object, str | None]:
    try:
        return json.loads(body), None
    except ValueError:
        return None, "the request body is not JSON"


class Gate:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def serve(self) -> None:
        listen = Path(self.settings.listen)
        listen.unlink(missing_ok=True)
        # Owner-only from the moment it exists, not only after the chmod.
        os.umask(0o077)
        server = await asyncio.start_unix_server(
            self._client, path=self.settings.listen
        )
        listen.chmod(_OWNER_ONLY)
        os.chown(
            self.settings.listen, self.settings.socket_uid, self.settings.socket_gid
        )
        log.info(
            json.dumps(
                {
                    "listening": self.settings.listen,
                    "upstream": self.settings.upstream,
                    "mode": "record" if self.settings.record else "enforce",
                    "allow_build": self.settings.allow_build,
                    "bind_roots": list(self.settings.policy.bind_roots),
                }
            )
        )
        async with server:
            await server.serve_forever()

    async def _client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        client = _Peer(h11.SERVER, reader, writer)
        upstream: _Peer | None = None
        try:
            while True:
                event = await client.next_event()
                if not isinstance(event, h11.Request):
                    return
                method = event.method.decode("ascii")
                target = event.target.decode("ascii")
                body, reason = await self._check(client, method, target)
                if reason is not None and not self.settings.record:
                    _log_decision("deny", method, target, reason, "client")
                    await self._refuse(client, reason, body_read=body is not None)
                    if client.conn.our_state is h11.MUST_CLOSE:
                        return
                    client.conn.start_next_cycle()
                    continue
                _log_decision(
                    "record" if self.settings.record else "allow",
                    method,
                    target,
                    reason,
                    "client",
                )
                if upstream is None:
                    up_reader, up_writer = await asyncio.open_unix_connection(
                        self.settings.upstream
                    )
                    upstream = _Peer(h11.CLIENT, up_reader, up_writer)
                switched = await self._forward(client, upstream, event, body)
                if switched:
                    await _splice(client, upstream)
                    return
                if (
                    client.conn.our_state is h11.MUST_CLOSE
                    or upstream.conn.our_state is h11.MUST_CLOSE
                ):
                    return
                client.conn.start_next_cycle()
                upstream.conn.start_next_cycle()
        except (
            h11.RemoteProtocolError,
            h11.LocalProtocolError,
            ConnectionError,
        ) as exc:
            log.info(json.dumps({"connection_error": f"{type(exc).__name__}: {exc}"}))
        finally:
            client.close()
            if upstream is not None:
                upstream.close()

    async def _check(
        self, client: _Peer, method: str, target: str
    ) -> tuple[bytes | None, str | None]:
        """Stage 1, then stage 2. Returns the request body when stage 2 had to
        read it (create and exec), and why the request is refused, if it is."""
        reason = decide(method, target, allow_build=self.settings.allow_build)
        if reason is not None:
            return None, reason
        path, query = api_path(target)
        if method == "POST" and (_CREATE.match(path) or _EXEC.match(path)):
            body, reason = await _read_body(client)
            if reason is None:
                document, reason = _json(body)
                if reason is None:
                    reason = (
                        await self._create_problem(document)
                        if _CREATE.match(path)
                        else exec_problem(document)
                    )
            return body, reason
        if method == "POST" and _PULL.match(path):
            return None, pull_problem(query, self.settings.policy)
        return None, await self._container_problem(method, path)

    async def _create_problem(self, body: object) -> str | None:
        """The body's own checks, then the image: an allowed one, and, when it
        is present, not root by default if the container has binds.

        An absent image passes on to Docker, which answers 404, so the CLI
        pulls it (an allowed image only) and asks again. Whatever image a
        container ends up with, its start checks the user again.
        """
        policy = self.settings.policy
        problem = create_problem(body, policy)
        if problem or not isinstance(body, dict):
            return problem
        image = str(body.get("Image") or "")
        binds = (body.get("HostConfig") or {}).get("Binds") or []
        status, info = await self._docker_get(
            f"/images/{quote(image, safe='/:@')}/json"
        )
        found = info if status == _OK and isinstance(info, dict) else None
        if _IMAGE_ID.match(image):
            if found is None:
                return f"image {image} is not present"
            names = list(found.get("RepoTags") or [])
        else:
            names = [image]
        if not image_allowed(names, policy):
            return f"image {image!r} is not one RoboCo runs"
        if found is not None and binds and not body.get("User"):
            default_user = str((found.get("Config") or {}).get("User") or "")
            if is_root_user(default_user):
                return f"image {image!r} runs as root, so it may not have binds"
        return binds_on_disk_problem(binds, policy.bind_roots)

    async def _container_problem(self, method: str, path: str) -> str | None:
        """A call by container id must reach one of RoboCo's containers, and a
        start checks the container's final user and binds (on disk) again."""
        by_id = _BY_ID.match(path)
        start = _START.match(path) if method == "POST" else None
        match = by_id or start
        if match is None:
            return None
        ref = match.group(1)
        status, info = await self._docker_get(f"/containers/{ref}/json")
        if status != _OK or not isinstance(info, dict):
            return None
        if by_id and not container_is_robocos(info):
            return f"container {ref} is not one of RoboCo's"
        return start_problem(info, self.settings.policy) if start else None

    async def _docker_get(self, path: str) -> tuple[int, object]:
        """GET ``path`` from Docker on a connection of its own."""
        reader, writer = await asyncio.open_unix_connection(self.settings.upstream)
        peer = _Peer(h11.CLIENT, reader, writer)
        status, data = 0, bytearray()
        try:
            await peer.send(
                h11.Request(
                    method="GET",
                    target=path,
                    headers=[("Host", "docker"), ("Connection", "close")],
                )
            )
            await peer.send(h11.EndOfMessage())
            while True:
                event = await peer.next_event()
                if isinstance(event, h11.Response):
                    status = event.status_code
                elif isinstance(event, h11.Data):
                    data += event.data
                else:
                    break
        finally:
            peer.close()
        document, _ = _json(bytes(data))
        return status, document

    async def _refuse(self, client: _Peer, reason: str, *, body_read: bool) -> None:
        """Answer 403 after reading the request body, so the connection stays usable."""
        if not body_read:
            while not isinstance(await client.next_event(), h11.EndOfMessage):
                pass
        body = json.dumps(
            {"message": f"refused by RoboCo's Docker gate: {reason}"}
        ).encode()
        await client.send(
            h11.Response(
                status_code=_FORBIDDEN,
                headers=[
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
            )
        )
        await client.send(h11.Data(data=body))
        await client.send(h11.EndOfMessage())

    async def _forward(
        self,
        client: _Peer,
        upstream: _Peer,
        request: h11.Request,
        body: bytes | None,
    ) -> bool:
        """Relay one request and its response; True when the protocol switched.

        ``body`` is the request body when the checks already read it; else it
        streams from the client.
        """
        await upstream.send(request)
        if body is not None:
            if body:
                await upstream.send(h11.Data(data=body))
            await upstream.send(h11.EndOfMessage())
        else:
            await _stream_body(client, upstream)
        while True:
            event = await upstream.next_event()
            if isinstance(event, h11.InformationalResponse):
                await client.send(event)
                if event.status_code == _SWITCHING_PROTOCOLS:
                    return True
                continue
            if isinstance(event, h11.Response):
                await client.send(event)
                break
            msg = f"unexpected {type(event).__name__} instead of a response"
            raise h11.RemoteProtocolError(msg)
        while True:
            event = await upstream.next_event()
            if isinstance(event, h11.Data):
                await client.send(event)
            elif isinstance(event, h11.EndOfMessage):
                await client.send(event)
                return False
            else:
                msg = f"unexpected {type(event).__name__} in a response body"
                raise h11.RemoteProtocolError(msg)


async def _stream_body(client: _Peer, upstream: _Peer) -> None:
    while True:
        event = await client.next_event()
        if isinstance(event, h11.Data):
            await upstream.send(event)
        elif isinstance(event, h11.EndOfMessage):
            await upstream.send(event)
            return
        else:
            msg = f"unexpected {type(event).__name__} in a request body"
            raise h11.RemoteProtocolError(msg)


async def _pipe(source: _Peer, sink: _Peer, leftover: bytes) -> None:
    if leftover:
        sink.writer.write(leftover)
        await sink.writer.drain()
    while data := await source.reader.read(_READ_CHUNK):
        sink.writer.write(data)
        await sink.writer.drain()
    if sink.writer.can_write_eof():
        sink.writer.write_eof()


async def _splice(client: _Peer, upstream: _Peer) -> None:
    """After a 101, copy raw bytes both ways until both sides are done."""
    client_rest, _ = client.conn.trailing_data
    upstream_rest, _ = upstream.conn.trailing_data
    await asyncio.gather(
        _pipe(client, upstream, client_rest),
        _pipe(upstream, client, upstream_rest),
        return_exceptions=True,
    )


def listener_problem(settings: Settings) -> str | None:
    """Why the listener is not the owner-only socket it should be; None if it is."""
    try:
        st = Path(settings.listen).stat()
    except OSError as exc:
        return f"no listener at {settings.listen}: {exc}"
    if not stat.S_ISSOCK(st.st_mode):
        return f"{settings.listen} is not a socket"
    if stat.S_IMODE(st.st_mode) != _OWNER_ONLY:
        return f"{settings.listen} has mode {stat.S_IMODE(st.st_mode):o}, not 600"
    if (st.st_uid, st.st_gid) != (settings.socket_uid, settings.socket_gid):
        return f"{settings.listen} is owned by {st.st_uid}:{st.st_gid}"
    return None


async def _upstream_answers(upstream: str) -> bool:
    reader, writer = await asyncio.open_unix_connection(upstream)
    writer.write(b"HEAD /_ping HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n")
    await writer.drain()
    status = await reader.readline()
    writer.close()
    return status.split(b" ")[1:2] == [b"200"]


def health(settings: Settings) -> int:
    """0 when the listener is right and Docker answers; 1 with the reason otherwise."""
    problem = listener_problem(settings)
    if problem is None:
        try:
            if not asyncio.run(_upstream_answers(settings.upstream)):
                problem = f"Docker did not answer a ping on {settings.upstream}"
        except OSError as exc:
            problem = f"cannot reach Docker on {settings.upstream}: {exc}"
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    settings = Settings.from_env()
    if argv[1:] == ["--health"]:
        return health(settings)
    asyncio.run(Gate(settings).serve())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
