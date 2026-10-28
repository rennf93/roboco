"""A workspace's own code runs in a throwaway container, never as root here."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings
from roboco.services import workspace_container as wc

_TIMEOUT_EXIT_CODE = 124


def _proc(
    returncode: int | None = 0, stdout: bytes = b"", stderr: bytes = b""
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.fixture(autouse=True)
def _host_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "host_data_dir", "/volume1/roboco/data")
    monkeypatch.setattr(settings, "workspaces_root", "/data/workspaces")
    monkeypatch.setattr(settings, "private_data_root", "/data/private")
    monkeypatch.setattr(settings, "workspace_container_image", "roboco-orch@sha")
    monkeypatch.setattr(settings, "workspace_container_network", "bridge")
    monkeypatch.setattr(settings, "agent_uid", 1000)
    monkeypatch.setattr(settings, "agent_gid", 1000)


async def test_the_command_runs_unprivileged_with_only_its_clone_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_mock = AsyncMock(return_value=_proc(0, b"done\n"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_mock)
    worktree = Path("/data/workspaces/roboco-api/backend/be-dev-1/.worktrees/abc")

    result = await wc.run_in_workspace_container(
        worktree, ["make", "gate"], timeout=60, env={"UV_PYTHON_INSTALL_DIR": "/x"}
    )

    assert result == wc.WorkspaceContainerResult(0, "done\n", "")
    argv = list(exec_mock.call_args.args)
    image_at = argv.index("roboco-orch@sha")
    options, command = argv[:image_at], argv[image_at + 1 :]
    assert options[:3] == ["docker", "run", "--rm"]
    assert options[options.index("--user") + 1] == "1000:1000"
    assert options[options.index("--cap-drop") + 1] == "ALL"
    assert options[options.index("--security-opt") + 1] == "no-new-privileges"
    assert options[options.index("--network") + 1] == "bridge"
    # The worktree's clone root (shared .venv / .uv-python) is the one mount,
    # at the same path it has here and in the agent containers.
    mounts = [options[i + 1] for i, o in enumerate(options) if o == "-v"]
    assert mounts == [
        "/volume1/roboco/data/workspaces/roboco-api/backend/be-dev-1:"
        "/data/workspaces/roboco-api/backend/be-dev-1"
    ]
    assert options[options.index("-w") + 1] == str(worktree)
    assert "/var/run/docker.sock" not in " ".join(argv)
    # Env values ride the docker CLI's env, never its argv.
    assert not [o for i, o in enumerate(options) if options[i - 1] == "-e" and "=" in o]
    assert exec_mock.call_args.kwargs["env"]["UV_PYTHON_INSTALL_DIR"] == "/x"
    assert command[:3] == ["-c", wc._entry_script(60, merge_stderr=False), "sh"]
    # The run ends on its own even if no orchestrator is left to kill it.
    assert "exec timeout -k 10 60 env" in command[1]
    assert command[3:] == ["make", "gate"]


async def test_merge_stderr_folds_stderr_into_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_mock = AsyncMock(return_value=_proc())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_mock)

    await wc.run_in_workspace_container(
        Path("/data/workspaces/p/t/a"), ["true"], timeout=5, merge_stderr=True
    )

    argv = list(exec_mock.call_args.args)
    assert argv[argv.index("-c") + 1].endswith(" 2>&1")


def test_host_paths_translate_only_the_mounted_data_roots() -> None:
    assert (
        wc.host_path_for(Path("/data/private/scratch/x/repo"))
        == "/volume1/roboco/data/private/scratch/x/repo"
    )
    with pytest.raises(wc.WorkspaceContainerError):
        wc.host_path_for(Path("/root/.claude"))
    with pytest.raises(wc.WorkspaceContainerError):
        # Lexically outside the root once normalized.
        wc.host_path_for(Path("/data/workspaces/../private-not"))


def test_host_paths_pass_through_when_running_on_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "host_data_dir", "")
    assert wc.host_path_for(Path("/srv/ws/p/t/a")) == "/srv/ws/p/t/a"


async def test_a_timeout_kills_the_container_and_reaps_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _proc(-9)

    async def _never() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return b"", b""

    cli.communicate = _never
    killer = _proc(0)
    calls: list[tuple[Any, ...]] = []

    async def _exec(*args: Any, **_kwargs: Any) -> MagicMock:
        calls.append(args)
        return killer if args[:2] == ("docker", "kill") else cli

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

    result = await wc.run_in_workspace_container(
        Path("/data/workspaces/p/t/a"), ["sleep", "60"], timeout=0.01
    )

    assert result.timed_out
    assert result.returncode == _TIMEOUT_EXIT_CODE
    name = calls[0][calls[0].index("--name") + 1]
    assert ("docker", "kill", name) in calls
    cli.kill.assert_called_once()
    cli.wait.assert_awaited_once()


async def test_an_outer_cancellation_kills_reaps_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cli = _proc(None)

    async def _forever() -> tuple[bytes, bytes]:
        started.set()
        await asyncio.sleep(30)
        return b"", b""

    cli.communicate = _forever
    killer = _proc(0)

    async def _exec(*args: Any, **_kwargs: Any) -> MagicMock:
        return killer if args[:2] == ("docker", "kill") else cli

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)

    task = asyncio.ensure_future(
        wc.run_in_workspace_container(Path("/data/workspaces/p/t/a"), ["x"], timeout=60)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cli.kill.assert_called_once()
    cli.wait.assert_awaited_once()


async def test_a_missing_exit_status_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown exit status must never pass a gate."""
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=_proc(None))
    )
    result = await wc.run_in_workspace_container(
        Path("/data/workspaces/p/t/a"), ["true"], timeout=5
    )
    assert result.returncode != 0


async def test_a_container_that_cannot_start_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_proc(125, b"", b"Unable to find image")),
    )
    with pytest.raises(wc.WorkspaceContainerError, match="Unable to find image"):
        await wc.run_in_workspace_container(
            Path("/data/workspaces/p/t/a"), ["true"], timeout=5
        )


async def test_the_own_image_is_read_once_from_this_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "workspace_container_image", None)
    monkeypatch.setattr(wc, "_own_image", {})
    monkeypatch.setattr(wc.socket, "gethostname", lambda: "0123abcd")
    exec_mock = AsyncMock(return_value=_proc(0, b"sha256:feed\n"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_mock)

    assert await wc.workspace_container_image() == "sha256:feed"
    assert await wc.workspace_container_image() == "sha256:feed"

    exec_mock.assert_awaited_once()
    assert exec_mock.call_args.args == (
        "docker",
        "inspect",
        "--format",
        "{{.Image}}",
        "0123abcd",
    )


async def test_an_unresolvable_own_image_names_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "workspace_container_image", None)
    monkeypatch.setattr(wc, "_own_image", {})
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=_proc(1, b"", b"No such object")),
    )
    with pytest.raises(wc.WorkspaceContainerError, match="WORKSPACE_CONTAINER_IMAGE"):
        await wc.workspace_container_image()
