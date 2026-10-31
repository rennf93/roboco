"""The Docker gate proxies what it allows and nothing else.

A fake Docker daemon on a Unix socket records every request that reaches it,
so these tests can show that a refused request never does, that an allowed
one arrives intact with its body, that responses stream back (including a
chunked one), that a ``101`` upgrade is spliced both ways, and that one
keep-alive connection is checked request by request. It also answers the
image and container lookups stage 2 makes, from its own tables, and keeps
those apart from the calls the gate forwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import h11
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

_GATE = Path(__file__).parents[3] / "docker" / "docker-gate" / "gate.py"
AGENT = "roboco-agent-0b6e1f2a"
ID = "4f1c2b3a9d8e"
OTHER_ID = "9a8b7c6d5e4f"
AGENT_IMAGE = "roboco-agent-dev-be"
ROOT_IMAGE = "postgres:16-alpine"
_NOT_FOUND = 404
_OK = 200
_FORBIDDEN = 403
_SWITCHING_PROTOCOLS = 101
_OWNER_ONLY = 0o600


@cache
def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docker_gate_proxy", _GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _container(
    name: str, *, user: str = "agent", binds: list[str] | None = None
) -> dict[str, Any]:
    return {
        "Name": f"/{name}",
        "Config": {"User": user, "Labels": {}},
        "HostConfig": {"Binds": binds},
    }


@dataclass
class FakeDocker:
    """Answers like the Engine API for the few calls these tests make.

    ``seen`` holds what the gate forwards; ``inspected`` the image and
    container lookups stage 2 makes on connections of its own.
    """

    seen: list[tuple[str, str, bytes]] = field(default_factory=list)
    inspected: list[str] = field(default_factory=list)
    images: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            AGENT_IMAGE: {
                "RepoTags": [f"{AGENT_IMAGE}:latest"],
                "Config": {"User": "agent"},
            },
            ROOT_IMAGE: {"RepoTags": [ROOT_IMAGE], "Config": {"User": ""}},
        }
    )
    containers: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            AGENT: _container(AGENT),
            ID: _container(AGENT),
            OTHER_ID: _container("roboco-postgres", user="postgres"),
        }
    )

    def _lookup(self, path: str) -> dict[str, Any] | None:
        """The image or container a stage-2 lookup asks for, if there is one."""
        _, kind, ref = path.removesuffix("/json").split("/", 2)
        table = self.images if kind == "images" else self.containers
        return table.get(ref)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = h11.Connection(h11.SERVER)
        try:
            while True:
                request, body = await self._read_request(conn, reader)
                if request is None:
                    return
                method, target = request.method.decode(), request.target.decode()
                path = urlsplit(target).path
                if (
                    method == "GET"
                    and path.endswith("/json")
                    and ("/images/" in path or "/containers/" in path)
                    and not path.endswith("/containers/json")
                ):
                    self.inspected.append(path)
                    found = self._lookup(path)
                    payload = json.dumps(
                        found or {"message": "No such object"}
                    ).encode()
                    writer.write(
                        conn.send(
                            h11.Response(
                                status_code=_OK if found else _NOT_FOUND,
                                headers=[("Content-Length", str(len(payload)))],
                            )
                        )
                    )
                    writer.write(conn.send(h11.Data(data=payload)))
                    writer.write(conn.send(h11.EndOfMessage()))
                    await writer.drain()
                    return
                self.seen.append((method, target, body))
                if path.endswith("/attach"):
                    writer.write(
                        conn.send(
                            h11.InformationalResponse(
                                status_code=101,
                                headers=[("Connection", "Upgrade"), ("Upgrade", "tcp")],
                            )
                        )
                    )
                    await writer.drain()
                    while data := await reader.read(4096):
                        writer.write(data.upper())
                        await writer.drain()
                    writer.write(b"BYE")
                    writer.write_eof()
                    return
                if path.endswith("/logs"):
                    writer.write(
                        conn.send(
                            h11.Response(
                                status_code=_OK,
                                headers=[("Transfer-Encoding", "chunked")],
                            )
                        )
                    )
                    for part in (b"one ", b"two ", b"three"):
                        writer.write(conn.send(h11.Data(data=part)))
                else:
                    payload = json.dumps(
                        {"target": target, "body": body.decode()}
                    ).encode()
                    writer.write(
                        conn.send(
                            h11.Response(
                                status_code=_OK,
                                headers=[("Content-Length", str(len(payload)))],
                            )
                        )
                    )
                    writer.write(conn.send(h11.Data(data=payload)))
                writer.write(conn.send(h11.EndOfMessage()))
                await writer.drain()
                conn.start_next_cycle()
        finally:
            writer.close()

    @staticmethod
    async def _read_request(
        conn: h11.Connection, reader: asyncio.StreamReader
    ) -> tuple[h11.Request | None, bytes]:
        request: h11.Request | None = None
        body = b""
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                conn.receive_data(await reader.read(4096))
            elif isinstance(event, h11.Request):
                request = event
            elif isinstance(event, h11.Data):
                body += event.data
            elif isinstance(event, h11.EndOfMessage):
                return request, body
            else:
                return None, b""


@dataclass
class Running:
    listen: str
    docker: FakeDocker
    settings: Any


@contextlib.asynccontextmanager
async def _running(
    *, record: bool = False, bind_roots: tuple[str, ...] = ()
) -> AsyncIterator[Running]:
    # A short directory: a Unix socket path is limited to ~108 bytes.
    root = tempfile.mkdtemp(prefix="dg")
    try:
        docker = FakeDocker()
        upstream = f"{root}/docker.sock"
        fake = await asyncio.start_unix_server(docker.handle, path=upstream)
        settings = _gate().Settings(
            listen=f"{root}/gate.sock",
            upstream=upstream,
            socket_uid=os.getuid(),
            socket_gid=os.getgid(),
            allow_build=False,
            record=record,
            policy=_gate().Policy(bind_roots=bind_roots),
        )
        serving = asyncio.create_task(_gate().Gate(settings).serve())
        for _ in range(100):
            if Path(settings.listen).exists():
                break
            await asyncio.sleep(0.01)
        yield Running(settings.listen, docker, settings)
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        fake.close()
        await fake.wait_closed()
    finally:
        shutil.rmtree(root, ignore_errors=True)


class Client:
    """A keep-alive HTTP/1.1 client over the gate's socket."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.conn = h11.Connection(h11.CLIENT)
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, path: str) -> Client:
        return cls(*await asyncio.open_unix_connection(path))

    async def call(
        self,
        method: str,
        target: str,
        body: bytes = b"",
        headers: list[tuple[str, str]] | None = None,
    ) -> tuple[int, bytes]:
        extra = list(headers or [])
        if body:
            extra.append(("Content-Length", str(len(body))))
        self.writer.write(
            self.conn.send(
                h11.Request(
                    method=method,
                    target=target,
                    headers=[("Host", "docker"), *extra],
                )
            )
        )
        if body:
            self.writer.write(self.conn.send(h11.Data(data=body)))
        self.writer.write(self.conn.send(h11.EndOfMessage()))
        await self.writer.drain()
        status, data = 0, b""
        while True:
            event = self.conn.next_event()
            if event is h11.NEED_DATA:
                self.conn.receive_data(await self.reader.read(4096))
            elif isinstance(event, h11.InformationalResponse):
                return event.status_code, b""
            elif isinstance(event, h11.Response):
                status = event.status_code
            elif isinstance(event, h11.Data):
                data += event.data
            elif isinstance(event, h11.EndOfMessage):
                self.conn.start_next_cycle()
                return status, data

    def close(self) -> None:
        self.writer.close()


