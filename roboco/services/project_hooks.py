"""A project's own git hooks, run where its other code runs: a throwaway container.

Projects ship hooks that plain git runs on commit and push (husky's
``pre-commit`` running ``npm run check``, a ``.githooks/pre-push`` running the
test suite). RoboCo's workspace git pins its hooks to the root-owned support
dir, so a hook an agent can rewrite never runs in the orchestrator. The
gating ones still run, in the same throwaway container as the quality gate
(the agent uid, no capabilities, only that clone mounted):

- ``pre-commit``, then ``commit-msg`` (on the message file, which it may
  rewrite), before a RoboCo commit;
- ``pre-push``, with git's arguments (``origin <url>``) and its input
  (``<local ref> <local sha> <remote ref> <remote sha>``), before a push
  (``GitService`` builds those: it holds the relay).

A hook is found where git would look (``workspace_hook_path``: the repo's
``core.hooksPath``, else ``.git/hooks``), only under the workspaces root; an
operator-configured path elsewhere cannot be mounted into the container, so
its hooks are skipped (logged). A wrapper Entire installed there stands for
the hook it moved aside (``<hook>.pre-entire``), if any: Entire's own part
RoboCo runs itself (``roboco.services.entire_hooks``), between ``pre-commit``
and ``commit-msg`` as git would. A non-zero exit refuses the commit or push
with the hook's output, as git does; so does a hook that could not be run at
all (the container did not start): a check that did not run is not a pass.
``--no-verify`` skips them, as with git. The commit hooks get the commit's
own time budget; ``pre-push`` gets ``project_hook_push_timeout_seconds``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.entire_hooks import (
    hook_entire_replaced,
    is_entire_hook,
    records_entire_sessions,
    run_entire_message_hooks,
)
from roboco.services.workspace_container import (
    WorkspaceContainerError,
    run_in_workspace_container,
)
from roboco.services.workspace_git import (
    run_as_workspace_user,
    workspace_hook_path,
    workspace_repository,
    write_workspace_file,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

_NO_VERIFY = frozenset({"--no-verify", "-n"})
_HOOK_REFUSED = 1
_REMOVE_TIMEOUT_SECONDS = 30


def _under_workspaces_root(path: Path) -> bool:
    return Path(os.path.normpath(path)).is_relative_to(settings.workspaces_root)


def _hook_env(stdin_file: Path | None) -> dict[str, str]:
    """The hook's environment beyond the image's own: git in there trusts
    the clone (its owner differs from the agent uid) through the support
    dir's global config, the same file the git user reads."""
    env = {
        "GIT_CONFIG_GLOBAL": str(Path(settings.git_support_dir, "workspace.gitconfig"))
    }
    if stdin_file is not None:
        env["ROBOCO_HOOK_STDIN"] = str(stdin_file)
    return env


def _refused(name: str, message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git", name], _HOOK_REFUSED, "", message)


async def find_project_hook(cwd: Path, name: str) -> Path | None:
    """The ``name`` hook plain git would run in ``cwd``; None when there is
    none or ``cwd`` is outside the workspaces root (logged)."""
    hook = await asyncio.to_thread(workspace_hook_path, cwd, name)
    if hook is not None and not _under_workspaces_root(cwd):
        logger.warning(
            "project hook skipped: outside the workspaces root",
            hook=str(hook),
            workspace=str(cwd),
        )
        return None
    if hook is not None and await asyncio.to_thread(is_entire_hook, hook):
        return await asyncio.to_thread(hook_entire_replaced, hook)
    return hook


async def run_project_hook(
    cwd: Path,
    hook: Path,
    args: Sequence[str],
    *,
    timeout: float,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run ``hook`` in a throwaway container; None when it passed, else the
    refusal (returncode 1, the hook's output as stderr). The hook's type is
    its file name: git always finds it as ``<hooks dir>/<type>``."""
    name = hook.name
    stdin_file = None
    argv = [str(hook), *args]
    if stdin is not None:
        stdin_file = workspace_repository(cwd).git_dir / f"roboco-{name}.stdin"
        await asyncio.to_thread(write_workspace_file, stdin_file, stdin)
        argv = ["sh", "-c", 'exec "$0" "$@" < "$ROBOCO_HOOK_STDIN"', *argv]
    try:
        result = await run_in_workspace_container(
            cwd, argv, timeout=timeout, env=_hook_env(stdin_file), merge_stderr=True
        )
    except WorkspaceContainerError as exc:
        return _refused(name, f"{name} hook ({hook}) could not run: {exc}")
    finally:
        if stdin_file is not None:
            await asyncio.to_thread(
                run_as_workspace_user,
                "rm",
                "-f",
                "--",
                str(stdin_file),
                timeout=_REMOVE_TIMEOUT_SECONDS,
            )
    if result.timed_out:
        return _refused(name, f"{name} hook ({hook}) timed out after {timeout}s")
    if result.returncode != 0:
        return _refused(name, f"{name} hook ({hook}) failed:\n{result.stdout.strip()}")
    return None


def _commit_message(args: Sequence[str]) -> str | None:
    """The message of ``commit -m <a> [-m <b>...]`` as git joins it (one
    paragraph per ``-m``); None when the command carries no ``-m``."""
    paragraphs = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-m"]
    return "\n\n".join(paragraphs) + "\n" if paragraphs else None


def _without_messages(args: Sequence[str]) -> list[str]:
    kept: list[str] = []
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg == "-m" and i + 1 < len(args):
            skip = True
            continue
        kept.append(arg)
    return kept


async def run_commit_hooks(
    cwd: Path, args: list[str], *, timeout: float
) -> tuple[list[str], subprocess.CompletedProcess[str] | None]:
    """``pre-commit``, Entire's message hooks (when Entire records the
    agent's session), then ``commit-msg``, for a ``git commit`` RoboCo is
    about to run; ``--no-verify`` skips the project's two, as with git.
    Returns the commit's (possibly rewritten) args and the refusal, if any.
    When a hook gets the message, it goes through ``<git dir>/COMMIT_EDITMSG``
    (as git does) and the commit reads it back with ``-F``, so a hook that
    edits the message is honored."""
    verify = not skips_hooks(args)
    if verify:
        refused = await _run_pre_commit(cwd, timeout)
        if refused is not None:
            return args, refused
    message = _commit_message(args)
    commit_msg = await find_project_hook(cwd, "commit-msg") if verify else None
    entire = await asyncio.to_thread(records_entire_sessions, cwd)
    if message is None or (commit_msg is None and not entire):
        return args, None
    message_file = workspace_repository(cwd).git_dir / "COMMIT_EDITMSG"
    await asyncio.to_thread(write_workspace_file, message_file, message)
    if entire:
        await run_entire_message_hooks(
            cwd, message_file, verify=verify, timeout=timeout
        )
    refused = None
    if commit_msg is not None:
        refused = await run_project_hook(
            cwd, commit_msg, [str(message_file)], timeout=timeout
        )
    rewritten = [*_without_messages(args), "-F", str(message_file)]
    return rewritten, refused


async def _run_pre_commit(
    cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    pre_commit = await find_project_hook(cwd, "pre-commit")
    if pre_commit is None:
        return None
    return await run_project_hook(cwd, pre_commit, [], timeout=timeout)


def skips_hooks(args: Sequence[str]) -> bool:
    """True for a command run with ``--no-verify`` / ``-n``."""
    return bool(_NO_VERIFY & set(args))
