"""Live contract of the git-user helper (``as-git-user``) in the orchestrator image.

Two opt-in groups:

- This process is the orchestrator image running as root with the drop
  required (scripts/orchestrator_image_suites.sh): the startup self-test
  passes, a process started through the helper is the git user and nothing
  more, the environment it gets is exactly the one RoboCo built (glibc's
  secure mode drops nothing of it), and a missing helper refuses.
- ROBOCO_WORKSPACE_CONTAINER_LIVE=1 and ROBOCO_WORKSPACE_CONTAINER_IMAGE=<the
  orchestrator image>, with Docker reachable: fresh containers of the image
  play the callers this process is not, and each must fail loudly where the
  drop would otherwise be weaker: an added group, no-new-privileges, root
  without CAP_SETGID, another uid, a service whose helper is gone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services import workspace_git
from roboco.services.workspace_git import (
    GIT_USER_HELPER,
    GitUserDropError,
    run_as_workspace_user,
    workspace_git_env,
    workspace_repository_env,
)

if TYPE_CHECKING:
    from pathlib import Path

in_the_image = pytest.mark.skipif(
    os.geteuid() != 0
    or settings.workspace_git_drop != "required"
    or not os.access(GIT_USER_HELPER, os.X_OK),
    reason="needs root in the orchestrator image (drop required, helper installed)",
)

_IMAGE = os.environ.get("ROBOCO_WORKSPACE_CONTAINER_IMAGE", "")
over_docker = pytest.mark.skipif(
    os.environ.get("ROBOCO_WORKSPACE_CONTAINER_LIVE") != "1"
    or not _IMAGE
    or shutil.which("docker") is None,
    reason="live Docker contract of the orchestrator image (opt-in)",
)

_TIMEOUT = 60
_REFUSED = 126
_IDLE_CAPABILITIES = "0" * 16
# What a process reports about itself: ids, groups, no_new_privs, capabilities.
_IDENTITY_PROBE = "id -u; id -G; grep -E '^(NoNewPrivs|CapEff):' /proc/self/status"


def _identity(output: str) -> tuple[int, list[int], dict[str, str]]:
    uid, groups, *status = output.strip().splitlines()
    fields = {}
    for line in status:
        name, _, value = line.partition(":")
        fields[name] = value.strip()
    return int(uid), [int(g) for g in groups.split()], fields


def _assert_the_git_user(output: str) -> None:
    uid, groups, status = _identity(output)
    assert uid == settings.git_user_uid
    assert groups == [settings.agent_gid]
    assert status == {"NoNewPrivs": "1", "CapEff": _IDLE_CAPABILITIES}


def _env_through_the_helper(env: dict[str, str]) -> dict[str, str]:
    result = run_as_workspace_user("env", "-0", env=env, timeout=_TIMEOUT)
    assert result.returncode == 0, result.stderr
    received = {}
    for entry in filter(None, result.stdout.split("\0")):
        name, _, value = entry.partition("=")
        received[name] = value
    return received


# --------------------------------------------------------------------------- #
# In this process: root in the orchestrator image
# --------------------------------------------------------------------------- #


@in_the_image
def test_the_startup_self_test_passes() -> None:
    workspace_git.verify_git_user_drop()


@in_the_image
def test_a_process_started_through_the_helper_is_the_git_user() -> None:
    result = run_as_workspace_user("sh", "-c", _IDENTITY_PROBE, timeout=_TIMEOUT)
    assert result.returncode == 0, result.stderr
    _assert_the_git_user(result.stdout)


@in_the_image
def test_the_environment_reaches_the_program_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set-id program runs in glibc's secure mode, which drops LD_*,
    GCONV_PATH, TMPDIR and the like: none of what RoboCo passes may be."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path))
    clone = tmp_path / "proj" / "backend" / "be-dev-1"
    (clone / ".git").mkdir(parents=True)
    env = workspace_git_env() | workspace_repository_env(clone / ".worktrees" / "t1")
    assert "GIT_DIR" in env

    assert _env_through_the_helper(env) == env


@in_the_image
def test_secure_mode_does_drop_what_roboco_never_passes() -> None:
    """The reason the test above matters: these would vanish on the way."""
    dropped = {"LD_LIBRARY_PATH": "/x", "GCONV_PATH": "/x", "TMPDIR": "/tmp"}

    received = _env_through_the_helper(workspace_git_env() | dropped)

    assert not set(dropped) & set(received)
    assert not set(dropped) & set(workspace_git_env())


@in_the_image
def test_a_missing_helper_refuses_rather_than_run_as_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace_git, "GIT_USER_HELPER", str(tmp_path / "gone"))
    with pytest.raises(GitUserDropError):
        run_as_workspace_user("id", "-u", timeout=_TIMEOUT)
    with pytest.raises(GitUserDropError):
        workspace_git.verify_git_user_drop()


# --------------------------------------------------------------------------- #
# Over Docker: callers this process is not
# --------------------------------------------------------------------------- #


def _run_image(
    *options: str, command: list[str], entrypoint: str = GIT_USER_HELPER
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            f"roboco-helper-live-{uuid4().hex[:12]}",
            "--no-healthcheck",
            "--network",
            "none",
            *options,
            "--entrypoint",
            entrypoint,
            _IMAGE,
            *command,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT * 2,
        check=False,
    )


@over_docker
def test_the_helper_is_set_id_to_the_git_user_and_closed_to_others() -> None:
    result = _run_image(
        command=["-c", "%u:%g %a", GIT_USER_HELPER], entrypoint="/usr/bin/stat"
    )
    assert result.stdout.split() == [
        f"{settings.git_user_uid}:{settings.agent_gid}",
        "6750",
    ]


@over_docker
@pytest.mark.parametrize("user", ["0:0", "1000:1000"])
def test_a_caller_becomes_the_git_user_with_nothing_more(user: str) -> None:
    """Root (the image today) and the agent uid (the image once it drops
    root) both end up as the git user."""
    result = _run_image("--user", user, command=["sh", "-c", _IDENTITY_PROBE])
    assert result.returncode == 0, result.stderr
    _assert_the_git_user(result.stdout)


@over_docker
def test_an_added_group_is_refused() -> None:
    """A non-root caller cannot drop a group: git must fail, not inherit it."""
    result = _run_image(
        "--user", "1000:1000", "--group-add", "999", command=["id", "-u"]
    )
    assert result.returncode == _REFUSED
    assert "supplementary group" in result.stderr


@over_docker
@pytest.mark.parametrize("user", ["0:0", "1000:1000"])
def test_no_new_privileges_is_refused(user: str) -> None:
    """The kernel ignores set-id bits under no_new_privs: the helper must not
    go on as its caller."""
    result = _run_image(
        "--user",
        user,
        "--security-opt",
        "no-new-privileges",
        command=["id", "-u"],
    )
    assert result.returncode == _REFUSED
    assert "set-id bits did not take effect" in result.stderr


@over_docker
def test_root_that_cannot_clear_its_groups_is_refused() -> None:
    result = _run_image("--user", "0:0", "--cap-drop", "SETGID", command=["id"])
    assert result.returncode == _REFUSED
    assert "clearing the supplementary groups" in result.stderr


@over_docker
def test_another_uid_cannot_run_the_helper() -> None:
    """Mode 6750: neither the git user's group nor root. Started from a
    shell: runc execs a container's entrypoint before it drops the
    container's capabilities, so DAC_OVERRIDE would let that one exec past
    the mode."""
    result = _run_image(
        "--user",
        "2000:2000",
        command=["-c", f"{GIT_USER_HELPER} id -u"],
        entrypoint="/bin/sh",
    )
    assert result.returncode == _REFUSED
    assert "permission denied" in result.stderr.lower()


@over_docker
@pytest.mark.parametrize("role", ["api", "dispatcher"])
def test_the_service_refuses_to_start_without_the_helper(role: str) -> None:
    result = _run_image(
        "--user",
        "0:0",
        "-e",
        f"ROBOCO_ROLE={role}",
        command=["-c", f"rm -f {GIT_USER_HELPER} && exec python -m roboco.cli"],
        entrypoint="/bin/sh",
    )
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "refusing to start" in output
    assert "missing or not executable" in output