def _create_body(image: str = AGENT_IMAGE, **host: Any) -> bytes:
    return json.dumps(
        {"Image": image, "HostConfig": {"NetworkMode": "roboco_default", **host}}
    ).encode()


async def test_an_allowed_call_reaches_docker_with_its_body() -> None:
    body = _create_body()
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        status, data = await client.call(
            "POST", "/v1.51/containers/create?name=roboco-wsx-0123456789ab", body
        )
        client.close()
    assert status == _OK
    assert json.loads(data)["body"] == body.decode()
    assert gate.docker.seen == [
        ("POST", "/v1.51/containers/create?name=roboco-wsx-0123456789ab", body)
    ]
    assert gate.docker.inspected == [f"/images/{AGENT_IMAGE}/json"]


async def test_a_refused_call_never_reaches_docker_and_says_why() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        status, data = await client.call(
            "POST", "/v1.51/containers/create?name=evil", b"{}"
        )
        client.close()
    assert status == _FORBIDDEN
    assert "roboco-agent" in json.loads(data)["message"]
    assert gate.docker.seen == []


async def test_one_keep_alive_connection_is_checked_request_by_request() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        first = await client.call("GET", "/v1.51/info")
        second = await client.call("GET", "/_ping")
        third = await client.call("POST", "/v1.51/networks/create", b"{}")
        client.close()
    assert [first[0], second[0], third[0]] == [_FORBIDDEN, _OK, _FORBIDDEN]
    assert [t for _, t, _ in gate.docker.seen] == ["/_ping"]


