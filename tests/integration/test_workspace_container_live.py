"""Live contract of the throwaway workspace container (real Docker).

Env-gated: set ROBOCO_WORKSPACE_CONTAINER_LIVE=1 and
ROBOCO_WORKSPACE_CONTAINER_IMAGE=<the orchestrator image> on a host whose
Docker daemon this process can reach. The unit tests pin the `docker run`
argv; this proves what that argv actually does to the code inside.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.services.workspace_container import (
    WorkspaceContainerError,
    run_in_workspace_container,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    os.environ.get("ROBOCO_WORKSPACE_CONTAINER_LIVE") != "1"
    or not os.environ.get("ROBOCO_WORKSPACE_CONTAINER_IMAGE")
    or shutil.which("docker") is None,
    reason="live Docker workspace-container contract (opt-in)",
)

_AGENT_UID = 1000
_GROUP_WRITABLE = 0o020
_TIMEOUT_EXIT_CODE = 124


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone dir the agent uid owns, under a workspaces root that maps 1:1
    to the host (the test runs on the host, so host path == path)."""
    monkeypatch.setattr(
        settings,
        "workspace_container_image",
        os.environ["ROBOCO_WORKSPACE_CONTAINER_IMAGE"],
    )
    monkeypatch.setattr(settings, "host_data_dir", "")
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    path = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    path.mkdir(parents=True)
    (tmp_path / "outside-the-clone.txt").write_text("not for the container")
    if os.geteuid() == 0:
        for entry in (tmp_path, tmp_path / "workspaces", path):
            os.chown(entry, _AGENT_UID, _AGENT_UID)
    return path


async def _sh(clone: Path, script: str, *, timeout: float = 120) -> str:
    result = await run_in_workspace_container(
        clone, ["sh", "-c", script], timeout=timeout, merge_stderr=True
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


async def test_runs_as_the_agent_uid_with_group_writable_output(clone: Path) -> None:
    out = await _sh(clone, "id -u; umask; mkdir -p d && touch d/f && echo ok")
    uid, umask, ok = out.split()
    assert int(uid) == _AGENT_UID
    assert umask.endswith("002")
    assert ok == "ok"
    assert (clone / "d" / "f").stat().st_mode & _GROUP_WRITABLE


async def test_has_no_capabilities_no_docker_socket_and_no_new_privileges(
    clone: Path,
) -> None:
    out = await _sh(
        clone,
        "grep -E '^(CapEff|CapBnd|NoNewPrivs):' /proc/self/status; "
        "test -e /var/run/docker.sock && echo SOCKET || echo NOSOCKET",
    )
    fields = dict(line.split(":\t") for line in out.strip().splitlines()[:3])
    assert int(fields["CapEff"], 16) == 0
    assert int(fields["CapBnd"], 16) == 0
    assert fields["NoNewPrivs"].strip() == "1"
    assert out.strip().endswith("NOSOCKET")


async def test_only_the_clone_is_visible(clone: Path) -> None:
    out = await _sh(
        clone,
        f"test -e {clone.parent.parent.parent.parent}/outside-the-clone.txt "
        "&& echo LEAK || echo CONTAINED",
    )
    assert out.strip() == "CONTAINED"


async def test_a_runaway_command_is_killed_at_the_deadline(clone: Path) -> None:
    result = await run_in_workspace_container(clone, ["sleep", "60"], timeout=3)
    assert result.timed_out or result.returncode == _TIMEOUT_EXIT_CODE


async def test_an_unstartable_image_raises_instead_of_running_here(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "workspace_container_image", "roboco-no-such-image:never"
    )
    with pytest.raises(WorkspaceContainerError):
        await run_in_workspace_container(clone, ["true"], timeout=60)
