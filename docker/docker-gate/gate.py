"""RoboCo's Docker gate: the orchestrator reaches Docker only through here.

The orchestrator drives Docker through the ``docker`` CLI
(``roboco/utils/docker_subcommands.py`` lists every subcommand it runs).
Holding the raw ``/var/run/docker.sock`` is root on the host, so the gate sits
between them: it listens on an owner-only Unix socket that the orchestrator's
``DOCKER_HOST`` points at, checks every Engine API request against the calls
RoboCo makes, and forwards only those to the real socket.

Stage 1 (this file) checks the method and the path, plus the container name on
``POST /containers/create``. Stage 2, the request-body policy, comes in its
own change (trail #34).

Requests are checked one by one, on every keep-alive connection. A denied
request gets ``403`` with ``{"message": ...}``, which the docker CLI prints,
and one log line. A ``101`` upgrade (``attach``, ``exec`` start) is spliced in
both directions once allowed. Bodies stream in both directions.

Environment:

- ``GATE_LISTEN``: the Unix socket to create (mode 0600).
- ``GATE_SOCKET_UID`` / ``GATE_SOCKET_GID``: its owner (default 0:0).
- ``GATE_UPSTREAM``: the Docker socket (default ``/var/run/docker.sock``).
- ``GATE_ALLOW_BUILD``: ``true`` admits image builds (local mode only).
- ``GATE_MODE``: ``enforce`` (default), or ``record``, which forwards every
  request and logs what enforce mode would have decided.

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
import json
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import h11

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("docker-gate")

_VERSION_PREFIX = re.compile(r"^/v1\.\d+(?=/)")
# Every container RoboCo names: agents (and the chats and sandboxes reached
# by agent id), throwaway workspace containers, sandboxes.
NAME = r"roboco-(?:agent|sandbox|wsx)-[A-Za-z0-9_.-]+"
ID = r"[0-9a-f]{12,64}"
_NAME_RE = re.compile(rf"{NAME}\Z")
_READ_CHUNK = 65536
_MAX_HEADER = 1 << 20
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
    _rule("POST", "/images/prune"),
    # CLI 29 builds through BuildKit: a /session and /grpc tunnel, not /build.
    _rule("POST", "/build", needs_build=True),
    _rule("POST", "/session", needs_build=True),
    _rule("POST", "/grpc", needs_build=True),
)


def decide(method: str, target: str, *, allow_build: bool) -> str | None:
    """Why ``method target`` is refused; None when the allowlist admits it."""
    parts = urlsplit(target)
    path = _VERSION_PREFIX.sub("", parts.path, count=1)
    query = parse_qs(parts.query, keep_blank_values=True)
    for rule in RULES:
        if method not in rule.methods or not rule.path.match(path):
            continue
        if rule.needs_build and not allow_build:
            return "image builds are off for this gate (GATE_ALLOW_BUILD)"
        return rule.check(query) if rule.check else None
    return "not a call RoboCo makes"


@dataclass(frozen=True)
class Settings:
    listen: str
    upstream: str
    socket_uid: int
    socket_gid: int
    allow_build: bool
    record: bool

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
                if isinstance(event, h11.ConnectionClosed):
                    return
                if not isinstance(event, h11.Request):
                    return
                method = event.method.decode("ascii")
                target = event.target.decode("ascii")
                reason = decide(method, target, allow_build=self.settings.allow_build)
                if reason is not None and not self.settings.record:
                    _log_decision("deny", method, target, reason, "client")
                    await self._refuse(client, reason)
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
                switched = await self._forward(client, upstream, event)
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

    async def _refuse(self, client: _Peer, reason: str) -> None:
        """Answer 403 after reading the request body, so the connection stays usable."""
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
        self, client: _Peer, upstream: _Peer, request: h11.Request
    ) -> bool:
        """Relay one request and its response; True when the protocol switched."""
        await upstream.send(request)
        while True:
            event = await client.next_event()
            if isinstance(event, h11.Data):
                await upstream.send(event)
            elif isinstance(event, h11.EndOfMessage):
                await upstream.send(event)
                break
            else:
                msg = f"unexpected {type(event).__name__} in a request body"
                raise h11.RemoteProtocolError(msg)
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