async def test_a_chunked_response_streams_back_whole() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        status, data = await client.call(
            "GET", f"/v1.51/containers/{AGENT}/logs?stdout=1"
        )
        client.close()
    assert (status, data) == (_OK, b"one two three")


async def test_an_upgraded_connection_is_spliced_both_ways() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        status, _ = await client.call(
            "POST",
            f"/v1.51/containers/{ID}/attach?stdin=1&stream=1",
            headers=[("Connection", "Upgrade"), ("Upgrade", "tcp")],
        )
        assert status == _SWITCHING_PROTOCOLS
        rest, _ = client.conn.trailing_data
        client.writer.write(b"hello")
        client.writer.write_eof()
        received = rest
        while chunk := await client.reader.read(4096):
            received += chunk
        client.close()
    assert received == b"HELLOBYE"


async def test_record_mode_forwards_what_enforce_mode_would_refuse() -> None:
    async with _running(record=True) as gate:
        client = await Client.connect(gate.listen)
        status, _ = await client.call("GET", "/v1.51/info")
        client.close()
    assert status == _OK
    assert [t for _, t, _ in gate.docker.seen] == ["/v1.51/info"]


async def test_the_socket_is_owner_only() -> None:
    async with _running() as gate:
        mode = stat.S_IMODE(Path(gate.listen).stat().st_mode)
    assert mode == _OWNER_ONLY


def test_an_unknown_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_LISTEN", "/tmp/x.sock")
    monkeypatch.setenv("GATE_MODE", "allow-all")
    with pytest.raises(ValueError, match="GATE_MODE"):
        _gate().Settings.from_env()


