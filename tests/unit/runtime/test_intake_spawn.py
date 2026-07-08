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
from uuid import UUID, uuid4

import pytest
from roboco.config import settings
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
    # A minimal orchestrator is a RUNNING one. The non-blocking spawn path
    # reads ``self._running`` after docker run to detect a mid-spawn shutdown
    # (F071); without this the post-docker-run guard would AttributeError on
    # the constructor-skipped instance.
    orch._running = True
    # Concurrent intake starts serialize on this lock; the constructor (skipped
    # here) initializes it.
    orch._intake_spawn_lock = asyncio.Lock()
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

    @pytest.mark.asyncio
    async def test_megatask_scope_resolves_explicit_project_ids_in_order(self) -> None:
        # A MegaTask spans an explicit set of (possibly unrelated) projects; the
        # slugs are resolved in the given order (the first is the primary cwd).
        ids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]

        class _FakeProjectSvc:
            async def get(self, pid: Any) -> Any:
                return SimpleNamespace(slug=f"proj-{str(pid)[0]}")

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            slugs = await AgentOrchestrator._intake_scope_slugs(
                db=object(),
                project_slug=None,
                product_id=None,
                project_ids=ids,
            )
        assert slugs == ["proj-1", "proj-2"]

    @pytest.mark.asyncio
    async def test_megatask_scope_with_unresolvable_project_raises(self) -> None:
        class _FakeProjectSvc:
            async def get(self, _pid: Any) -> Any:
                return None

        with (
            patch(
                "roboco.services.project.get_project_service",
                lambda _db: _FakeProjectSvc(),
            ),
            pytest.raises(ValueError, match="not found"),
        ):
            await AgentOrchestrator._intake_scope_slugs(
                db=object(),
                project_slug=None,
                product_id=None,
                project_ids=["11111111-1111-1111-1111-111111111111"],
            )

    @pytest.mark.asyncio
    async def test_megatask_scope_with_one_unresolvable_id_raises(self) -> None:
        # A PARTIAL failure (one of N ids invalid) must fail loud, not silently
        # clone fewer repos than the agent was told it has.
        good = "11111111-1111-1111-1111-111111111111"
        bad = "22222222-2222-2222-2222-222222222222"

        class _FakeProjectSvc:
            async def get(self, pid: Any) -> Any:
                return SimpleNamespace(slug="proj-a") if str(pid) == good else None

        with (
            patch(
                "roboco.services.project.get_project_service",
                lambda _db: _FakeProjectSvc(),
            ),
            pytest.raises(ValueError, match="not found"),
        ):
            await AgentOrchestrator._intake_scope_slugs(
                db=object(),
                project_slug=None,
                product_id=None,
                project_ids=[good, bad],
            )


# ---------------------------------------------------------------------------
# _resolve_history_digest_projects — the prompter-memory ambient's project scope
# (covers all three intake scopes: project_slug, product_id, project_ids).
# ---------------------------------------------------------------------------


class TestResolveHistoryDigestProjects:
    @pytest.mark.asyncio
    async def test_project_slug_branch_resolves_single_project(self) -> None:
        class _FakeProjectSvc:
            async def get_by_slug(self, slug: str) -> Any:
                return SimpleNamespace(slug=slug, id=uuid4())

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await AgentOrchestrator._resolve_history_digest_projects(
                object(), project_slug="roboco", product_id=None, project_ids=None
            )
        assert [p.slug for p in projects] == ["roboco"]

    @pytest.mark.asyncio
    async def test_project_slug_missing_returns_empty(self) -> None:
        class _FakeProjectSvc:
            async def get_by_slug(self, _slug: str) -> Any:
                return None

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await AgentOrchestrator._resolve_history_digest_projects(
                object(), project_slug="ghost", product_id=None, project_ids=None
            )
        assert projects == []

    @pytest.mark.asyncio
    async def test_product_id_branch_delegates_to_ambient_product_projects(
        self,
    ) -> None:
        sentinel = [SimpleNamespace(slug="p1", id=uuid4())]

        async def _fake_product_projects(_db: Any, product_id: str) -> list[Any]:
            assert product_id == "prod-1"
            return sentinel

        with patch.object(
            AgentOrchestrator, "_ambient_product_projects", _fake_product_projects
        ):
            projects = await AgentOrchestrator._resolve_history_digest_projects(
                object(), project_slug=None, product_id="prod-1", project_ids=None
            )
        assert projects is sentinel

    @pytest.mark.asyncio
    async def test_project_ids_branch_preserves_order_and_skips_missing(
        self,
    ) -> None:
        good1 = "11111111-1111-1111-1111-111111111111"
        missing = "22222222-2222-2222-2222-222222222222"
        good2 = "33333333-3333-3333-3333-333333333333"

        class _FakeProjectSvc:
            async def get(self, pid: Any) -> Any:
                if str(pid) == missing:
                    return None
                return SimpleNamespace(slug=f"proj-{str(pid)[0]}", id=pid)

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await AgentOrchestrator._resolve_history_digest_projects(
                object(),
                project_slug=None,
                product_id=None,
                project_ids=[good1, missing, good2],
            )
        # Order preserved; the unresolvable id is skipped, not raised — this is
        # a best-effort ambient resolver, not the hard clone-scope resolver.
        assert [p.slug for p in projects] == ["proj-1", "proj-3"]

    @pytest.mark.asyncio
    async def test_no_scope_given_returns_empty(self) -> None:
        projects = await AgentOrchestrator._resolve_history_digest_projects(
            object(), project_slug=None, product_id=None, project_ids=None
        )
        assert projects == []


