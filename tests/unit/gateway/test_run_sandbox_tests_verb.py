"""ContentActions.run_sandbox_tests — the Token Factory Sandboxes verb.

Guard matrix (flag off / no active task / no Nebius key / team unresolved /
workspace missing / git-archive failure / archive oversize / sandbox API
failure), the success envelope evidence shape, journaling, and the role
scoping (QA's do_tools only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import ModelProvider
from roboco.runtime.spawn_manifest import SpawnInputs, build_for_role
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps

_HEARTBEATS_PER_RUN = 2  # before + after the sandbox wait
_EXPECTED_COST = 0.4


def _make_actions(*, task_obj: MagicMock | None) -> tuple[ContentActions, AsyncMock]:
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
        orchestrator=None,
    )
    deps.journal.write_entry = AsyncMock()
    return ContentActions(deps), task


def _task(project_id: object | None = uuid4()) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.project_id = project_id
    t.status = "in_review"
    return t


def _stub_project(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    project = MagicMock(slug="proj-a", sandbox_services=None)
    project_service = MagicMock()
    project_service.get = AsyncMock(return_value=project)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_service
    )
    return project


def _stub_nebius_key(monkeypatch: pytest.MonkeyPatch, *, keyed: bool = True) -> None:
    provider_service = MagicMock()
    if keyed:
        row = MagicMock(
            type=ModelProvider.NEBIUS,
            id=uuid4(),
            auth_token_encrypted="fernet-blob",
        )
        provider_service.list_providers = AsyncMock(return_value=[row])
        provider_service.get_decrypted_token = AsyncMock(return_value="tf-key")
    else:
        provider_service.list_providers = AsyncMock(return_value=[])
        provider_service.get_decrypted_token = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.provider.get_provider_service", lambda _s: provider_service
    )


def _arm(monkeypatch: pytest.MonkeyPatch, *, keyed: bool = True) -> None:
    monkeypatch.setattr(settings, "token_factory_sandboxes_enabled", True)
    _stub_project(monkeypatch)
    _stub_nebius_key(monkeypatch, keyed=keyed)


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object] | None,
    error: str | None = None,
) -> AsyncMock:
    run = AsyncMock(return_value=(result, error))
    monkeypatch.setattr(
        "roboco.services.gateway.content_actions.run_sandbox_command", run
    )
    return run


def _fake_archive_ok(monkeypatch: pytest.MonkeyPatch, task_id_hex: str) -> Path:
    """Make the archive step succeed against a pre-created temp tarball."""
    monkeypatch.setattr(
        ContentActions,
        "_git_archive_workspace",
        AsyncMock(return_value=(True, "")),
    )
    archive = Path("/tmp") / f"roboco-sandbox-{task_id_hex[:12]}.tar.gz"
    archive.write_bytes(b"tarball")
    return archive


# ---------------------------------------------------------------------------
# Guard matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_refuses_before_task_lookup() -> None:
    # settings default: the flag is OFF unless a test arms it.
    actions, task = _make_actions(task_obj=None)

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "ROBOCO_TOKEN_FACTORY_SANDBOXES_ENABLED" in (env.remediate or "")
    task.get_active_task_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_active_task_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch)
    actions, _task_svc = _make_actions(task_obj=None)

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "give_me_work" in (env.remediate or "")


@pytest.mark.asyncio
async def test_no_nebius_key_refused_with_panel_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch, keyed=False)
    actions, _task_svc = _make_actions(task_obj=_task())

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "Nebius" in (env.message or "")
    assert "AI Routing" in (env.remediate or "")


@pytest.mark.asyncio
async def test_unresolved_team_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch)
    actions, task_svc = _make_actions(task_obj=_task())
    task_svc.agent_for = AsyncMock(return_value=None)

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "team" in (env.message or "")


@pytest.mark.asyncio
async def test_missing_workspace_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch)
    actions, task_svc = _make_actions(task_obj=_task())
    agent = MagicMock(team="qa")
    task_svc.agent_for = AsyncMock(return_value=agent)
    # get_worktree_path/get_clone_root_path return MagicMocks whose .exists()
    # is truthy, so point them at nonexistent real paths instead.
    missing = Path("/nonexistent/roboco-ws")
    actions._deps.workspace.get_worktree_path = MagicMock(return_value=missing)
    actions._deps.workspace.get_clone_root_path = MagicMock(
        return_value=Path("/nonexistent/roboco-clone")
    )

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "give_me_work" in (env.remediate or "")


@pytest.mark.asyncio
async def test_git_archive_failure_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)
    monkeypatch.setattr(
        ContentActions,
        "_git_archive_workspace",
        AsyncMock(return_value=(False, "unborn HEAD")),
    )
    # Point the workspace at a real dir so the archive step is reached.
    actions._deps.workspace.get_worktree_path = MagicMock(return_value=tmp_path)
    actions._deps.workspace.get_clone_root_path = MagicMock(return_value=tmp_path)

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "git archive" in (env.message or "")
    assert "commit" in (env.remediate or "")


@pytest.mark.asyncio
async def test_oversize_archive_refused_and_cleaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)
    monkeypatch.setattr(
        ContentActions,
        "_git_archive_workspace",
        AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr(settings, "token_factory_sandboxes_max_archive_bytes", 4)
    archive = Path("/tmp") / f"roboco-sandbox-{t.id.hex[:12]}.tar.gz"
    archive.write_bytes(b"way-more-than-four-bytes")

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "ceiling" in (env.message or "")
    assert not archive.exists()  # the oversize archive is cleaned up


# ---------------------------------------------------------------------------
# Success path — evidence, journal, heartbeat, next hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_returns_sandbox_evidence_and_journals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, task_svc = _make_actions(task_obj=t)
    archive = _fake_archive_ok(monkeypatch, t.id.hex)
    result = {
        "exit_code": 0,
        "timed_out": False,
        "stdout": "5 passed in 1.2s",
        "stderr": "",
        "cost": 0.4,
        "elapsed_time": 30.0,
        "instance_uuid": "inst-1",
        "operation_uuid": "op-1",
    }
    run = _stub_run(monkeypatch, result)

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error is None
    assert env.evidence is not None
    sandbox = env.evidence["sandbox"]
    assert sandbox["exit_code"] == 0
    assert sandbox["stdout_tail"] == "5 passed in 1.2s"
    assert sandbox["cost"] == _EXPECTED_COST
    assert sandbox["instance_uuid"] == "inst-1"
    assert "pr_pass" in (env.next or "")
    assert "fail_review" not in (env.next or "")
    run.assert_awaited_once()
    assert run.call_args.kwargs["command"] == "pytest -q"
    assert run.call_args.kwargs["archive_path"] == archive
    actions.journal.write_entry.assert_awaited_once()
    assert task_svc.heartbeat.await_count == _HEARTBEATS_PER_RUN
    assert not archive.exists()  # temp archive cleaned up


@pytest.mark.asyncio
async def test_failure_exit_code_points_at_fail_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)
    _fake_archive_ok(monkeypatch, t.id.hex)
    _stub_run(
        monkeypatch,
        {
            "exit_code": 1,
            "timed_out": False,
            "stdout": "1 failed",
            "stderr": "assert False",
            "cost": 0.4,
            "elapsed_time": 30.0,
            "instance_uuid": "inst-1",
            "operation_uuid": "op-1",
        },
    )

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error is None
    assert "fail_review" in (env.next or "")
    assert env.evidence is not None
    assert env.evidence["sandbox"]["exit_code"] == 1


@pytest.mark.asyncio
async def test_sandbox_api_failure_is_retryable_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, task_svc = _make_actions(task_obj=t)
    _fake_archive_ok(monkeypatch, t.id.hex)
    _stub_run(monkeypatch, None, "connection to Token Factory Sandboxes failed")

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "your own container" in (env.remediate or "")
    # The run attempt still heartbeats the caller (before + after).
    assert task_svc.heartbeat.await_count == _HEARTBEATS_PER_RUN


# ---------------------------------------------------------------------------
# Role scoping — the manifest carries the verb for dev + QA only
# ---------------------------------------------------------------------------


def test_manifest_grants_run_sandbox_tests_to_dev_and_qa_only() -> None:
    """role_config -> spawn_manifest wiring: the verb reaches dev and QA
    manifests (the two roles that run test suites) and no coordinator/
    board role's — the sandbox is a verification surface, never a
    coordinator credit surface."""

    def do_tools(role: str) -> list[str]:
        manifest = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role=role,
                team="qa",
                workspace_path="/tmp/ws",
                agent_model="nvidia/nemotron-3-super-120b-a12b",
            )
        )
        return list(manifest.do_tools)

    assert "run_sandbox_tests" in do_tools("qa")
    assert "run_sandbox_tests" in do_tools("developer")
    for role in (
        "documenter",
        "cell_pm",
        "main_pm",
        "pr_reviewer",
        "product_owner",
    ):
        assert "run_sandbox_tests" not in do_tools(role), role