async def test_health_passes_when_the_listener_and_docker_are_right() -> None:
    async with _running() as gate:
        code = await asyncio.to_thread(_gate().health, gate.settings)
    assert code == 0


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda listen: Path(listen).chmod(0o666), "mode 666"),
        (lambda listen: Path(listen).unlink(), "no listener"),
    ],
)
async def test_health_fails_and_says_why(
    break_it: Any, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with _running() as gate:
        break_it(gate.listen)
        code = await asyncio.to_thread(_gate().health, gate.settings)
    assert code == 1
    assert expected in capsys.readouterr().err


async def test_health_fails_when_the_listener_has_another_owner() -> None:
    async with _running() as gate:
        other = _gate().Settings(
            listen=gate.listen,
            upstream=gate.settings.upstream,
            socket_uid=gate.settings.socket_uid + 1,
            socket_gid=gate.settings.socket_gid,
            allow_build=False,
            record=False,
        )
        assert "is owned by" in (_gate().listener_problem(other) or "")


async def test_a_create_asking_for_too_much_never_reaches_docker() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        status, data = await client.call(
            "POST",
            f"/v1.51/containers/create?name={AGENT}",
            _create_body(Privileged=True),
        )
        # The connection stays usable after a body the gate read whole.
        ping, _ = await client.call("GET", "/_ping")
        client.close()
    assert status == _FORBIDDEN
    assert "privileged" in json.loads(data)["message"]
    assert ping == _OK
    assert [t for _, t, _ in gate.docker.seen] == ["/_ping"]


async def test_a_root_image_may_not_bind_and_an_absent_one_is_left_to_docker(
    tmp_path: Path,
) -> None:
    (tmp_path / "data").mkdir()
    bind = [f"{tmp_path}/data:/data"]
    async with _running(bind_roots=(str(tmp_path),)) as gate:
        client = await Client.connect(gate.listen)
        root, data = await client.call(
            "POST",
            f"/v1.51/containers/create?name={AGENT}",
            _create_body(ROOT_IMAGE, Binds=bind),
        )
        absent, _ = await client.call(
            "POST",
            f"/v1.51/containers/create?name={AGENT}",
            _create_body("roboco-agent-not-pulled-yet", Binds=bind),
        )
        client.close()
    assert root == _FORBIDDEN
    assert "runs as root" in json.loads(data)["message"]
    # Docker itself answers for an image it does not have (the CLI then
    # pulls an allowed image and asks again); the start checks the user.
    assert absent == _OK


async def test_a_bind_through_a_symlink_is_refused_at_create_and_at_start(
    tmp_path: Path,
) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("/etc")
    async with _running(bind_roots=(str(tmp_path),)) as gate:
        gate.docker.containers[AGENT] = _container(
            AGENT, binds=[f"{tmp_path}/link:/mnt"]
        )
        client = await Client.connect(gate.listen)
        create, created = await client.call(
            "POST",
            f"/v1.51/containers/create?name={AGENT}",
            _create_body(Binds=[f"{tmp_path}/link:/mnt"]),
        )
        start, started = await client.call("POST", f"/v1.51/containers/{AGENT}/start")
        client.close()
    assert (create, start) == (_FORBIDDEN, _FORBIDDEN)
    for data in (created, started):
        assert "symlink" in json.loads(data)["message"]
    assert gate.docker.seen == []


async def test_a_start_checks_the_container_as_it_is_now(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    async with _running(bind_roots=(str(tmp_path),)) as gate:
        gate.docker.containers[AGENT] = _container(
            AGENT, binds=[f"{tmp_path}/real:/mnt"]
        )
        gate.docker.containers["roboco-sandbox-pg-x"] = _container(
            "roboco-sandbox-pg-x", user="", binds=[f"{tmp_path}/real:/mnt"]
        )
        client = await Client.connect(gate.listen)
        plain, _ = await client.call("POST", f"/v1.51/containers/{AGENT}/start")
        root, data = await client.call(
            "POST", "/v1.51/containers/roboco-sandbox-pg-x/start"
        )
        client.close()
    assert (plain, root) == (_OK, _FORBIDDEN)
    assert "root" in json.loads(data)["message"]


async def test_a_call_by_id_must_reach_a_roboco_container() -> None:
    async with _running() as gate:
        client = await Client.connect(gate.listen)
        mine, _ = await client.call("POST", f"/v1.51/containers/{ID}/stop")
        theirs, data = await client.call("POST", f"/v1.51/containers/{OTHER_ID}/stop")
        logs, _ = await client.call("GET", f"/v1.51/containers/{OTHER_ID}/logs")
        client.close()
    assert (mine, theirs, logs) == (_OK, _FORBIDDEN, _FORBIDDEN)
    assert "not one of RoboCo's" in json.loads(data)["message"]


async def test_record_mode_forwards_a_create_stage_2_would_refuse() -> None:
    async with _running(record=True) as gate:
        client = await Client.connect(gate.listen)
        status, _ = await client.call(
            "POST",
            f"/v1.51/containers/create?name={AGENT}",
            _create_body(Privileged=True),
        )
        client.close()
    assert status == _OK
    assert len(gate.docker.seen) == 1
