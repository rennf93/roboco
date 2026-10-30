"""Record the Engine API bodies RoboCo's ``docker run`` and ``docker exec`` send.

Usage: ``uv run python scripts/record_docker_gate_bodies.py OUT_DIR``

The Docker gate's stage-2 policy (``docker/docker-gate/gate.py``) checks the
JSON body of ``POST /containers/create`` and ``POST /containers/{name}/exec``.
Its allowlist is built from what RoboCo actually sends, so this script:

1. drives RoboCo's real argv builders with ``asyncio.create_subprocess_exec``
   captured: every agent provider's spawn (``_spawn_container``), the Secretary
   and intake live chats, each sandbox engine, the throwaway workspace
   container (online, and offline with the read-only transcript mount), and
   the exec probes;
2. runs each captured ``docker`` argv, with the environment its ``-e NAME``
   values ride in, through the real docker CLI into a stub daemon that answers
   the version handshake and records the request body instead of acting on it;
3. writes one JSON file per call to OUT_DIR (the gate's tests read
   ``tests/unit/docker_gate/recorded``).

Two rewrites keep the files stable and free of secrets: the temporary host
root this run uses becomes ``/host``, and every ``Env`` value becomes
``<value>`` (the policy never reads them). Needs the docker CLI on PATH; it
never reaches a Docker daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import h11

_HOST_ALIAS = "/host"
_AUTH_FILES = {
    ".grok": "auth.json",
    ".codex": "auth.json",
    ".gemini": "oauth_creds.json",
    ".kimi-code": "credentials/kimi-code.json",
}
_AGENT = "be-dev-1"
_STUB_API = "1.51"
_RECORDED_STATUS = 500
_OK = 200
_USAGE_ARGC = 2


@dataclass
class Captured:
    """One docker invocation RoboCo made: where from, its argv and CLI env."""

    source: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


def prepare_host(host: Path) -> None:
    """The host paths compose hands the orchestrator, with every credential
    file a spawn checks for, so each provider adds its real mounts. No
    ``~/.claude.json``: under compose the orchestrator cannot see it, so it
    never binds it."""
    home = host / "home"
    (home / ".claude").mkdir(parents=True)
    for directory, name in _AUTH_FILES.items():
        path = home / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    for directory in ("project/docs", "data/workspaces/proj/backend/be-dev-1"):
        (host / directory).mkdir(parents=True)
    os.environ.update(
        {
            "ROBOCO_HOST_PROJECT_DIR": str(host / "project"),
            "ROBOCO_HOST_DATA_DIR": str(host / "data"),
            "ROBOCO_HOST_CLAUDE_DIR": str(home / ".claude"),
            "ROBOCO_HOST_GROK_DIR": str(home / ".grok"),
            "ROBOCO_HOST_CODEX_DIR": str(home / ".codex"),
            "ROBOCO_HOST_GEMINI_DIR": str(home / ".gemini"),
            "ROBOCO_HOST_KIMI_DIR": str(home / ".kimi-code"),
            "ROBOCO_WORKSPACES_ROOT": str(host / "data/workspaces"),
            # Where the orchestrator writes a developer's tool manifest; the
            # bind names its host side, under ROBOCO_HOST_DATA_DIR.
            "ROBOCO_MANIFEST_HOST_DIR": str(host / "data/manifests"),
        }
    )


class _Recorder:
    """Stands in for ``asyncio.create_subprocess_exec``: keeps every docker
    ``run``, ``exec`` and ``pull`` argv and answers like a docker CLI that
    succeeded; with ``absent_images``, every image inspect finds nothing, so
    the caller pulls."""

    def __init__(self) -> None:
        self.source = ""
        self.calls: list[Captured] = []
        self.absent_images = False

    async def __call__(self, *argv: str, **kwargs: Any) -> AsyncMock:
        if argv[:1] == ("docker",) and argv[1:2] in (("run",), ("exec",), ("pull",)):
            env = kwargs.get("env") or {}
            extra = {k: v for k, v in env.items() if os.environ.get(k) != v}
            self.calls.append(Captured(self.source, list(argv), extra))
        proc = AsyncMock()
        absent = self.absent_images and argv[1:3] == ("image", "inspect")
        proc.returncode = 1 if absent else 0
        proc.communicate.return_value = (b"0123456789ab\n", b"")
        proc.wait.return_value = 0
        return proc


def _agent_config(provider: str) -> Any:
    from roboco.models.runtime import OrchestratorAgentConfig, SpawnGitContext

    return OrchestratorAgentConfig(
        agent_id=_AGENT,
        blueprint_path=Path("/app/agents/blueprints/be-dev-1.md"),
        model="sonnet",
        mcp_config_path=Path("/app/mcp-configs/be-dev-1.json"),
        git_context=SpawnGitContext(
            project_slug="proj", branch_name="feature/backend/ABC12345"
        ),
        provider_type=provider,
        provider_base_url="https://provider.invalid/v1",
        provider_auth_token="provider-key",
    )


async def _record_spawns(recorder: _Recorder) -> None:
    from roboco.config import settings
    from roboco.models.base import ModelProvider
    from roboco.runtime.orchestrator import AgentOrchestrator

    with patch.object(AgentOrchestrator, "_remove_container", AsyncMock()):
        for provider in ModelProvider:
            recorder.source = f"spawn:{provider.value}"
            await _new_orchestrator()._spawn_container(
                _agent_config(provider.value), "Call give_me_work().", None
            )
        # docker-compose.registry.yml: images are registry-qualified and tagged.
        with (
            patch.object(settings, "agent_image_registry", "ghcr.io/rennf93"),
            patch.object(settings, "agent_image_tag", "0.33.0"),
        ):
            for provider in (ModelProvider.ANTHROPIC, ModelProvider.GROK):
                recorder.source = f"spawn-registry:{provider.value}"
                await _new_orchestrator()._spawn_container(
                    _agent_config(provider.value), "Call give_me_work().", None
                )
            # Registry mode pulls an agent image it does not have.
            recorder.source = "pull-registry:agent"
            recorder.absent_images = True
            await _new_orchestrator()._ensure_image_present(
                "roboco-agent-dev-be", "docker/agent-dev-be.Dockerfile", "."
            )
            recorder.absent_images = False


def _new_orchestrator() -> Any:
    """An orchestrator without its constructor's I/O, and a fresh provider
    registry (each provider qualifies its image when the registry is built)."""
    from roboco.runtime.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._provider_registry = None
    orchestrator.project_root = Path.cwd()
    return orchestrator


def _record_live_chats(recorder: _Recorder) -> None:
    # Claude, Grok and one live-CLI provider: the three image and mount shapes
    # interactive_sessions.py chooses between.
    for provider in ("anthropic", "grok", "openai"):
        for chat in ("secretary", "intake"):
            recorder.calls.append(
                Captured(f"{chat}:{provider}", _live_chat_argv(chat, provider))
            )


def _live_chat_argv(chat: str, provider: str) -> list[str]:
    from roboco.agent_sdk.live_providers import (
        LIVE_CLI_PROVIDERS,
        LIVE_INTERACTIVE_IMAGE,
    )
    from roboco.runtime.orchestrator import (
        GROK_PROMPTER_IMAGE,
        GROK_SECRETARY_IMAGE,
        INTAKE_AGENT_ID,
        SECRETARY_AGENT_ID,
        AgentOrchestrator,
        _IntakeRunSpec,
        _SecretaryRunSpec,
        get_agent_image,
    )

    agent_id = SECRETARY_AGENT_ID if chat == "secretary" else INTAKE_AGENT_ID
    if provider == "grok":
        image = GROK_SECRETARY_IMAGE if chat == "secretary" else GROK_PROMPTER_IMAGE
    elif provider in LIVE_CLI_PROVIDERS:
        image = LIVE_INTERACTIVE_IMAGE[provider]
    else:
        image = get_agent_image(agent_id)
    data = os.environ["ROBOCO_HOST_DATA_DIR"]
    hosts: dict[str, str | None] = {
        "claude": os.environ["ROBOCO_HOST_CLAUDE_DIR"],
        "prompt": f"{data}/prompts-generated/{agent_id}-prompt.md",
        "workspaces": f"{data}/workspaces",
        "grok_usage": f"{data}/grok-usage/{agent_id}",
    }
    container_name = f"roboco-agent-{agent_id}"
    session_id = "11111111-1111-1111-1111-111111111111"
    api_url = "http://roboco-dispatcher-blue:8000"
    if chat == "secretary":
        return AgentOrchestrator._build_secretary_run_cmd(
            _SecretaryRunSpec(
                container_name=container_name,
                image=image,
                hosts=hosts,
                session_id=session_id,
                cwd="/data/workspaces/proj",
                cli_model="sonnet",
                api_url=api_url,
                agent_uuid="22222222-2222-2222-2222-222222222222",
                agent_token="token",
                provider_base_url=None,
                provider_auth_token=None,
                provider_type=provider,
            )
        )
    return AgentOrchestrator._build_intake_run_cmd(
        _IntakeRunSpec(
            container_name=container_name,
            image=image,
            hosts=hosts,
            session_id=session_id,
            cwd="/data/workspaces/proj",
            cli_model="sonnet",
            api_url=api_url,
            provider_base_url=None,
            provider_auth_token=None,
            provider_type=provider,
        )
    )


async def _record_sandboxes(recorder: _Recorder) -> None:
    from roboco.models.sandbox import SANDBOX_ENGINE_FEATURES, SANDBOX_ENGINES
    from roboco.runtime.orchestrator import AGENT_NETWORK
    from roboco.runtime.sandbox import SandboxProvisioner, SandboxProvisionError

    async def runner(args: list[str], _timeout: float) -> tuple[int, bytes, bytes]:
        # Every image is absent, so the provisioner pulls it first.
        if args[:2] == ["image", "inspect"]:
            return 1, b"", b"No such image"
        if args[:1] == ["pull"]:
            source = recorder.source.replace("sandbox:", "pull-sandbox:", 1)
            recorder.calls.append(Captured(source, ["docker", *args]))
            return 0, b"", b""
        if args[:1] == ["run"]:
            recorder.calls.append(Captured(recorder.source, ["docker", *args]))
            return 1, b"", b"recorded"
        return 0, b"", b""

    provisioner = SandboxProvisioner(network=AGENT_NETWORK, runner=runner)
    # A fixed password: it rides in redis's command line, and a fresh one per
    # run would change the recording every time.
    fixed_password = patch(
        "roboco.runtime.sandbox.secrets.token_hex", return_value="sandbox-password"
    )
    for name, engine in SANDBOX_ENGINES.items():
        some_feature = sorted(SANDBOX_ENGINE_FEATURES.get(name, ()))[:1]
        for features in ([], some_feature) if some_feature else ([],):
            recorder.source = f"sandbox:{name}{':features' if features else ''}"
            # The runner refuses the run once it is recorded.
            with fixed_password, contextlib.suppress(SandboxProvisionError):
                await provisioner._provision_engine(_AGENT, engine, features)


async def _record_workspace_containers(recorder: _Recorder, host: Path) -> None:
    from roboco.services import workspace_container

    clone = host / "data/workspaces/proj/backend/be-dev-1"
    transcripts = host / "home/.claude/projects/-data-workspaces-proj"
    transcripts.mkdir(parents=True)
    with (
        patch.object(
            workspace_container,
            "workspace_container_image",
            AsyncMock(return_value="sha256:" + "a" * 64),
        ),
        # A fixed container name, so a re-recording only changes what changed.
        patch.object(
            workspace_container,
            "uuid4",
            lambda: UUID("00000000-0000-4000-8000-0000000000ab"),
        ),
    ):
        recorder.source = "workspace-container:online"
        await workspace_container.run_in_workspace_container(
            clone, ["make", "quality"], timeout=60
        )
        recorder.source = "workspace-container:offline"
        await workspace_container.run_offline_in_workspace_container(
            clone,
            ["git", "commit"],
            timeout=60,
            read_only_mount=workspace_container.ReadOnlyMount(
                root=host / "home/.claude",
                local=transcripts,
                host=str(transcripts),
                target="/home/agent/.claude/projects/-data-workspaces-proj",
            ),
        )


def _record_execs(recorder: _Recorder) -> None:
    # dispatch_breaker.py and reconcile.py probe an agent, sandbox.py a
    # sandbox, always as `docker exec NAME ARGV...` with no options.
    for source, argv in (
        ("exec:agent", ["roboco-agent-be-dev-1", "printenv", "ROBOCO_AGENT_TOKEN"]),
        ("exec:sandbox", ["roboco-sandbox-pg-be-dev-1", "pg_isready"]),
    ):
        recorder.calls.append(Captured(source, ["docker", "exec", *argv]))


async def capture(host: Path) -> list[Captured]:
    recorder = _Recorder()

    async def project() -> str:
        return "roboco"

    with (
        patch("asyncio.create_subprocess_exec", recorder),
        patch("roboco.runtime.compose_labels._discover", project),
        # Where a live chat writes its MCP config; the bind names the host
        # side, which the run builds from ROBOCO_HOST_DATA_DIR.
        patch(
            "roboco.runtime.engines.interactive_sessions._LIVE_MCP_WRITE_DIR",
            host / "data" / "mcp-configs",
        ),
    ):
        await _record_spawns(recorder)
        _record_live_chats(recorder)
        await _record_sandboxes(recorder)
        await _record_workspace_containers(recorder, host)
        _record_execs(recorder)
    return recorder.calls


class _StubDaemon:
    """Answers the CLI's version handshake and an exec's container lookup,
    and records every create and exec body, refusing them so nothing runs."""

    def __init__(self) -> None:
        self.bodies: list[tuple[str, str, Any]] = []

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = h11.Connection(h11.SERVER)
        request: h11.Request | None = None
        body = b""
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                data = await reader.read(65536)
                if not data:
                    break
                conn.receive_data(data)
                continue
            if isinstance(event, h11.Request):
                request, body = event, b""
            elif isinstance(event, h11.Data):
                body += event.data
            elif isinstance(event, h11.EndOfMessage) and request is not None:
                status, payload = self._answer(request, body)
                writer.write(
                    conn.send(
                        h11.Response(
                            status_code=status,
                            headers=[
                                ("Content-Type", "application/json"),
                                ("Content-Length", str(len(payload))),
                                ("Api-Version", _STUB_API),
                                ("Ostype", "linux"),
                            ],
                        )
                    )
                    or b""
                )
                if request.method != b"HEAD":
                    writer.write(conn.send(h11.Data(data=payload)) or b"")
                writer.write(conn.send(h11.EndOfMessage()) or b"")
                await writer.drain()
                if conn.our_state is h11.MUST_CLOSE:
                    break
                conn.start_next_cycle()
            else:
                break
        writer.close()

    def _answer(self, request: h11.Request, body: bytes) -> tuple[int, bytes]:
        method = request.method.decode()
        target = request.target.decode()
        path = target.split("?", 1)[0]
        if path.endswith(("/_ping", "/version")):
            return _OK, json.dumps(
                {"ApiVersion": _STUB_API, "MinAPIVersion": "1.24", "Os": "linux"}
            ).encode()
        if method == "GET" and path.endswith("/json") and "/containers/" in path:
            return _OK, json.dumps(
                {"Id": "0" * 64, "State": {"Running": True}, "Config": {"Tty": False}}
            ).encode()
        if method == "POST" and (
            path.endswith(("/containers/create", "/exec", "/images/create"))
        ):
            self.bodies.append((method, target, json.loads(body or b"null")))
            return _RECORDED_STATUS, b'{"message": "recorded by the stub daemon"}'
        return _RECORDED_STATUS, b'{"message": "not stubbed"}'


def _alias(value: Any, host: str) -> Any:
    if isinstance(value, str):
        return value.replace(host, _HOST_ALIAS)
    if isinstance(value, list):
        return [_alias(item, host) for item in value]
    if isinstance(value, dict):
        return {key: _alias(item, host) for key, item in value.items()}
    return value


def _stable(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Env values redacted, and Binds sorted: the CLI emits them from a map,
    in a different order on every run."""
    if body is None:
        return None
    env = body.get("Env")
    if isinstance(env, list):
        body["Env"] = sorted(f"{item.split('=', 1)[0]}=<value>" for item in env)
    host = body.get("HostConfig")
    if isinstance(host, dict) and isinstance(host.get("Binds"), list):
        host["Binds"] = sorted(host["Binds"])
    return body