# ---------------------------------------------------------------------------
# _resolve_history_digest_ambient — best-effort: any failure returns None.
# ---------------------------------------------------------------------------


class TestResolveHistoryDigestAmbient:
    @pytest.mark.asyncio
    async def test_failure_returns_none_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()

        def _boom() -> Any:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("roboco.db.base.get_session_factory", _boom)

        result = await orch._resolve_history_digest_ambient("roboco")
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_ambient_projects — the conventions ambient's project scope. Mirrors
# _resolve_history_digest_projects's shape, including the MegaTask project_ids
# scope (the pre-existing gap: this resolver used to stop at project_slug /
# product_id / task_id and never saw a MegaTask's explicit project_ids).
# ---------------------------------------------------------------------------


class TestResolveAmbientProjects:
    @pytest.mark.asyncio
    async def test_project_slug_branch_resolves_single_project(self) -> None:
        orch = _make_minimal_orchestrator()

        class _FakeProjectSvc:
            async def get_by_slug(self, slug: str) -> Any:
                return SimpleNamespace(slug=slug, id=uuid4())

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await orch._resolve_ambient_projects(
                object(),
                project_slug="roboco",
                task_id=None,
                product_id=None,
            )
        assert [p.slug for p in projects] == ["roboco"]

    @pytest.mark.asyncio
    async def test_project_slug_missing_returns_empty(self) -> None:
        orch = _make_minimal_orchestrator()

        class _FakeProjectSvc:
            async def get_by_slug(self, _slug: str) -> Any:
                return None

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await orch._resolve_ambient_projects(
                object(),
                project_slug="ghost",
                task_id=None,
                product_id=None,
            )
        assert projects == []

    @pytest.mark.asyncio
    async def test_product_id_branch_delegates_to_ambient_product_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        sentinel = [SimpleNamespace(slug="p1", id=uuid4())]

        async def _fake_product_projects(_db: Any, product_id: str) -> list[Any]:
            assert product_id == "prod-1"
            return sentinel

        # Patched on the instance (not the class): _ambient_product_projects is
        # a staticmethod, so accessing it via `self.` never binds `self` — but a
        # plain function patched onto the *class* would, since it's no longer
        # wrapped in `staticmethod`. Patching the instance attribute sidesteps
        # the descriptor lookup entirely.
        monkeypatch.setattr(orch, "_ambient_product_projects", _fake_product_projects)
        projects = await orch._resolve_ambient_projects(
            object(),
            project_slug=None,
            task_id=None,
            product_id="prod-1",
        )
        assert projects is sentinel

    @pytest.mark.asyncio
    async def test_project_ids_branch_preserves_order_and_skips_missing(
        self,
    ) -> None:
        """The MegaTask scope: an explicit project_ids list must resolve into
        projects (order preserved, unresolvable ids skipped) — this is the gap
        fix, mirroring the history digest resolver's own project_ids branch."""
        orch = _make_minimal_orchestrator()
        good1 = "11111111-1111-1111-1111-111111111111"
        missing = "22222222-2222-2222-2222-222222222222"
        good2 = "33333333-3333-3333-3333-333333333333"

        class _FakeProjectSvc:
            async def get(self, pid: Any) -> Any:
                if str(pid) == missing:
                    return None
                return SimpleNamespace(slug=f"proj-{str(pid)[0]}", id=pid)

        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await orch._resolve_ambient_projects(
                object(),
                project_slug=None,
                task_id=None,
                product_id=None,
                project_ids=[good1, missing, good2],
            )
        assert [p.slug for p in projects] == ["proj-1", "proj-3"]

    @pytest.mark.asyncio
    async def test_project_ids_takes_priority_over_other_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A MegaTask spawn passes project_slug/product_id as None in practice,
        but the resolver must still prefer the explicit project_ids set over a
        stray product_id/task_id if both were somehow present."""
        orch = _make_minimal_orchestrator()
        pid = "11111111-1111-1111-1111-111111111111"

        class _FakeProjectSvc:
            async def get(self, _pid: Any) -> Any:
                return SimpleNamespace(slug="from-ids", id=_pid)

        async def _fail_product_projects(*_a: Any, **_k: Any) -> list[Any]:
            raise AssertionError("product_id branch must not run")

        monkeypatch.setattr(orch, "_ambient_product_projects", _fail_product_projects)
        with patch(
            "roboco.services.project.get_project_service",
            lambda _db: _FakeProjectSvc(),
        ):
            projects = await orch._resolve_ambient_projects(
                object(),
                project_slug=None,
                task_id=None,
                product_id="prod-should-be-ignored",
                project_ids=[pid],
            )
        assert [p.slug for p in projects] == ["from-ids"]

    @pytest.mark.asyncio
    async def test_no_scope_given_returns_empty(self) -> None:
        orch = _make_minimal_orchestrator()
        projects = await orch._resolve_ambient_projects(
            object(),
            project_slug=None,
            task_id=None,
            product_id=None,
        )
        assert projects == []


# ---------------------------------------------------------------------------
# _resolve_conventions_ambient — flag-gated + best-effort, now MegaTask-aware.
# ---------------------------------------------------------------------------


class TestResolveConventionsAmbient:
    @pytest.mark.asyncio
    async def test_flag_off_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        orch = _make_minimal_orchestrator()
        monkeypatch.setattr(settings, "conventions_enabled", False)

        result = await orch._resolve_conventions_ambient(
            "roboco", project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_failure_returns_none_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        monkeypatch.setattr(settings, "conventions_enabled", True)

        def _boom() -> Any:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("roboco.db.base.get_session_factory", _boom)

        result = await orch._resolve_conventions_ambient(
            None, project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_project_ids_scope_reaches_conventions_layer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The MegaTask project_ids scope must flow all the way through to
        conventions_ambient_layer — the actual gap this fix closes."""
        orch = _make_minimal_orchestrator()
        monkeypatch.setattr(settings, "conventions_enabled", True)

        class _FakeFactory:
            def __call__(self) -> Any:
                return self

            async def __aenter__(self) -> Any:
                return "fake-db"

            async def __aexit__(self, *_a: Any) -> None:
                return None

        monkeypatch.setattr("roboco.db.base.get_session_factory", _FakeFactory)

        sentinel_projects = [SimpleNamespace(slug="proj-1")]
        captured: dict[str, Any] = {}

        async def _fake_resolve_projects(_self: Any, _db: Any, **kwargs: Any) -> Any:
            captured["kwargs"] = kwargs
            return sentinel_projects

        async def _fake_layer(_db: Any, projects: Any) -> str:
            captured["projects"] = projects
            return "RENDERED BLOCK"

        monkeypatch.setattr(
            AgentOrchestrator, "_resolve_ambient_projects", _fake_resolve_projects
        )
        monkeypatch.setattr(
            "roboco.agents.factories._base.conventions_ambient_layer", _fake_layer
        )

        result = await orch._resolve_conventions_ambient(
            None, project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        assert result == "RENDERED BLOCK"
        assert captured["kwargs"]["project_ids"] == [
            "11111111-1111-1111-1111-111111111111"
        ]
        assert captured["projects"] is sentinel_projects


# ---------------------------------------------------------------------------
# _resolve_intake_ambient — must forward project_ids to BOTH sub-resolvers.
# Regression test for the gap: it used to thread project_ids only to the
# history-digest resolver, leaving a MegaTask intake with no conventions block.
# ---------------------------------------------------------------------------


class TestResolveIntakeAmbientThreadsProjectIds:
    @pytest.mark.asyncio
    async def test_project_ids_forwarded_to_conventions_and_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        conventions_calls: list[dict[str, Any]] = []
        history_calls: list[dict[str, Any]] = []

        async def _conventions(_slug: Any, **kwargs: Any) -> str | None:
            conventions_calls.append(kwargs)
            return "CONVENTIONS"

        async def _history(_slug: Any, **kwargs: Any) -> str | None:
            history_calls.append(kwargs)
            return "HISTORY"

        monkeypatch.setattr(orch, "_resolve_conventions_ambient", _conventions)
        monkeypatch.setattr(orch, "_resolve_history_digest_ambient", _history)

        result = await orch._resolve_intake_ambient(
            None,
            product_id=None,
            project_ids=["11111111-1111-1111-1111-111111111111"],
        )

        assert result == "CONVENTIONS\n\n---\n\nHISTORY"
        assert conventions_calls == [
            {
                "product_id": None,
                "project_ids": ["11111111-1111-1111-1111-111111111111"],
            }
        ]
        assert history_calls == [
            {
                "product_id": None,
                "project_ids": ["11111111-1111-1111-1111-111111111111"],
            }
        ]


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

    async def _clone(_p: Any, _pr: Any, _pids: Any = None) -> tuple[str, list[str]]:
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
        lambda *_args, **_kwargs: Path("/tmp/intake-1-prompt.md"),
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
        # A MegaTask scope cannot combine with a single-project scope.
        with pytest.raises(ValueError, match="exactly one"):
            await orch.spawn_intake_session(
                "s", project_slug="roboco", project_ids=["p1"]
            )

    @pytest.mark.asyncio
    async def test_spawn_accepts_megatask_project_ids_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)

        instance = await orch.spawn_intake_session(
            "sess-mega", project_ids=["11111111-1111-1111-1111-111111111111"]
        )
        assert orch._instances[INTAKE_AGENT_ID] is instance
        assert run_calls  # the container actually launched for the MegaTask scope

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
    async def test_spawn_merges_conventions_and_history_ambient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The composed prompt's ambient is the conventions + history-digest
        blocks joined with compose_prompt's own layer separator."""
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)

        async def _conventions(*_a: Any, **_k: Any) -> str | None:
            return "CONVENTIONS BLOCK"

        async def _history(*_a: Any, **_k: Any) -> str | None:
            return "HISTORY BLOCK"

        monkeypatch.setattr(orch, "_resolve_conventions_ambient", _conventions)
        monkeypatch.setattr(orch, "_resolve_history_digest_ambient", _history)

        captured: dict[str, Any] = {}

        def _spy_prompt(*_args: Any, **kwargs: Any) -> Path:
            captured["ambient"] = kwargs.get("ambient")
            return Path("/tmp/intake-1-prompt.md")

        monkeypatch.setattr(orch, "_generate_composed_prompt", _spy_prompt)

        await orch.spawn_intake_session("sess-merge", project_slug="roboco")

        assert captured["ambient"] == "CONVENTIONS BLOCK\n\n---\n\nHISTORY BLOCK"

    @pytest.mark.asyncio
    async def test_spawn_ambient_none_when_both_resolvers_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)

        async def _none(*_a: Any, **_k: Any) -> str | None:
            return None

        monkeypatch.setattr(orch, "_resolve_conventions_ambient", _none)
        monkeypatch.setattr(orch, "_resolve_history_digest_ambient", _none)

        captured: dict[str, Any] = {}

        def _spy_prompt(*_args: Any, **kwargs: Any) -> Path:
            captured["ambient"] = kwargs.get("ambient")
            return Path("/tmp/intake-1-prompt.md")

        monkeypatch.setattr(orch, "_generate_composed_prompt", _spy_prompt)

        await orch.spawn_intake_session("sess-no-ambient", project_slug="roboco")

        assert captured["ambient"] is None

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


