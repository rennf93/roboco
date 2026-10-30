"""Live contract of the git-user helper (``as-git-user``) in the orchestrator image.

Two opt-in groups:

- This process is the orchestrator image running as root with the drop
  required (scripts/orchestrator_image_suites.sh): the startup self-test
  passes, a process started through the helper is the git user and nothing
  more, the environment it gets is exactly the one RoboCo built (glibc's
  secure mode drops nothing of it), a missing helper refuses, exit statuses
  and signals pass through, and the deadline ends the whole tree (a
  grandchild that ignores TERM, a process that left with setsid) and nothing
  else (another git-user command running at the same time).
- ROBOCO_WORKSPACE_CONTAINER_LIVE=1 and ROBOCO_WORKSPACE_CONTAINER_IMAGE=<the
  orchestrator image>, with Docker reachable: fresh containers of the image
  play the callers this process is not, and each must fail loudly where the
  drop would otherwise be weaker: an added group, no-new-privileges, root
  without CAP_SETGID, another uid, a service whose helper is gone.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
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
    workspace_user_argv,
    workspace_user_executable,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

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
_NOT_FOUND = 127
_IDLE_CAPABILITIES = "0" * 16
# What a process reports about itself: ids, groups, no_new_privs, capabilities.
_IDENTITY_PROBE = "id -u; id -G; grep -E '^(NoNewPrivs|CapEff):' /proc/self/status"
_DEADLINE = 2
_GRACE = 2
# Scheduling slack on "killed by deadline + grace", well under the caller's
# own wait margin: the helper did the kill, not the caller giving up.
_SLACK = 1
# The session a shell is in, printed by the shell itself.
_PRINT_SESSION = "cut -d' ' -f6 /proc/$$/stat"
# A process that outlives TERM (the ignored disposition survives the exec).
_SLEEP_PAST_TERM = "trap '' TERM; exec sleep 300"
# A process that leaves the command's session and process group, then
# outlives TERM.
_ESCAPEE = f"setsid sh -c {shlex.quote(_SLEEP_PAST_TERM)}"
# A command's own exit status, told apart from the helper's.
_OWN_STATUS = 5


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


@in_the_image
@pytest.mark.parametrize(
    "arguments",
    [
        ["id", "-u"],
        ["--timeout", "0", "--grace", "1", "--", "id"],
        ["--timeout", "1s", "--grace", "1", "--", "id"],
        ["--timeout", "1234567890", "--grace", "1", "--", "id"],
        ["--timeout", "1", "--grace", "-1", "--", "id"],
        ["--timeout", "1", "--grace", "1", "id"],
        ["--timeout", "1", "--grace", "1", "--"],
    ],
    ids=[
        "old-form",
        "zero",
        "suffix",
        "ten-digits",
        "negative",
        "no-dashes",
        "no-program",
    ],
)
def test_a_malformed_invocation_is_refused(arguments: list[str]) -> None:
    result = subprocess.run(
        [GIT_USER_HELPER, *arguments],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    assert result.returncode == _REFUSED
    assert result.stderr.startswith("as-git-user: usage:")


@in_the_image
@pytest.mark.parametrize(
    ("script", "returncode"),
    [
        ("exit 0", 0),
        ("exit 3", 3),
        ("exit 124", 124),
        ("kill -TERM $$", -signal.SIGTERM),
        ("kill -HUP $$", -signal.SIGHUP),
        ("kill -KILL $$", -signal.SIGKILL),
    ],
)
def test_exit_statuses_and_signals_pass_through(script: str, returncode: int) -> None:
    """What the caller would see without the helper: a 124 before the
    deadline is the command's own, a signal death stays a signal death."""
    result = run_as_workspace_user("sh", "-c", script, timeout=_TIMEOUT)
    assert result.returncode == returncode
    assert result.stderr == ""


@in_the_image
def test_a_program_that_cannot_be_found_is_the_commands_result() -> None:
    result = run_as_workspace_user("/nonexistent/program", timeout=_TIMEOUT)
    assert result.returncode == _NOT_FOUND
    assert result.stderr.startswith("failed to run command '/nonexistent/program'")


@in_the_image
@pytest.mark.usefixtures("short_grace")
def test_a_grandchild_that_ignores_term_is_killed_after_the_grace() -> None:
    """GNU timeout returned once its child (the outer shell's sleep) died on
    TERM, leaving the grandchild running past the deadline."""
    ignores_term = f"sh -c {shlex.quote(_SLEEP_PAST_TERM)} & exec sleep 300"

    elapsed, _ = _ended_by_the_deadline("sh", "-c", ignores_term)

    assert _DEADLINE + _GRACE <= elapsed < _DEADLINE + _GRACE + _SLACK
    assert _git_user_processes() == []


@in_the_image
@pytest.mark.usefixtures("short_grace")
def test_a_process_that_left_with_setsid_is_killed_after_the_grace() -> None:
    """The escapee is in its own session, so outside the command's process
    group, and its parent ignores TERM and stays alive past the deadline:
    only the helper, as subreaper, reaches it once the parent is killed."""
    escapee = f"trap '' TERM; {_PRINT_SESSION}; exec sleep 300"
    command = (
        f"trap '' TERM; {_PRINT_SESSION}; setsid sh -c {shlex.quote(escapee)} & wait"
    )

    elapsed, output = _ended_by_the_deadline("sh", "-c", command)

    assert _DEADLINE + _GRACE <= elapsed < _DEADLINE + _GRACE + _SLACK
    command_session, escapee_session = output.split()
    assert escapee_session != command_session
    assert _git_user_processes() == []


