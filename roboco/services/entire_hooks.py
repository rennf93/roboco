"""Entire's commit hooks for the commits RoboCo makes for an agent.

Entire (entire.io) links an agent's Claude Code session to the commits made
while it ran. The agent's own container runs its session hooks (the agent
image's ``entire`` wrapper, behind ``entire_enabled``), which keep the session
in the clone (``.git/entire-sessions/``, a transcript snapshot under
``.entire/metadata/``). Its commit hooks belong where the commit runs, and
RoboCo commits for the agent: ``prepare-commit-msg`` adds an
``Entire-Checkpoint: <id>`` trailer, ``commit-msg`` drops it again from a
message with nothing else in it, ``post-commit`` writes the redacted
transcript as the checkpoint ref ``refs/entire/checkpoints/<id[-2:]>/<id>``.

Those hooks read what the agent wrote: the session record names the
transcript file to read, and whatever that file holds lands in a checkpoint
that gets pushed. So they never run here. They run in the throwaway workspace
container as the agent uid, with no network, and with nothing mounted but the
clone and that agent's own transcript folder (read-only, where the agent's
container has it): a record pointed anywhere else reaches only what the agent
can already read. Like Entire's own installed hooks they are best-effort: a
failure is logged, it never refuses the commit.

Entire also leaves the git objects it writes readable by their owner only, so
the same container then gives the agent group read on them: the git user,
whose ``upload-pack`` hands a checkpoint to the relay, is in that group.

The checkpoints of the commits a push sends go to the project's origin through
the relay (``GitService``). Entire's own ``pre-push`` would need the network
and a token, and the ``.git/hooks`` wrappers an agent's Entire session
installs only start Entire's CLI (absent from the container's PATH) and chain
to the hook they moved aside, so that hook is what ``project_hooks`` runs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.workspace_container import (
    ReadOnlyMount,
    WorkspaceContainerError,
    run_offline_in_workspace_container,
)
from roboco.services.workspace_git import run_as_workspace_user, workspace_repository

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger(__name__)

ENTIRE_CLI = "/usr/local/lib/entire/entire"
CHECKPOINT_TRAILER = "Entire-Checkpoint"

# Where every agent container mounts the host's ~/.claude.
_AGENT_CLAUDE_DIR = "/home/agent/.claude"
# A checkpoint id is a ULID: 26 characters of Crockford base32.
_CHECKPOINT_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
_ENTIRE_HOOK_MARKER = "# Entire CLI hooks"
_HOOK_HEAD_LINES = 3
_HOOK_HEAD_BYTES = "512"
_FILE_CHECK_TIMEOUT_SECONDS = 30

_MESSAGE_HOOKS = '"$0" hooks git prepare-commit-msg "$1" message; '
_COMMIT_MSG_HOOK = '"$0" hooks git commit-msg "$1"'
# post-commit, then group read (and write on directories) for the agent group
# on what this uid left under objects/ and refs/entire/.
_POST_COMMIT = (
    '"$0" hooks git post-commit; status=$?; '
    'find "$1/objects" "$1/refs/entire" -user "$(id -u)" '
    r"\( -type d ! -perm -g=rwx -exec chmod g+rwx {} + \) -o "
    r"\( -type f ! -perm -g=r -exec chmod g+r {} + \) 2>/dev/null; "
    'exit "$status"'
)


def records_entire_sessions(cwd: Path) -> bool:
    """True when ``entire_enabled`` is on and an agent's Entire session hooks
    have recorded a session in ``cwd``'s repository."""
    if not settings.entire_enabled:
        return False
    sessions = workspace_repository(cwd).common_dir / "entire-sessions"
    try:
        return any(entry.suffix == ".json" for entry in sessions.iterdir())
    except OSError:
        return False


async def run_entire_message_hooks(
    cwd: Path, message_file: Path, *, verify: bool, timeout: float
) -> None:
    """Entire's ``prepare-commit-msg``, then (unless the commit skips its
    checks, as ``--no-verify`` does for git) ``commit-msg``, on the message
    RoboCo is about to commit with."""
    script = _MESSAGE_HOOKS + (_COMMIT_MSG_HOOK if verify else "true")
    await _run_entire(cwd, "message", script, str(message_file), timeout)


