"""ContentActions.request_sandbox — the on-demand sandbox DB/Redis/Mongo verb.

Guard matrix (flag off / no active task / no project / not opted in / subset
violation / orchestrator unavailable), the success envelope payload shape, and
cross-agent isolation (ensure_sandbox is always called with the CALLER's own
resolved slug, never another agent's).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.sandbox import SandboxConnection, SandboxInfo
from roboco.runtime.sandbox import SandboxProvisionError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_actions(
    *,
    task_obj: MagicMock | None,
    orchestrator: AsyncMock | None,
) -> tuple[ContentActions, MagicMock]:
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = task_obj
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        orchestrator=orchestrator,
    )
    return ContentActions(deps), task


def _task(project_id: object | None = uuid4()) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.project_id = project_id
    t.status = "in_progress"
    return t


def _stub_project(
    monkeypatch: pytest.MonkeyPatch,
    services: list[str] | None,
    extensions: dict[str, list[str]] | None = None,
) -> None:
    project = MagicMock(sandbox_services=services, sandbox_extensions=extensions)
    project_service = MagicMock()
    project_service.get = AsyncMock(return_value=project)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_service
    )


def _sandbox_info(
    features: tuple[str, ...] = (),
) -> SandboxInfo:
    return SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="roboco-sandbox-pg-dev-1",
                port=5432,
                password="pw",
                user="sandbox",
                database="sandbox",
                features=features,
            )
        }
    )


# ---------------------------------------------------------------------------
# Guard matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_refuses_before_task_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    actions, task = _make_actions(task_obj=None, orchestrator=None)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"
    task.get_active_task_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_active_task_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    actions, _task_svc = _make_actions(task_obj=None, orchestrator=None)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "give_me_work" in (env.remediate or "")


@pytest.mark.asyncio
async def test_task_without_project_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    actions, _task_svc = _make_actions(
        task_obj=_task(project_id=None), orchestrator=None
    )

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_project_not_opted_in_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=None)
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=None)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "not opted" in (env.message or "")


@pytest.mark.asyncio
async def test_requested_service_outside_opted_set_names_allowed_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=None)

    env = await actions.request_sandbox(agent_id=uuid4(), services=["redis"])

    assert env.error == "invalid_state"
    assert "postgres" in (env.remediate or "")


@pytest.mark.asyncio
async def test_orchestrator_unavailable_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=None)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "retry" in (env.remediate or "").lower()


@pytest.mark.asyncio
async def test_provision_failure_surfaces_as_retryable_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.side_effect = SandboxProvisionError("image pull failed")
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "provisioning failed" in (env.message or "")


# ---------------------------------------------------------------------------
# Success path — envelope payload shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_returns_creds_in_evidence_with_env_subdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(agent_id=uuid4())

    expected_port = 5432
    assert env.error is None
    assert env.evidence is not None
    payload = env.evidence["postgres"]
    assert payload["host"] == "roboco-sandbox-pg-dev-1"
    assert payload["port"] == expected_port
    assert payload["user"] == "sandbox"
    assert payload["password"] == "pw"
    assert payload["database"] == "sandbox"
    assert payload["env"]["ROBOCO_TEST_DB_HOST"] == "roboco-sandbox-pg-dev-1"
    assert payload["env"]["ROBOCO_TEST_DB_PASSWORD"] == "pw"
    task_svc.heartbeat.assert_awaited_once()


@pytest.mark.asyncio
async def test_omitted_services_requests_full_opted_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres", "redis"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    await actions.request_sandbox(agent_id=uuid4())

    orch.ensure_sandbox.assert_awaited_once()
    called_services = orch.ensure_sandbox.call_args.args[1]
    assert sorted(called_services) == ["postgres", "redis"]


@pytest.mark.asyncio
async def test_ensure_sandbox_called_with_full_opted_set_not_just_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT 1 fix: the verb always passes the project's whole opted-in set
    (not just this call's ``services`` subset) as ensure_sandbox's ``opted``
    argument, so a superset request later in the session can never trigger a
    fresh provision() that tears down the agent's live sandbox."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres", "redis"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    await actions.request_sandbox(agent_id=uuid4(), services=["postgres"])

    called_requested = orch.ensure_sandbox.call_args.args[1]
    called_opted = orch.ensure_sandbox.call_args.args[2]
    assert called_requested == ["postgres"]
    assert sorted(called_opted) == ["postgres", "redis"]


@pytest.mark.asyncio
async def test_response_payload_filtered_to_requested_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ensure_sandbox` provisions the full opted set under the hood; the
    verb's response only surfaces what THIS call actually asked for."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres", "redis"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="h", port=5432, password="pw", user="sandbox", database="sandbox"
            ),
            "redis": SandboxConnection(host="h", port=6379, password="rw"),
        }
    )
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(agent_id=uuid4(), services=["postgres"])

    assert env.error is None
    assert env.evidence is not None
    assert set(env.evidence) == {"postgres"}


# ---------------------------------------------------------------------------
# Cross-agent isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_sandbox_keyed_off_caller_own_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different callers resolve to two different ensure_sandbox slugs —
    a caller can never reach another agent's cached sandbox."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    agent_a, agent_b = uuid4(), uuid4()
    await actions.request_sandbox(agent_id=agent_a)
    await actions.request_sandbox(agent_id=agent_b)

    slugs_called = [c.args[0] for c in orch.ensure_sandbox.call_args_list]
    assert slugs_called[0] != slugs_called[1]
    assert slugs_called[0] == str(agent_a)
    assert slugs_called[1] == str(agent_b)


# ---------------------------------------------------------------------------
# Extensions — per-service additive override, allowlist-guarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extensions_additive_unioned_with_project_standing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-call extensions union with the project's standing set (bounded by
    the opted set + allowlist) and reach ensure_sandbox as the features kwarg."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(
        monkeypatch,
        services=["postgres"],
        extensions={"postgres": ["vector"]},
    )
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    await actions.request_sandbox(
        agent_id=uuid4(), extensions={"postgres": ["postgis"]}
    )

    features = orch.ensure_sandbox.call_args.kwargs["features"]
    assert features == {"postgres": ["postgis", "vector"]}


@pytest.mark.asyncio
async def test_standing_extensions_passed_with_no_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(
        monkeypatch,
        services=["postgres"],
        extensions={"postgres": ["vector"]},
    )
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    await actions.request_sandbox(agent_id=uuid4())

    assert orch.ensure_sandbox.call_args.kwargs["features"] == {"postgres": ["vector"]}


@pytest.mark.asyncio
async def test_no_extensions_passes_none_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare call (no standing, no per-call) → features=None (bare provision)."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    await actions.request_sandbox(agent_id=uuid4())

    assert orch.ensure_sandbox.call_args.kwargs["features"] is None


@pytest.mark.asyncio
async def test_extensions_rejects_plpython_names_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plpython3u is rejected at the verb with the allowlist named in remediate
    (not only at the provisioner), mirroring the unknown-service remediate."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(
        agent_id=uuid4(), extensions={"postgres": ["plpython3u"]}
    )

    assert env.error == "invalid_state"
    remediate = env.remediate or ""
    assert "vector" in remediate  # the allowlist is named
    orch.ensure_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_extensions_for_non_opted_service_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(monkeypatch, services=["postgres"])
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info()
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(
        agent_id=uuid4(), extensions={"redis": ["search"]}
    )

    assert env.error == "invalid_state"
    orch.ensure_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_available_extensions_surfaced_in_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evidence payload carries available_extensions so the agent doesn't
    guess what was activated."""
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    _stub_project(
        monkeypatch,
        services=["postgres"],
        extensions={"postgres": ["vector", "postgis"]},
    )
    orch = AsyncMock()
    orch.ensure_sandbox.return_value = _sandbox_info(features=("postgis", "vector"))
    actions, _task_svc = _make_actions(task_obj=_task(), orchestrator=orch)

    env = await actions.request_sandbox(agent_id=uuid4())

    assert env.error is None
    assert env.evidence is not None
    assert env.evidence["postgres"]["available_extensions"] == ["postgis", "vector"]