@in_the_image
@pytest.mark.usefixtures("short_grace")
def test_another_git_user_command_survives_the_deadline_kill() -> None:
    """The kill reaches the command's own tree only: a git-user command
    started apart, under its own helper, is still running afterwards. Its
    helper forwards TERM to it and dies by the same signal."""
    with _another_git_user_command() as unrelated:
        _ended_by_the_deadline("sh", "-c", f"{_ESCAPEE} & exec sleep 300")
        assert unrelated.poll() is None
        unrelated.terminate()
        assert unrelated.wait(timeout=_TIMEOUT) == -signal.SIGTERM
    assert _git_user_processes() == []


@in_the_image
def test_the_helper_waits_for_what_the_command_left_running() -> None:
    """A background process the command left behind still runs inside the
    deadline: the helper returns once it is done, with the command's status."""
    started = time.monotonic()
    result = run_as_workspace_user(
        "sh", "-c", f"sleep 1 & exit {_OWN_STATUS}", timeout=_TIMEOUT
    )
    assert result.returncode == _OWN_STATUS
    assert 1 <= time.monotonic() - started < _DEADLINE + _GRACE
    assert _git_user_processes() == []


@in_the_image
@pytest.mark.usefixtures("short_grace")
def test_a_leftover_past_the_deadline_is_killed_and_the_status_kept() -> None:
    """The command itself finished in time: its status, not a timeout, once
    what it left running is killed at the deadline."""
    started = time.monotonic()
    result = run_as_workspace_user(
        "sh", "-c", f"{_ESCAPEE} & exit {_OWN_STATUS}", timeout=_DEADLINE
    )
    elapsed = time.monotonic() - started
    assert result.returncode == _OWN_STATUS
    assert _DEADLINE + _GRACE <= elapsed < _DEADLINE + _GRACE + _SLACK
    assert _git_user_processes() == []


@pytest.fixture
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper's grace as callers pass it, kept short for these tests."""
    monkeypatch.setattr(workspace_git, "_KILL_GRACE_SECONDS", _GRACE)


def _ended_by_the_deadline(program: str, *args: str) -> tuple[float, str]:
    """Seconds until ``TimeoutExpired`` and what the command printed."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_as_workspace_user(program, *args, timeout=_DEADLINE)
    output = raised.value.output
    return time.monotonic() - started, output if isinstance(output, str) else ""


@contextlib.contextmanager
def _another_git_user_command() -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        workspace_user_argv(["sleep", "300"], timeout=_TIMEOUT),
        executable=workspace_user_executable("sleep"),
        cwd="/",
        env=workspace_git_env(),
        text=True,
    )
    try:
        _wait_until_running_as_the_git_user(process.pid)
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=_TIMEOUT)


def _wait_until_running_as_the_git_user(helper: int) -> None:
    """Until the helper has dropped to the git user and started its command."""
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        children = [p for p in _git_user_processes_with_parents() if p[1] == helper]
        if children:
            return
        time.sleep(0.05)
    pytest.fail("the unrelated git-user command never started")


def _git_user_processes() -> list[str]:
    """Command lines of the live (not zombie) processes of the git user."""
    return [cmdline for _, _, cmdline in _git_user_processes_with_parents()]


def _git_user_processes_with_parents() -> list[tuple[int, int, str]]:
    found = []
    for status in Path("/proc").glob("[0-9]*/status"):
        with contextlib.suppress(OSError, KeyError, ValueError):
            fields = dict(
                line.split(":", 1) for line in status.read_text().splitlines()
            )
            if (
                int(fields["Uid"].split()[0]) == settings.git_user_uid
                and "Z" not in fields["State"]
            ):
                cmdline = (status.parent / "cmdline").read_text().replace("\0", " ")
                found.append(
                    (int(status.parent.name), int(fields["PPid"]), cmdline.strip())
                )
    return found


# --------------------------------------------------------------------------- #
# Over Docker: callers this process is not
# --------------------------------------------------------------------------- #


def _helper_running(*command: str) -> list[str]:
    """The helper's arguments to run ``command`` (the helper is the
    entrypoint)."""
    return ["--timeout", str(_TIMEOUT), "--grace", str(_GRACE), "--", *command]


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
    result = _run_image(
        "--user", user, command=_helper_running("sh", "-c", _IDENTITY_PROBE)
    )
    assert result.returncode == 0, result.stderr
    _assert_the_git_user(result.stdout)


@over_docker
def test_an_added_group_is_refused() -> None:
    """A non-root caller cannot drop a group: git must fail, not inherit it."""
    result = _run_image(
        "--user", "1000:1000", "--group-add", "999", command=_helper_running("id", "-u")
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
        command=_helper_running("id", "-u"),
    )
    assert result.returncode == _REFUSED
    assert "set-id bits did not take effect" in result.stderr


@over_docker
def test_root_that_cannot_clear_its_groups_is_refused() -> None:
    result = _run_image(
        "--user", "0:0", "--cap-drop", "SETGID", command=_helper_running("id")
    )
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
        command=["-c", shlex.join([GIT_USER_HELPER, *_helper_running("id", "-u")])],
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