class TestConcurrentSpawnSerialization:
    """Two concurrent intake starts must serialize — the intake agent id is a
    single fixed id, so two ``docker run --name roboco-agent-prompter`` calls and
    two ``_instances[INTAKE_AGENT_ID]`` writes racing orphan a container + relay.
    The spawn body (reap-prior → clone → docker run → register) must run under
    a per-agent lock so the second start only begins once the first has fully
    registered (or been reaped).
    """

    @pytest.mark.asyncio
    async def test_concurrent_intake_spawns_do_not_interleave(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = _make_minimal_orchestrator()
        _wire_spawn_mocks(monkeypatch, orch, run_calls=[])

        # Reap the prior instance on a concurrent start: mock stop_agent so the
        # second spawn's reap doesn't need the real self._lock (not set on the
        # minimal orchestrator). Records that the prior instance was reaped.
        reaped: list[str] = []

        async def _stop(aid: str, **_kw: Any) -> None:
            reaped.append(aid)

        monkeypatch.setattr(orch, "stop_agent", _stop)

        # Instrument the first await inside the spawn body (the scope clone) to
        # measure how many spawns are inside the body at once. With a serializing
        # lock the second spawn is parked on lock.acquire() and can't enter clone
        # until the first releases (after fully registering) -> max depth 1.
        # Without the lock both spawns reach clone concurrently -> max depth 2.
        in_clone = 0
        max_depth = 0

        async def _clone(*_a: Any, **_kw: Any) -> tuple[str, list[str]]:
            nonlocal in_clone, max_depth
            in_clone += 1
            max_depth = max(max_depth, in_clone)
            await asyncio.sleep(0)  # yield so the other spawn may enter if not locked
            in_clone -= 1
            return "/data/workspaces/roboco/board/intake-1", ["/cwd"]

        monkeypatch.setattr(orch, "_clone_intake_scope", _clone)

        await asyncio.gather(
            orch.spawn_intake_session("sess-a", project_slug="roboco"),
            orch.spawn_intake_session("sess-b", project_slug="roboco"),
        )

        assert max_depth == 1  # serialized: never two spawns in the body at once
        # The second start reaped the first's registered instance (proves the two
        # spawns ran in order, not concurrently clobbering the registry).
        assert reaped == [INTAKE_AGENT_ID]


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


# ---------------------------------------------------------------------------
# Non-blocking intake spawn must not orphan a container if shutdown arrives
# between ``docker run`` and _instances registration: without a post-docker-run
# shutdown check the just-started container is never recorded so leaks.
# ---------------------------------------------------------------------------


class TestSpawnIntakeShutdownNoOrphan:
    @pytest.mark.asyncio
    async def test_shutdown_mid_spawn_removes_container_and_skips_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """docker run completes, THEN the orchestrator begins shutting down
        (``_running`` flips to False) before the registration line. The just-
        started container must be removed and NOT registered — otherwise it is
        orphaned (live, untracked by stop())."""
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)
        removed: list[str] = []

        async def _remove(name: str, **_kw: Any) -> None:
            removed.append(name)

        async def _run(cmd: list[str]) -> str:
            run_calls.append(cmd)
            # Shutdown arrives AFTER docker run started the container but BEFORE
            # the registration line runs.
            orch._running = False
            return "containerid0123456789"

        monkeypatch.setattr(orch, "_run_container_cmd", _run)
        monkeypatch.setattr(orch, "_remove_container", _remove)

        registry = prompter_live.get_live_registry()
        pushed: list[tuple[str, dict[str, Any]]] = []
        closed: list[str] = []
        monkeypatch.setattr(registry, "push", lambda sid, ev: pushed.append((sid, ev)))
        monkeypatch.setattr(registry, "close", closed.append)
        registry.open("sess-orphan", INTAKE_AGENT_ID)

        await orch._spawn_intake_container_guarded(
            "sess-orphan",
            project_slug="roboco",
            product_id=None,
            initial_message=None,
        )

        # The just-started container was removed by name (not orphaned). Two
        # removes: the pre-spawn reap of any stale container, then the
        # post-docker-run shutdown guard reaping the just-started one. Without
        # the guard there is only ONE remove (the pre-spawn reap) and the
        # just-started container is orphaned — so asserting two proves the guard
        # ran.
        assert removed == [
            f"roboco-agent-{INTAKE_AGENT_ID}",
            f"roboco-agent-{INTAKE_AGENT_ID}",
        ]
        # No instance registered — stop()'s _instances iteration has already
        # run, so a registration now would land a live container nothing stops.
        assert INTAKE_AGENT_ID not in orch._instances
        # Shutdown is not a user-facing failure: the relay closes silently,
        # no error pushed to the SSE stream.
        assert pushed == []
        assert closed == ["sess-orphan"]

    @pytest.mark.asyncio
    async def test_running_spawn_registers_normally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: when the orchestrator stays running, the spawn registers the
        instance as before — the shutdown guard does not fire on a healthy spawn."""
        orch = _make_minimal_orchestrator()
        run_calls: list[list[str]] = []
        _wire_spawn_mocks(monkeypatch, orch, run_calls)
        # _wire_spawn_mocks' _remove_container is a no-op; override to record.
        removed: list[str] = []

        async def _remove(name: str, **_kw: Any) -> None:
            removed.append(name)

        monkeypatch.setattr(orch, "_remove_container", _remove)

        instance = await orch.spawn_intake_session("sess-ok", project_slug="roboco")

        assert orch._instances[INTAKE_AGENT_ID] is instance
        # The pre-spawn reap remove is the only remove call (the shutdown guard
        # did NOT remove the just-started container).
        assert removed == [f"roboco-agent-{INTAKE_AGENT_ID}"]
