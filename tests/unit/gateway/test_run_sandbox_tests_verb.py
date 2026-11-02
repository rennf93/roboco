"""ContentActions.run_sandbox_tests — the Token Factory Sandboxes verb.

Guard matrix (flag off / no active task / no Nebius key / team unresolved /
workspace missing / git-archive failure / archive oversize / sandbox API
failure), the success envelope evidence shape, journaling, and the role
scoping (QA's do_tools only).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

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
    t.branch_name = None  # HEAD fallback unless a test sets a branch
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


def _stub_archive_write(
    monkeypatch: pytest.MonkeyPatch, payload: bytes = b"tarball"
) -> None:
    """Make the archive step succeed, writing ``payload`` to whatever random
    mkstemp path the verb chose (the real helper runs ``git archive -o``)."""

    async def fake_git(
        _root: Path, archive: Path, _ref: str = "HEAD"
    ) -> tuple[bool, str]:
        archive.write_bytes(payload)
        return True, ""

    monkeypatch.setattr(
        ContentActions, "_git_archive_workspace", AsyncMock(side_effect=fake_git)
    )


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
    _OVERSIZE_CEILING = 4
    monkeypatch.setattr(
        settings,
        "token_factory_sandboxes_max_archive_bytes",
        _OVERSIZE_CEILING,
    )
    _stub_archive_write(monkeypatch, payload=b"way-more-than-four-bytes")

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "ceiling" in (env.message or "")
    # Every file the verb's mkstemp created is cleaned up on the rejection
    # path - nothing predictable or lingering is left in the shared tmp.
    leftovers = [
        p
        for p in Path(tempfile.gettempdir()).glob("roboco-sandbox-*.tar.gz")
        if p.stat().st_size > _OVERSIZE_CEILING
    ]
    assert leftovers == []


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
    _stub_archive_write(monkeypatch)
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
    called_archive = run.call_args.kwargs["archive_path"]
    assert called_archive.exists() is False  # temp archive cleaned up
    actions.journal.write_entry.assert_awaited_once()
    assert task_svc.heartbeat.await_count == _HEARTBEATS_PER_RUN


@pytest.mark.asyncio
async def test_failure_exit_code_points_at_fail_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)
    _stub_archive_write(monkeypatch)
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
    _stub_archive_write(monkeypatch)
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
                workspace_path=Path("/tmp/ws"),
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


# ---------------------------------------------------------------------------
# Archive ref: QA archives the fetched task branch, a dev worktree HEAD
# ---------------------------------------------------------------------------


_BRANCH = "feature/backend/ABC12345--DEF67890"


def _wire_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    head: str,
    fetch_ok: bool,
    fetch_raises: bool = False,
) -> tuple[ContentActions, AsyncMock, AsyncMock, UUID]:
    _arm(monkeypatch)
    t = _task()
    t.branch_name = _BRANCH
    actions, _svc = _make_actions(task_obj=t)
    actions._deps.workspace.get_worktree_path = MagicMock(return_value=tmp_path)
    actions._deps.workspace.get_clone_root_path = MagicMock(return_value=tmp_path)
    fetch = AsyncMock(return_value=tmp_path)
    if fetch_raises:
        fetch.side_effect = RuntimeError("no token")
    actions._deps.workspace.fetch_branch_for_inspection = fetch
    # FETCH_HEAD as git writes it: names the branch only on a real fetch.
    (tmp_path / "FETCH_HEAD").write_text(
        f"abc123\t\tbranch '{_BRANCH}' of https://example/r\n"
        if fetch_ok
        else "abc123\t\tbranch 'other' of https://example/r\n"
    )

    async def fake_capture(_root: Path, *args: str) -> tuple[bool, str, str]:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return True, head, ""
        if args[0] == "rev-parse" and "--git-path" in args:
            return True, str(tmp_path / "FETCH_HEAD"), ""
        return True, "", ""

    monkeypatch.setattr(ContentActions, "_git_capture", fake_capture)
    archive = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(ContentActions, "_git_archive_workspace", archive)
    return actions, fetch, archive, uuid4()


@pytest.mark.asyncio
async def test_qa_root_archives_fetched_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    actions, fetch, archive, agent_id = _wire_branch(
        monkeypatch, tmp_path, head="master", fetch_ok=True
    )
    _stub_run(monkeypatch, {"exit_code": 0, "stdout": "", "stderr": ""})

    env = await actions.run_sandbox_tests(agent_id=agent_id, command="pytest -q")

    assert env.error is None
    fetch.assert_awaited_once_with(agent_id=agent_id, branch_name=_BRANCH)
    assert archive.await_args is not None
    assert archive.await_args.args[2] == "FETCH_HEAD"


@pytest.mark.asyncio
async def test_dev_worktree_archives_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    actions, fetch, archive, _ = _wire_branch(
        monkeypatch, tmp_path, head=_BRANCH, fetch_ok=True
    )
    _stub_run(monkeypatch, {"exit_code": 0, "stdout": "", "stderr": ""})

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error is None
    fetch.assert_not_awaited()
    assert archive.await_args is not None
    assert archive.await_args.args[2] == "HEAD"


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_branch_fetch_failure_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, raises: bool
) -> None:
    # raises=False: helper returns but FETCH_HEAD is stale (swallowed failure).
    actions, _fetch, archive, _ = _wire_branch(
        monkeypatch, tmp_path, head="master", fetch_ok=False, fetch_raises=raises
    )

    env = await actions.run_sandbox_tests(agent_id=uuid4(), command="pytest -q")

    assert env.error == "invalid_state"
    assert "could not fetch task branch" in (env.message or "")
    assert "push" in (env.remediate or "")
    archive.assert_not_awaited()