async def replay(calls: list[Captured], host: Path, out: Path) -> None:
    daemon = _StubDaemon()
    sock = host / "stub.sock"
    server = await asyncio.start_unix_server(daemon.serve, path=str(sock))
    config = host / "docker-config"
    config.mkdir()
    out.mkdir(parents=True, exist_ok=True)
    async with server:
        for index, call in enumerate(calls):
            before = len(daemon.bodies)
            env = {
                **os.environ,
                **call.env,
                "DOCKER_HOST": f"unix://{sock}",
                "DOCKER_CONFIG": str(config),
            }
            proc = await asyncio.create_subprocess_exec(
                *call.argv,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if len(daemon.bodies) != before + 1:
                msg = f"{call.source}: no body recorded ({stderr.decode().strip()})"
                raise RuntimeError(msg)
            method, target, body = daemon.bodies[-1]
            record = {
                "source": call.source,
                "method": method,
                "target": _alias(target, str(host)),
                "body": _alias(_stable(body), str(host)),
            }
            name = f"{index:02d}-{call.source.replace(':', '-')}.json"
            (out / name).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != _USAGE_ARGC:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    out = Path(argv[1])
    # The system temp dir, not a configurable one: the stub daemon's socket
    # path must fit in a Unix socket address (108 bytes).
    with tempfile.TemporaryDirectory(prefix="roboco-gate-record-") as tmp:
        host = Path(tmp)
        prepare_host(host)
        calls = asyncio.run(capture(host))
        asyncio.run(replay(calls, host, out))
    print(f"{len(calls)} bodies written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