async def run_entire_post_commit(cwd: Path, *, timeout: float) -> None:
    """Entire's ``post-commit`` after a RoboCo commit: the checkpoint of the
    commit's ``Entire-Checkpoint`` trailer, when it has one."""
    common_dir = str(workspace_repository(cwd).common_dir)
    await _run_entire(cwd, "post-commit", _POST_COMMIT, common_dir, timeout)


async def _run_entire(
    cwd: Path, step: str, script: str, path_arg: str, timeout: float
) -> None:
    """``sh -c <script> <Entire CLI> <path_arg>`` in the sealed container."""
    try:
        result = await run_offline_in_workspace_container(
            cwd,
            ["sh", "-c", script, ENTIRE_CLI, path_arg],
            timeout=timeout,
            env=_entire_env(),
            read_only_mount=_transcript_mount(cwd),
        )
    except WorkspaceContainerError as exc:
        logger.warning("entire hooks not run", step=step, error=str(exc))
        return
    if result.timed_out or result.returncode != 0:
        logger.warning(
            "entire hooks failed",
            step=step,
            returncode=result.returncode,
            output=result.stdout.strip()[-500:],
        )


def _entire_env() -> dict[str, str]:
    """git in there trusts the clone through the support dir's global config
    (as for project hooks); Entire sends no telemetry."""
    return {
        "GIT_CONFIG_GLOBAL": str(Path(settings.git_support_dir, "workspace.gitconfig")),
        "ENTIRE_TELEMETRY_OPTOUT": "1",
    }


def _claude_dir() -> Path:
    """This orchestrator's view of the ~/.claude the agents mount."""
    return Path.home() / ".claude"


def _transcript_mount(cwd: Path) -> ReadOnlyMount | None:
    """The agent's Claude Code transcript folder for ``cwd``, mounted where
    the agent's session record names it. Claude Code names the folder after
    the session's working directory, every character but [A-Za-z0-9] made
    ``-``; RoboCo commits in the directory the agent works in."""
    folder = re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(cwd))
    root = _claude_dir()
    local = root / "projects" / folder
    if not local.is_dir():
        return None
    host_root = Path(settings.host_claude_dir) if settings.host_claude_dir else root
    return ReadOnlyMount(
        root=root,
        local=local,
        host=str(host_root / "projects" / folder),
        target=f"{_AGENT_CLAUDE_DIR}/projects/{folder}",
    )


def checkpoint_refs(trailer_values: Iterable[str]) -> list[str]:
    """Entire's refs for the ``Entire-Checkpoint`` trailer values of some
    commits; a value that is not a checkpoint id is dropped."""
    refs: list[str] = []
    for value in trailer_values:
        if _CHECKPOINT_ID.fullmatch(value):
            ref = f"refs/entire/checkpoints/{value[-2:]}/{value}"
            if ref not in refs:
                refs.append(ref)
    return refs


def is_entire_hook(hook: Path) -> bool:
    """True for a hook Entire installed (its marker opens the file): it only
    starts Entire's CLI and chains to the hook it replaced. Read as the git
    user, and only its first bytes: the file is the agent's."""
    head = run_as_workspace_user(
        ["head", "-c", _HOOK_HEAD_BYTES, "--", str(hook)],
        timeout=_FILE_CHECK_TIMEOUT_SECONDS,
    )
    lines = head.stdout.splitlines()[:_HOOK_HEAD_LINES]
    return head.returncode == 0 and _ENTIRE_HOOK_MARKER in lines


def hook_entire_replaced(hook: Path) -> Path | None:
    """``<hook>.pre-entire``, the hook Entire moved aside and chains to,
    when git would run it (an executable file)."""
    chained = hook.with_name(f"{hook.name}.pre-entire")
    runnable = run_as_workspace_user(
        ["test", "-f", str(chained), "-a", "-x", str(chained)],
        timeout=_FILE_CHECK_TIMEOUT_SECONDS,
    )
    return chained if runnable.returncode == 0 else None
