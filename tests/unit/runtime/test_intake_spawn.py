"""The persistent intake (prompter) live-session spawn/reap path.

The intake agent is not task-driven: ``spawn_intake_session`` launches a
long-lived Agent-SDK driver container (image ENTRYPOINT, NOT ``claude -p``),
clones the chat scope's repo(s), and registers the live relay session. These
tests cover the docker-command construction, scope resolution, and the
spawn/reap orchestration with docker + clone mocked (no daemon, no NAS).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from roboco.runtime.orchestrator import (
    INTAKE_AGENT_ID,
    AgentInstance,
    AgentOrchestrator,
    _IntakeRunSpec,
)
from roboco.services import prompter_live


def _make_minimal_orchestrator() -> AgentOrchestrator:
    """AgentOrchestrator with constructor I/O skipped; _instances ready."""
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._bg_tasks = set()
    return orch


def _spec(**overrides: Any) -> _IntakeRunSpec:
    base: dict[str, Any] = {
        "container_name": "roboco-agent-intake-1",
        "image": "roboco-agent-prompter",
        "hosts": {
            "claude": "/home/runner/.claude",
            "prompt": "/data/prompts-generated/intake-1-prompt.md",
            "workspaces": "/data/workspaces",
        },
        "session_id": "sess-abc",
        "cwd": "/data/workspaces/roboco/board/intake-1",
        "cli_model": "claude-opus-4-6",
        "api_url": "http://roboco-orchestrator:8000",
        "provider_base_url": None,
        "provider_auth_token": None,
    }
    base.update(overrides)
    return _IntakeRunSpec(**base)


@pytest.fixture(autouse=True)
def _fresh_registry() -> Any:
    """Isolate the process-wide live registry per test."""
    prev = prompter_live._RegistryHolder.instance
    prompter_live._RegistryHolder.instance = prompter_live.PrompterLiveRegistry()
    yield
    prompter_live._RegistryHolder.instance = prev


# ---------------------------------------------------------------------------
# _build_intake_run_cmd — the pure docker-argv builder.
# ---------------------------------------------------------------------------


class TestBuildIntakeRunCmd:
    def test_image_is_last_and_no_claude_cli_args(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(_spec())
        assert cmd[-1] == "roboco-agent-prompter"
        # The image ENTRYPOINT is the driver — none of the claude CLI flags
        # the task-driven path appends may appear here.
        for flag in (
            "-p",
            "--model",
            "--system-prompt-file",
            "--mcp-config",
            "--tools",
        ):
            assert flag not in cmd, f"{flag} must not be in the intake run cmd"

    def test_no_workdir_settings_or_manifest_mounts(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(_spec())
        joined = " ".join(cmd)
        assert "-w" not in cmd  # driver sets cwd via ROBOCO_WORKSPACE/the SDK
        assert "settings.json" not in joined  # no hook mount (driver owns 9000)
        assert "mcp-config.json" not in joined  # MCP-free live agent
        assert "tool-manifest.json" not in joined

    def test_env_carries_session_workspace_and_api(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(_spec())
        assert "ROBOCO_PROMPTER_SESSION_ID=sess-abc" in cmd
        assert "ROBOCO_WORKSPACE=/data/workspaces/roboco/board/intake-1" in cmd
        assert "ROBOCO_API_URL=http://roboco-orchestrator:8000" in cmd
        assert "ROBOCO_AGENT_ID=intake-1" in cmd
        assert "CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-4-6" in cmd

    def test_mounts_prompt_and_workspaces(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(_spec())
        assert (
            "/data/prompts-generated/intake-1-prompt.md:/app/system-prompt.md:ro" in cmd
        )
        assert "/data/workspaces:/data/workspaces" in cmd

    def test_anthropic_default_omits_provider_env(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(_spec())
        joined = " ".join(cmd)
        assert "ANTHROPIC_BASE_URL" not in joined
        assert "ANTHROPIC_AUTH_TOKEN" not in joined

    def test_non_anthropic_injects_provider_env(self) -> None:
        cmd = AgentOrchestrator._build_intake_run_cmd(
            _spec(provider_base_url="http://ollama:11434/v1", provider_auth_token="tok")
        )
        assert "ANTHROPIC_BASE_URL=http://ollama:11434/v1" in cmd
        assert "ANTHROPIC_AUTH_TOKEN=tok" in cmd


# ---------------------------------------------------------------------------
# _intake_scope_slugs — project XOR product resolution.
# ---------------------------------------------------------------------------


class TestIntakeScopeSlugs:
    @pytest.mark.asyncio
    async def test_project_scope_returns_single_slug(self) -> None:
        slugs = await AgentOrchestrator._intake_scope_slugs(
            db=object(), project_slug="roboco", product_id=None
        )
        assert slugs == ["roboco"]

    @pytest.mark.asyncio
    async def test_product_scope_resolves_distinct_projects_in_order(self) -> None:
        # distinct_project_ids returns UUIDs in deterministic team order; the
        # primary (cwd) is the first, so order must be preserved (not sorted).
        ids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]

        class _FakeProduct:
            def __init__(self, _db: Any) -> None: ...
            async def distinct_project_ids(self, _pid: Any) -> list[Any]:
                return [UUID(i) for i in ids]

        class _FakeProjectSvc:
            async def get(self, pid: Any) -> Any:
                return SimpleNamespace(slug=f"proj-{str(pid)[0]}")

        with (
            patch("roboco.services.product.ProductService", _FakeProduct),
            patch(
                "roboco.services.project.get_project_service",
                lambda _db: _FakeProjectSvc(),
            ),
        ):
            slugs = await AgentOrchestrator._intake_scope_slugs(
                db=object(),
                project_slug=None,
                product_id="33333333-3333-3333-3333-333333333333",
            )
        assert slugs == ["proj-1", "proj-2"]

    @pytest.mark.asyncio
    async def test_product_with_no_projects_raises(self) -> None:
        class _FakeProduct:
            def __init__(self, _db: Any) -> None: ...
            async def distinct_project_ids(self, _pid: Any) -> list[Any]:
                return []

        with (
            patch("roboco.services.product.ProductService", _FakeProduct),
            patch("roboco.services.project.get_project_service", lambda _db: object()),
            pytest.raises(ValueError, match="no projects"),
        ):
            await AgentOrchestrator._intake_scope_slugs(
                db=object(),
                project_slug=None,
                product_id="33333333-3333-3333-3333-333333333333",
            )


# ---------------------------------------------------------------------------
# spawn_intake_session / reap_intake_session — orchestration (docker mocked).
# ---------------------------------------------------------------------------


def _fake_route() -> SimpleNamespace:
    return SimpleNamespace(
        provider_type=SimpleNamespace(value="anthropic"),
        model_name="opus",
        base_url=None,
        auth_token=None,
    )


def _wire_spawn_mocks(
    monkeypatch: pytest.MonkeyPatch,
    orch: AgentOrchestrator,
    run_calls: list[list[str]],
) -> None:
    """Patch every external boundary spawn_intake_session touches."""

    async def _clone(_p: Any, _pr: Any) -> tuple[str, list[str]]:
        return "/data/workspaces/roboco/board/intake-1", [
            "/data/workspaces/roboco/board/intake-1"
        ]

    async def _route(_aid: str) -> Any:
        return _fake_route()

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    async def _run(cmd: list[str]) -> str:
        run_calls.append(cmd)
        return "containerid0123456789"

    monkeypatch.setattr(orch, "_clone_intake_scope", _clone)
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_ensure_agent_image", _noop)
    monkeypatch.setattr(orch, "_remove_container", _noop)
    monkeypatch.setattr(orch, "_run_container_cmd", _run)
    monkeypatch.setattr(orch, "_fire_audit", lambda **_k: None)
    monkeypatch.setattr(
        orch,
        "_generate_composed_prompt",
        lambda _aid: Path("/tmp/intake-1-prompt.md"),
    )
    monkeypatch.setattr(
        orch,
        "_resolve_intake_host_paths",
        lambda: {
            "claude": "/home/runner/.claude",
            "prompt": "/data/prompts-generated/intake-1-prompt.md",
            "workspaces": "/data/workspaces",
        },
    )


class TestSpawnIntakeSession:
    @pytest.mark.asyncio
    async def test_spawn_registers_session_and_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)

        instance = await orch.spawn_intake_session("sess-1", project_slug="roboco")

        # Live relay session opened for the container.
        session = prompter_live.get_live_registry().get("sess-1")
        assert session is not None
        assert session.agent_id == INTAKE_AGENT_ID
        # Orchestrator instance tracked and marked active.
        assert orch._instances[INTAKE_AGENT_ID] is instance
        assert instance.container_id == "containerid0123456789"
        # The cloned cwd reached the docker cmd.
        assert "ROBOCO_WORKSPACE=/data/workspaces/roboco/board/intake-1" in run_calls[0]

    @pytest.mark.asyncio
    async def test_scope_must_be_exactly_one(self) -> None:
        orch = _make_minimal_orchestrator()
        with pytest.raises(ValueError, match="exactly one"):
            await orch.spawn_intake_session("s", project_slug="roboco", product_id="p")
        with pytest.raises(ValueError, match="exactly one"):
            await orch.spawn_intake_session("s")

    @pytest.mark.asyncio
    async def test_spawn_reaps_prior_session_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)

        stopped: list[str] = []

        async def _stop(aid: str, **_kw: Any) -> None:
            stopped.append(aid)

        monkeypatch.setattr(orch, "stop_agent", _stop)
        # A prior live container already registered for this agent.
        orch._instances[INTAKE_AGENT_ID] = AgentInstance(agent_id=INTAKE_AGENT_ID)

        await orch.spawn_intake_session("sess-2", project_slug="roboco")
        assert stopped == [INTAKE_AGENT_ID]  # the old one was reaped first

    @pytest.mark.asyncio
    async def test_initial_message_is_scheduled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)
        scheduled: list[tuple[str, str]] = []
        monkeypatch.setattr(
            orch,
            "_schedule_intake_first_message",
            lambda sid, text: scheduled.append((sid, text)),
        )

        await orch.spawn_intake_session(
            "sess-3", project_slug="roboco", initial_message="build X"
        )
        assert scheduled == [("sess-3", "build X")]


class TestStartIntakeSession:
    """Non-blocking start: relay opens synchronously, spawn runs in the background."""

    @pytest.mark.asyncio
    async def test_opens_relay_now_and_schedules_spawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        spawned: list[str] = []

        async def _spawn(session_id: str, **_kw: Any) -> Any:
            spawned.append(session_id)
            return AgentInstance(agent_id=INTAKE_AGENT_ID)

        monkeypatch.setattr(orch, "_spawn_intake_container", _spawn)

        await orch.start_intake_session("sess-A", project_slug="roboco")

        # Relay is open the instant start returns — the SSE stream can connect
        # before the (slow) container spawn finishes.
        assert prompter_live.get_live_registry().get("sess-A") is not None
        await asyncio.sleep(0)  # let the scheduled bg spawn run
        assert spawned == ["sess-A"]

    @pytest.mark.asyncio
    async def test_rejects_bad_scope(self) -> None:
        orch = _make_minimal_orchestrator()
        with pytest.raises(ValueError, match="exactly one"):
            await orch.start_intake_session("s", project_slug="r", product_id="p")


class TestSpawnGuarded:
    """A background spawn failure surfaces on the relay instead of dying silently."""

    @pytest.mark.asyncio
    async def test_failure_pushes_error_and_closes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        registry = prompter_live.get_live_registry()
        registry.open("sess-B", INTAKE_AGENT_ID)

        pushed: list[tuple[str, dict[str, Any]]] = []
        closed: list[str] = []

        async def _boom(_session_id: str, **_kw: Any) -> Any:
            raise RuntimeError("clone exploded")

        def _push(sid: str, ev: dict[str, Any]) -> bool:
            pushed.append((sid, ev))
            return True

        monkeypatch.setattr(orch, "_spawn_intake_container", _boom)
        monkeypatch.setattr(registry, "push", _push)
        monkeypatch.setattr(registry, "close", closed.append)

        await orch._spawn_intake_container_guarded(
            "sess-B", project_slug="roboco", product_id=None, initial_message=None
        )

        assert len(pushed) == 1
        assert pushed[0][1]["kind"] == "error"
        assert "clone exploded" in pushed[0][1]["text"]
        assert closed == ["sess-B"]


class TestReapIntakeSession:
    @pytest.mark.asyncio
    async def test_reap_closes_session_and_stops_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        stopped: list[str] = []

        async def _stop(aid: str, **_kw: Any) -> None:
            stopped.append(aid)

        monkeypatch.setattr(orch, "stop_agent", _stop)
        registry = prompter_live.get_live_registry()
        registry.open("sess-x", INTAKE_AGENT_ID)

        await orch.reap_intake_session("sess-x")

        assert registry.get("sess-x") is None  # relay session closed
        assert stopped == [INTAKE_AGENT_ID]


class TestDeliverWhenReady:
    @pytest.mark.asyncio
    async def test_retries_until_receiver_is_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        registry = prompter_live.get_live_registry()
        succeed_on = 2  # fails once, then succeeds
        attempts = {"n": 0}

        async def _deliver(_sid: str, _text: str) -> bool:
            attempts["n"] += 1
            return attempts["n"] >= succeed_on

        monkeypatch.setattr(registry, "deliver", _deliver)

        await orch._deliver_when_ready("sess-y", "hi", attempts=5, delay=0)
        assert attempts["n"] == succeed_on  # stopped as soon as delivery succeeded
