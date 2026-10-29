"""Serve the git commands that name ``origin`` through the project's relay.

``GitService`` keeps issuing plain ``git fetch origin <b>`` / ``git push -u
origin <b>`` / ``git ls-remote --heads origin <b>`` / ``git pull --ff-only`` /
``git remote prune origin``. None of them may run in a repository an agent can
write with a token in reach, so each is served as a relay round-trip with the
same observable effect on the repository: remote-tracking refs, FETCH_HEAD,
upstream config, and git's own output for the caller to parse. Only the
command shapes RoboCo issues are accepted; anything else is refused, never
guessed at.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from roboco.services.git_relay import GitRelay

    LocalGit = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

ORIGIN = "origin"
_HEADS = "refs/heads/"
_TRACKING = "refs/remotes/origin/"
_FETCH_FLAGS = frozenset({"--prune", "--no-tags", "--tags", "--force"})
_PUSH_FLAGS = frozenset({"-u", "--set-upstream", "--force-with-lease"})
_LS_REMOTE_FLAGS = frozenset({"--heads", "--tags"})
_CHECKPOINT_REF = re.compile(r"refs/entire/checkpoints/[0-9A-Z]{2}/[0-9A-Z]{26}")


class UnsupportedOriginCommandError(ValueError):
    """An origin-talking git command RoboCo never issues; refused, not guessed."""


@dataclass(frozen=True)
class OriginContext:
    """Where a translated command runs."""

    relay: GitRelay
    repo: Path
    local_git: LocalGit
    from_workspace: bool
    timeout: float


def is_origin_command(args: Sequence[str]) -> bool:
    """True for a git command that would talk to ``origin``."""
    if not args:
        return False
    if args[0] in {"fetch", "push", "ls-remote"}:
        return ORIGIN in args[1:]
    if args[0] == "pull":
        return True
    return list(args[:3]) == ["remote", "prune", ORIGIN]


def run_origin_command(
    ctx: OriginContext, args: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    """Serve one ``is_origin_command`` through the relay."""
    verb = args[0]
    if verb == "fetch":
        return _fetch(ctx, list(args[1:]))
    if verb == "push":
        return _push(ctx, list(args[1:]))
    if verb == "ls-remote":
        return _ls_remote(ctx, list(args[1:]))
    if verb == "pull":
        return _pull(ctx, list(args[1:]))
    if list(args) == ["remote", "prune", ORIGIN]:
        return _prune(ctx)
    raise UnsupportedOriginCommandError(f"git {' '.join(args)}")


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #


def _split(
    args: list[str], allowed_flags: frozenset[str]
) -> tuple[set[str], list[str]]:
    flags = {a for a in args if a.startswith("-")}
    unknown = flags - allowed_flags
    if unknown:
        raise UnsupportedOriginCommandError(f"unsupported flags {sorted(unknown)}")
    positional = [a for a in args if not a.startswith("-")]
    if not positional or positional[0] != ORIGIN:
        raise UnsupportedOriginCommandError(f"expected 'origin' in {args}")
    return flags, positional[1:]


def _full_ref(name: str) -> str:
    if name.startswith("refs/"):
        return name
    if name.startswith("pull/"):
        return f"refs/{name}"
    return f"{_HEADS}{name}"


def _fetch_refspecs(specs: list[str]) -> tuple[list[str], list[str]]:
    """(relay refspecs, workspace refspecs) for ``git fetch origin <specs>``.

    The relay mirrors origin's refs under their own names; the workspace gets
    what a plain fetch from origin would have given it: ``origin/<b>``
    tracking refs for branches, an explicit destination when one was named,
    and FETCH_HEAD always.
    """
    if not specs:
        return ["+refs/heads/*:refs/heads/*"], ["+refs/heads/*:refs/remotes/origin/*"]
    relay_specs: list[str] = []
    local_specs: list[str] = []
    for spec in specs:
        forced = spec.startswith("+")
        src, sep, dst = spec.lstrip("+").partition(":")
        src = _full_ref(src)
        relay_specs.append(f"+{src}:{src}")
        if sep:
            dst = dst if dst.startswith("refs/") else f"{_HEADS}{dst}"
            local_specs.append(f"{'+' if forced else ''}{src}:{dst}")
        elif src.startswith(_HEADS):
            local_specs.append(f"+{src}:{_TRACKING}{src[len(_HEADS) :]}")
        else:
            local_specs.append(src)
    return relay_specs, local_specs


def _fetch(ctx: OriginContext, args: list[str]) -> subprocess.CompletedProcess[str]:
    flags, specs = _split(args, _FETCH_FLAGS)
    prune = "--prune" in flags
    tags = "--tags" in flags
    relay_specs, local_specs = _fetch_refspecs(specs)
    upstream = ctx.relay.fetch_from_origin(
        relay_specs, prune=prune, tags=tags, timeout=ctx.timeout
    )
    if upstream.returncode != 0:
        return upstream
    local_args = ["fetch", "--prune" if prune else "--no-prune"]
    local_args += ["--tags", "--force"] if tags else ["--no-tags"]
    return ctx.local_git([*local_args, str(ctx.relay.path), *local_specs])


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #


def push_ref_update(args: Sequence[str]) -> tuple[str, str]:
    """``(local ref, remote ref)`` of a ``git push ... origin <src>[:<dst>]``.

    What git hands a ``pre-push`` hook: the local side as git names it
    (``HEAD``, ``refs/heads/<b>``, ``refs/remotes/origin/<b>``) and the full
    remote ref. Raises ``UnsupportedOriginCommandError`` for a push shape
    ``run_origin_command`` would refuse.
    """
    _, specs = _split(list(args[1:]), _PUSH_FLAGS)
    if len(specs) != 1:
        raise UnsupportedOriginCommandError(f"expected one refspec, got {specs}")
    src, sep, dst = specs[0].partition(":")
    remote_ref = _full_ref(dst if sep else src)
    if src == "HEAD":
        return "HEAD", remote_ref
    if src.startswith(f"{ORIGIN}/"):
        return f"refs/remotes/{src}", remote_ref
    return _full_ref(src), remote_ref


def _push(ctx: OriginContext, args: list[str]) -> subprocess.CompletedProcess[str]:
    flags, specs = _split(args, _PUSH_FLAGS)
    if len(specs) != 1:
        raise UnsupportedOriginCommandError(f"expected one refspec, got {specs}")
    src, sep, dst = specs[0].partition(":")
    dst_ref = _full_ref(dst if sep else src)
    if not dst_ref.startswith(_HEADS):
        raise UnsupportedOriginCommandError(f"push target {dst_ref} is not a branch")
    if src.startswith(f"{ORIGIN}/"):
        # Remote-side copy (`origin/<a>:refs/heads/<b>`): the relay already
        # mirrors <a> from the fetch that precedes it.
        return ctx.relay.push_to_origin(
            [f"{_HEADS}{src[len(ORIGIN) + 1 :]}:{dst_ref}"], timeout=ctx.timeout
        )
    return _push_local_commit(ctx, src, dst_ref, flags)


def _push_local_commit(
    ctx: OriginContext, src: str, dst_ref: str, flags: set[str]
) -> subprocess.CompletedProcess[str]:
    src_ref = src if src == "HEAD" else _full_ref(src)
    resolved = ctx.local_git(["rev-parse", "--verify", f"{src_ref}^{{commit}}"])
    if resolved.returncode != 0:
        return _failed(f"error: src refspec {src} does not match any")
    sha = resolved.stdout.strip()
    branch = dst_ref[len(_HEADS) :]
    leases: list[str] = []
    if "--force-with-lease" in flags:
        tracking = ctx.local_git(["rev-parse", "--verify", f"{_TRACKING}{branch}"])
        expected = tracking.stdout.strip() if tracking.returncode == 0 else ""
        leases.append(f"{dst_ref}:{expected}")
    taken, incoming = ctx.relay.take_ref(
        ctx.repo, src_ref, from_workspace=ctx.from_workspace, timeout=ctx.timeout
    )
    if taken.returncode != 0:
        return taken
    try:
        pushed = ctx.relay.push_to_origin(
            [f"{incoming}:{dst_ref}"], leases=leases, timeout=ctx.timeout
        )
    finally:
        ctx.relay.drop_ref(incoming, timeout=ctx.timeout)
    if pushed.returncode != 0:
        return pushed
    # What `git push` itself would have left behind.
    ctx.relay.set_ref(dst_ref, sha, timeout=ctx.timeout)
    ctx.local_git(["update-ref", f"{_TRACKING}{branch}", sha])
    if flags & {"-u", "--set-upstream"} and src != "HEAD":
        local = src_ref[len(_HEADS) :]
        ctx.local_git(["config", f"branch.{local}.remote", ORIGIN])
        ctx.local_git(["config", f"branch.{local}.merge", dst_ref])
    return pushed


def push_checkpoint_refs(
    ctx: OriginContext, refs: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    """Push Entire checkpoint refs (``refs/entire/checkpoints/<xx>/<id>``)
    to origin under their own names, never forced: each is taken into the
    relay like a branch push (fsck'd), then all go in one push. Anything
    that is not a checkpoint ref is refused."""
    for ref in refs:
        if not _CHECKPOINT_REF.fullmatch(ref):
            raise UnsupportedOriginCommandError(f"{ref} is not an Entire checkpoint")
    staged: list[str] = []
    try:
        for ref in refs:
            taken, incoming = ctx.relay.take_ref(
                ctx.repo, ref, from_workspace=ctx.from_workspace, timeout=ctx.timeout
            )
            if taken.returncode != 0:
                return taken
            staged.append(incoming)
        refspecs = [f"{src}:{dst}" for src, dst in zip(staged, refs, strict=True)]
        return ctx.relay.push_to_origin(refspecs, timeout=ctx.timeout)
    finally:
        for incoming in staged:
            ctx.relay.drop_ref(incoming, timeout=ctx.timeout)


# --------------------------------------------------------------------------- #
# ls-remote, pull, remote prune
# --------------------------------------------------------------------------- #


def _ls_remote(ctx: OriginContext, args: list[str]) -> subprocess.CompletedProcess[str]:
    flags, patterns = _split(args, _LS_REMOTE_FLAGS)
    return ctx.relay.ls_remote([*sorted(flags), *patterns], timeout=ctx.timeout)


def _pull(ctx: OriginContext, args: list[str]) -> subprocess.CompletedProcess[str]:
    if args != ["--ff-only"]:
        raise UnsupportedOriginCommandError(f"git pull {' '.join(args)}")
    current = ctx.local_git(["branch", "--show-current"])
    branch = current.stdout.strip()
    if current.returncode != 0 or not branch:
        return _failed("fatal: You are not currently on a branch.")
    fetched = _fetch(ctx, [ORIGIN, branch])
    if fetched.returncode != 0:
        return fetched
    return ctx.local_git(["merge", "--ff-only", f"{_TRACKING}{branch}"])


def _prune(ctx: OriginContext) -> subprocess.CompletedProcess[str]:
    """Drop ``origin/*`` tracking refs whose branch is gone from origin.

    Ref-only, like ``git remote prune``: no objects move.
    """
    listed = ctx.relay.ls_remote(["--heads"], timeout=ctx.timeout)
    if listed.returncode != 0:
        return listed
    live = {
        line.split("\t", 1)[1][len(_HEADS) :]
        for line in listed.stdout.splitlines()
        if "\t" in line
    }
    tracking = ctx.local_git(["for-each-ref", "--format=%(refname)", _TRACKING])
    if tracking.returncode != 0:
        return tracking
    pruned = []
    for ref in tracking.stdout.split():
        branch = ref[len(_TRACKING) :]
        if branch != "HEAD" and branch not in live:
            ctx.local_git(["update-ref", "-d", ref])
            pruned.append(ref)
    return subprocess.CompletedProcess(
        ["git", "remote", "prune", ORIGIN],
        0,
        "".join(f" * [pruned] {ref}\n" for ref in pruned),
        "",
    )


def _failed(message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], 128, "", f"{message}\n")
