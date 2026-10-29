"""Hostile-clone suite: an agent controls every byte of its clone.

Every agent container mounts every clone read-write, so each test here plants
one attack an agent (say, prompt-injected by an external PR) could plant, then
drives the git the orchestrator really runs — through ``run_workspace_git``
and the relay — against a real local origin. A canary file proves whether a
planted program ran; a fake token proves where credentials can reach.

Checks of the privilege drop itself run as root with the drop required, i.e.
inside the orchestrator image (scripts/orchestrator_image_suites.sh); elsewhere
they skip.
"""

from __future__ import annotations

import base64
import contextlib
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.services import workspace_git
from roboco.services.git_origin import OriginContext, run_origin_command
from roboco.services.git_relay import GitRelay
from roboco.services.workspace_git import (
    UnsafeWorkspaceGitConfigError,
    make_clone_group_writable,
    prepare_clone_parent,
    remove_workspace_tree,
    run_workspace_git,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOKEN = "ghp_HOSTILECANARY0123456789abcdefABCDEF"
_SUPPORT = Path(__file__).parents[2] / "docker" / "git-support"
_TIMEOUT = 60
_OWNER_ONLY = 0o600
_OWNER_ONLY_DIR = 0o700
# Seconds before the commands these tests make hang are ended.
_DEADLINE = 2


class Lab:
    """A local origin, the project's relay, and one agent clone of it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.canary = root / "canary"
        self.origin = root / "origin.git"
        self.relay = GitRelay(
            "proj", f"file://{self.origin}", _TOKEN, default_branch="main"
        )
        self.ws = root / "workspaces" / "proj" / "backend" / "be-dev-1"

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_workspace_git(self.ws, list(args), timeout=_TIMEOUT, check=check)

    def origin_command(self, *args: str) -> subprocess.CompletedProcess[str]:
        def _local(local: Sequence[str]) -> subprocess.CompletedProcess[str]:
            return run_workspace_git(self.ws, local, timeout=_TIMEOUT, check=False)

        ctx = OriginContext(
            relay=self.relay,
            repo=self.ws,
            local_git=_local,
            from_workspace=True,
            timeout=_TIMEOUT,
        )
        return run_origin_command(ctx, list(args))

    def script(self, name: str, body: str = "") -> Path:
        """An executable that leaves a canary (with its env) when it runs."""
        path = self.root / "attacker" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/bin/sh\n"
            f'mkdir -p "{self.canary}"\n'
            f'{{ id -u; env; }} > "{self.canary}/{name}.$$"\n'
            f"{body}\n"
            "cat\n"
        )
        path.chmod(0o755)
        return path

    def ran(self) -> list[str]:
        if not self.canary.exists():
            return []
        return sorted(entry.name for entry in self.canary.iterdir())

    def everyday_ops(self) -> None:
        """What RoboCo runs in a dev clone across a task."""
        (self.ws / "work.py").write_text("x = 1\n")
        self.git("checkout", "-q", "-b", "feature/t")
        self.git("add", "-A")
        self.git("-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "w")
        self.git("status", "--porcelain")
        self.git("diff", "HEAD~1..HEAD")
        self.git("log", "-1", "--format=%H|%s")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "feature/t")
        pushed = self.origin_command("push", "-u", "origin", "feature/t")
        assert pushed.returncode == 0, pushed.stderr
        fetched = self.origin_command("fetch", "origin", "main")
        assert fetched.returncode == 0, fetched.stderr


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lab:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    support = tmp_path / "support"
    shutil.copytree(_SUPPORT, support)
    monkeypatch.setattr(settings, "git_support_dir", str(support))
    lab = Lab(tmp_path)
    seed = tmp_path / "seed"
    plain = ["git", "-c", "user.name=s", "-c", "user.email=s@s"]
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(lab.origin)], check=True
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README").write_text("seed\n")
    subprocess.run([*plain, "-C", str(seed), "add", "."], check=True)
    subprocess.run([*plain, "-C", str(seed), "commit", "-qm", "seed"], check=True)
    subprocess.run(
        [*plain, "-C", str(seed), "push", "-q", str(lab.origin), "main"], check=True
    )
    fetched = lab.relay.fetch_from_origin(
        ["+refs/heads/*:refs/heads/*"], timeout=_TIMEOUT
    )
    assert fetched.returncode == 0, fetched.stderr
    lab.ws.parent.mkdir(parents=True)
    if os.geteuid() == 0:
        _open_for_the_git_user(tmp_path, lab)
    run_workspace_git(
        lab.ws.parent,
        [
            "clone",
            "-q",
            "--no-local",
            "--branch",
            "main",
            str(lab.relay.path),
            str(lab.ws),
        ],
        timeout=_TIMEOUT,
    )
    lab.git("remote", "set-url", "origin", f"file://{lab.origin}")
    return lab


def _open_for_the_git_user(tmp_path: Path, lab: Lab) -> None:
    """As root, workspace git runs as the git user: let it traverse pytest's
    0700 temp dirs and own the workspace tree, as in production."""
    for directory in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        directory.chmod(directory.stat().st_mode | 0o055)
    workspaces = lab.root / "workspaces"
    for directory in (
        workspaces,
        *[p for p in lab.ws.parents if workspaces in (p, *p.parents)],
    ):
        os.chown(directory, settings.git_user_uid, settings.agent_gid)
        directory.chmod(0o2775)
    lab.root.chmod(0o777)  # canaries are written by whichever uid runs


def _as_the_agent(tree: Path) -> None:
    """Files an agent plants are its own: uid agent_uid, group-writable
    (umask 002). Only root can make them so; otherwise they already are ours."""
    if os.geteuid() != 0:
        return
    for path in (tree, *tree.rglob("*")):
        os.chown(path, settings.agent_uid, settings.agent_gid)
        path.chmod(path.stat().st_mode | stat.S_IRGRP | stat.S_IWGRP)


def _git_config(lab: Lab, *args: str) -> None:
    """Plant config the way an agent would: straight into the file, from
    outside any repository (as root, git refuses a foreign-owned cwd)."""
    config = lab.ws / ".git" / "config"
    subprocess.run(
        ["git", "config", "--file", str(config), *args], cwd=lab.root, check=True
    )


# --------------------------------------------------------------------------- #
# Programs git would start: pinned by command-line overrides, never run
# --------------------------------------------------------------------------- #


_HOOKS = (
    "pre-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "post-checkout",
    "post-merge",
    "pre-push",
    "reference-transaction",
    "post-index-change",
)


def test_planted_repository_hooks_never_run(lab: Lab) -> None:
    for hook in _HOOKS:
        target = lab.ws / ".git" / "hooks" / hook
        shutil.copy(lab.script(hook), target)
        target.chmod(0o755)
    lab.everyday_ops()
    assert lab.ran() == []


def test_a_hooks_path_like_huskys_is_neutralized_not_run(lab: Lab) -> None:
    hooks = lab.ws / ".husky" / "_"
    hooks.mkdir(parents=True)
    for hook in _HOOKS:
        shutil.copy(lab.script(f"husky-{hook}"), hooks / hook)
    _as_the_agent(lab.ws / ".husky")
    _git_config(lab, "core.hooksPath", ".husky/_")
    lab.everyday_ops()
    assert lab.ran() == []


@pytest.mark.parametrize(
    ("key", "extra"),
    [
        ("core.fsmonitor", ()),
        ("core.sshCommand", ()),
        ("core.pager", ()),
        ("core.editor", ()),
        ("sequence.editor", ()),
        ("core.askPass", ()),
        ("credential.helper", ()),
        ("gpg.program", (("commit.gpgSign", "true"),)),
    ],
)
def test_program_settings_are_pinned_off(
    lab: Lab, key: str, extra: tuple[tuple[str, str], ...]
) -> None:
    value = str(lab.script(key.replace(".", "-")))
    _git_config(lab, key, f"!{value}" if key == "credential.helper" else value)
    for extra_key, extra_value in extra:
        _git_config(lab, extra_key, extra_value)
    lab.everyday_ops()
    assert lab.ran() == []


def test_no_network_transport_is_reachable_from_a_clone(lab: Lab) -> None:
    """ext:: runs an arbitrary command; https would carry credentials."""
    _git_config(lab, "protocol.ext.allow", "always")
    ext = lab.git("ls-remote", f"ext::{lab.script('ext')} %s", check=False)
    https = lab.git("ls-remote", "https://example.invalid/x.git", check=False)
    assert ext.returncode != 0
    assert https.returncode != 0
    assert "not allowed" in https.stderr
    assert lab.ran() == []


# --------------------------------------------------------------------------- #
# What no override can pin: refused before git runs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("settings_to_plant", "attributes"),
    [
        (
            (("filter.evil.clean", "{script}"), ("filter.evil.smudge", "{script}")),
            "* filter=evil\n",
        ),
        ((("filter.evil.process", "{script}"),), "* filter=evil\n"),
        ((("diff.evil.textconv", "{script}"),), "* diff=evil\n"),
        ((("diff.external", "{script}"),), ""),
        ((("merge.evil.driver", "{script}"),), "* merge=evil\n"),
        ((("core.alternateRefsCommand", "{script}"),), ""),
        ((("include.path", "{config}"),), ""),
        ((("url.file:///tmp/elsewhere.insteadOf", "{relay}"),), ""),
        ((("remote.origin.uploadpack", "{script}"),), ""),
        ((("core.worktree", "/tmp"),), ""),
    ],
)
def test_unpinnable_settings_refuse_the_command(
    lab: Lab, settings_to_plant: tuple[tuple[str, str], ...], attributes: str
) -> None:
    script = lab.script("driver")
    included = lab.root / "attacker" / "included.cfg"
    included.write_text(f"[core]\n\tfsmonitor = {script}\n")
    for key, value in settings_to_plant:
        _git_config(
            lab,
            key,
            value.format(script=script, config=included, relay=lab.relay.path),
        )
    if attributes:
        (lab.ws / ".gitattributes").write_text(attributes)
    with pytest.raises(UnsafeWorkspaceGitConfigError):
        lab.everyday_ops()
    assert lab.ran() == []


def test_a_redirected_git_dir_is_refused(lab: Lab, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.git"
    shutil.move(str(lab.ws / ".git"), str(elsewhere))
    (lab.ws / ".git").write_text(f"gitdir: {elsewhere}\n")
    with pytest.raises(UnsafeWorkspaceGitConfigError):
        lab.git("status")


def _evil_git_dir(lab: Lab, name: str) -> Path:
    """A copy of the clone's git dir whose config runs a filter driver."""
    evil = lab.root / "workspaces" / "proj" / name
    shutil.copytree(lab.ws / ".git", evil)
    with (evil / "config").open("a") as config:
        config.write(f'[filter "evil"]\n\tclean = {lab.script(name)}\n')
    _as_the_agent(evil)
    return evil


def test_a_planted_commondir_is_refused(lab: Lab) -> None:
    """``.git/commondir`` would make git read config (and objects,
    attributes) from a directory of the agent's choosing — past the audit,
    with no race. Only a worktree's admin dir has one: in a clone's own
    ``.git`` it is tampering, and the command is refused."""
    evil = _evil_git_dir(lab, "commondir")
    (lab.ws / ".git" / "commondir").write_text(f"{evil}\n")
    (lab.ws / ".gitattributes").write_text("* filter=evil\n")
    with pytest.raises(UnsafeWorkspaceGitConfigError, match="commondir"):
        lab.git("status")
    assert lab.ran() == []


def test_a_commondir_planted_after_the_audit_is_still_ignored(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit is check-then-use: a ``commondir`` written right after it
    passes must still not move the config. Git is told its repository by
    layout (GIT_DIR / GIT_COMMON_DIR), so the file is never read."""
    monkeypatch.setattr(workspace_git, "audit_workspace_git_config", lambda _p: None)
    evil = _evil_git_dir(lab, "commondir")
    (lab.ws / ".git" / "commondir").write_text(f"{evil}\n")
    (lab.ws / ".gitattributes").write_text("* filter=evil\n")
    lab.everyday_ops()
    assert lab.ran() == []


def test_a_redirected_worktree_gitfile_is_ignored(lab: Lab) -> None:
    """A per-task worktree's ``.git`` gitfile is agent-writable; git is told
    its repository instead of following the file — in everyday commands and
    when the relay takes the worktree's commit for a push."""
    worktree = lab.ws / ".worktrees" / "t1"
    lab.git("worktree", "add", "-q", "-b", "feature/wt", str(worktree))
    evil = _evil_git_dir(lab, "gitfile")
    (worktree / ".git").write_text(f"gitdir: {evil}\n")
    (worktree / ".gitattributes").write_text("* filter=evil\n")
    (worktree / "w.txt").write_text("w\n")

    def wt(*args: str) -> subprocess.CompletedProcess[str]:
        return run_workspace_git(worktree, list(args), timeout=_TIMEOUT)

    wt("add", "-A")
    wt("-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "wt")
    ctx = OriginContext(
        relay=lab.relay,
        repo=worktree,
        local_git=lambda a: run_workspace_git(
            worktree, a, timeout=_TIMEOUT, check=False
        ),
        from_workspace=True,
        timeout=_TIMEOUT,
    )
    pushed = run_origin_command(ctx, ["push", "origin", "HEAD:refs/heads/feature/wt"])
    assert pushed.returncode == 0, pushed.stderr
    assert lab.ran() == []
    on_origin = subprocess.run(
        ["git", "-C", str(lab.origin), "rev-parse", "feature/wt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert on_origin == wt("rev-parse", "HEAD").stdout.strip()


def test_a_repository_planted_above_a_directory_is_never_discovered(
    lab: Lab,
) -> None:
    """A directory under the workspaces root with no ``.git`` is not a
    repository: git never climbs into the agent-owned team/project dirs."""
    evil = _evil_git_dir(lab, "above")
    shutil.move(evil, lab.ws.parent / ".git")
    fresh = lab.ws.parent / "fresh"
    fresh.mkdir()
    _as_the_agent(fresh)
    status = lab_git_in(fresh, "status")
    assert status.returncode != 0
    assert "not a git repository" in status.stderr
    assert lab.ran() == []


def lab_git_in(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_workspace_git(path, list(args), timeout=_TIMEOUT, check=False)


def test_borrowed_objects_are_refused(lab: Lab) -> None:
    alternates = lab.ws / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(f"{lab.relay.path / 'objects'}\n")
    with pytest.raises(UnsafeWorkspaceGitConfigError):
        lab.git("status")


def test_replace_refs_never_change_what_a_reviewer_sees(lab: Lab) -> None:
    """An agent can plant refs/replace/* so `git show` lies about a commit."""
    lab.everyday_ops()
    real = lab.git("show", "-s", "--format=%s", "HEAD").stdout.strip()
    decoy = lab.git(
        "-c",
        "user.name=a",
        "-c",
        "user.email=a@a",
        "commit-tree",
        "HEAD^{tree}",
        "-m",
        "decoy",
    ).stdout.strip()
    lab.git(
        "update-ref",
        "refs/replace/" + lab.git("rev-parse", "HEAD").stdout.strip(),
        decoy,
    )
    assert lab.git("show", "-s", "--format=%s", "HEAD").stdout.strip() == real


# --------------------------------------------------------------------------- #
# The token: never reachable from anything an agent can influence
# --------------------------------------------------------------------------- #


def _token_forms() -> tuple[str, str]:
    return _TOKEN, base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()


def test_code_running_in_workspace_git_never_sees_the_token(lab: Lab) -> None:
    """Simulates code execution inside workspace git (a support-dir hook runs
    on every commit, and a won race would run planted code the same way):
    its environment and every ancestor's command line carry no token."""
    spy = Path(settings.git_support_dir) / "hooks" / "post-commit"
    spy.write_text(
        "#!/bin/sh\n"
        f'mkdir -p "{lab.canary}"\n'
        f'out="{lab.canary}/spy.$$"\n'
        'env > "$out"\n'
        'pid=$$; while [ "$pid" -gt 1 ]; do\n'
        '  tr "\\0" " " < /proc/$pid/cmdline >> "$out" 2>/dev/null; echo >> "$out"\n'
        '  pid=$(awk "/^PPid:/{print \\$2}" /proc/$pid/status)\n'
        "done\n"
    )
    spy.chmod(0o755)
    lab.everyday_ops()
    dumps = [(lab.canary / name).read_text() for name in lab.ran()]
    assert dumps, "the spy hook should have run on the commit"
    for dump in dumps:
        for form in _token_forms():
            assert form not in dump


def test_the_relay_takes_the_commit_without_reading_clone_config(lab: Lab) -> None:
    """Even if a clone's config steered its own fetches, a push is served by
    the relay against the database URL: origin gets the commit."""
    lab.everyday_ops()
    on_origin = subprocess.run(
        ["git", "-C", str(lab.origin), "rev-parse", "feature/t"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert on_origin == lab.git("rev-parse", "feature/t").stdout.strip()


def test_a_crafted_object_never_reaches_the_relay(lab: Lab) -> None:
    """A commit carrying a ``.gitmodules`` URL that is really an option
    (CVE-2018-17456's shape) is refused by the relay's fsck on the way in:
    the push fails and neither the relay nor origin holds the branch."""
    (lab.ws / ".gitmodules").write_text(
        '[submodule "x"]\n\tpath = x\n\turl = -u./payload\n'
    )
    lab.git("checkout", "-q", "-b", "feature/evil")
    lab.git("add", ".gitmodules")
    lab.git("-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "e")

    pushed = lab.origin_command("push", "-u", "origin", "feature/evil")

    assert pushed.returncode != 0
    assert "gitmodulesUrl" in pushed.stderr
    for repo in (lab.relay.path, lab.origin):
        listed = subprocess.run(
            ["git", "-C", str(repo), "for-each-ref", "refs/heads/feature/evil"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert listed.stdout == ""


def test_a_config_flip_race_never_reaches_the_token(lab: Lab) -> None:
    """The audit runs before git reads its config, so an agent flipping the
    file in a loop can win the race. Whatever it wins runs as the git user,
    with no token anywhere in reach — that, not the audit, is the boundary."""
    script = lab.script("race")
    (lab.ws / ".gitattributes").write_text("* filter=evil\n")
    config = lab.ws / ".git" / "config"
    benign = config.read_text()
    hostile = benign + f'[filter "evil"]\n\tclean = {script}\n'
    stop = threading.Event()

    def _flip() -> None:
        while not stop.is_set():
            config.write_text(hostile)
            config.write_text(benign)

    flipper = threading.Thread(target=_flip)
    flipper.start()
    try:
        for i in range(40):
            (lab.ws / f"f{i}.txt").write_text(str(i))
            # Refused or not, depending on which side of the flip the audit saw.
            with contextlib.suppress(UnsafeWorkspaceGitConfigError):
                lab.git("add", f"f{i}.txt", check=False)
    finally:
        stop.set()
        flipper.join()
    for name in lab.ran():
        dump = (lab.canary / name).read_text()
        for form in _token_forms():
            assert form not in dump


# --------------------------------------------------------------------------- #
# The privilege drop itself (root, drop required: the orchestrator image)
# --------------------------------------------------------------------------- #

root_with_the_drop = pytest.mark.skipif(
    os.geteuid() != 0 or settings.workspace_git_drop != "required",
    reason="needs root with workspace_git_drop=required (the orchestrator image)",
)


@root_with_the_drop
def test_workspace_git_runs_as_the_git_user_without_capabilities(lab: Lab) -> None:
    probe = Path(settings.git_support_dir) / "hooks" / "post-commit"
    probe.write_text(
        "#!/bin/sh\n"
        f'mkdir -p "{lab.canary}"\n'
        "{ id -u; id -G; grep -E '^(CapEff|NoNewPrivs)' /proc/self/status; }"
        f' > "{lab.canary}/probe"\n'
    )
    probe.chmod(0o755)
    lab.everyday_ops()
    lines = (lab.canary / "probe").read_text().split("\n")
    assert int(lines[0]) == settings.git_user_uid
    assert lines[1].split() == [str(settings.agent_gid)]
    assert "CapEff:\t0000000000000000" in lines
    assert "NoNewPrivs:\t1" in lines


@root_with_the_drop
def test_a_hung_git_is_ended_by_its_in_command_timeout(lab: Lab) -> None:
    """git blocked in a hook past its deadline: the in-command ``timeout``,
    running as the git user, ends git and the hook (TERM to their process
    group), the caller gets ``TimeoutExpired`` well before its own wait runs
    out, and no process of the git user is left."""
    hook = Path(settings.git_support_dir) / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 600\n")
    hook.chmod(0o755)
    (lab.ws / "work.py").write_text("x = 1\n")
    lab.git("add", "-A")
    commit = ["-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "w"]

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_workspace_git(lab.ws, commit, timeout=_DEADLINE)
    elapsed = time.monotonic() - started

    assert _DEADLINE <= elapsed < workspace_git.workspace_user_wait_seconds(_DEADLINE)
    assert _processes_of(settings.git_user_uid) == []


@root_with_the_drop
def test_a_command_that_ignores_term_is_killed_after_the_grace(lab: Lab) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        workspace_git.run_as_workspace_user(
            "sh", "-c", "trap '' TERM; sleep 600", cwd=lab.ws, timeout=_DEADLINE
        )
    elapsed = time.monotonic() - started

    killed_at = _DEADLINE + workspace_git._KILL_GRACE_SECONDS
    assert killed_at <= elapsed < workspace_git.workspace_user_wait_seconds(_DEADLINE)
    assert _processes_of(settings.git_user_uid) == []


def _processes_of(uid: int) -> list[str]:
    """Command lines of the live processes whose real uid is ``uid``."""
    found = []
    for status in Path("/proc").glob("[0-9]*/status"):
        with contextlib.suppress(OSError):
            fields = {}
            for line in status.read_text().splitlines():
                name, _, value = line.partition(":")
                fields[name] = value
            if int(fields["Uid"].split()[0]) == uid and "Z" not in fields["State"]:
                found.append((status.parent / "cmdline").read_text())
    return found


@root_with_the_drop
def test_the_repair_walk_never_follows_a_planted_symlink(
    lab: Lab, tmp_path: Path
) -> None:
    secret = tmp_path / "root-only-secret"
    secret.write_text("x")
    secret.chmod(0o600)
    (lab.ws / "innocent").symlink_to(secret)
    make_clone_group_writable(lab.ws)
    st = secret.stat()
    assert st.st_uid == 0
    assert stat.S_IMODE(st.st_mode) == _OWNER_ONLY


def _plant_symlinked_team_dir(lab: Lab, tmp_path: Path) -> Path:
    """An agent swaps a team directory for a symlink to a root-only place
    that holds a directory named like a clone."""
    elsewhere = tmp_path / "root-only"
    (elsewhere / "be-dev-9").mkdir(parents=True)
    (elsewhere / "be-dev-9" / "keep").write_text("x")
    elsewhere.chmod(_OWNER_ONLY_DIR)
    team = lab.root / "workspaces" / "proj" / "frontend"
    team.symlink_to(elsewhere)
    return elsewhere


@root_with_the_drop
def test_a_host_made_skeleton_is_opened_for_the_git_user(lab: Lab) -> None:
    """On the NAS the project/team dirs are the host user's, mode 755: the
    git user could not clone into them until root regroups them."""
    team = lab.root / "workspaces" / "proj" / "ux_ui"
    team.mkdir(mode=0o755)
    os.chown(team, 0, 0)
    clone = team / "ux-dev-1"

    prepare_clone_parent(clone)
    run_workspace_git(
        team,
        ["clone", "-q", "--no-local", str(lab.relay.path), str(clone)],
        timeout=_TIMEOUT,
    )

    assert team.stat().st_gid == settings.agent_gid
    assert team.stat().st_mode & stat.S_IWGRP
    assert clone.stat().st_uid == settings.git_user_uid


@root_with_the_drop
def test_the_skeleton_is_never_made_through_a_planted_symlink(
    lab: Lab, tmp_path: Path
) -> None:
    elsewhere = _plant_symlinked_team_dir(lab, tmp_path)

    with pytest.raises(OSError):
        prepare_clone_parent(lab.root / "workspaces" / "proj" / "frontend" / "x")

    assert elsewhere.stat().st_gid == 0
    assert stat.S_IMODE(elsewhere.stat().st_mode) == _OWNER_ONLY_DIR


@root_with_the_drop
def test_a_workspace_delete_never_follows_a_planted_symlink(
    lab: Lab, tmp_path: Path
) -> None:
    elsewhere = _plant_symlinked_team_dir(lab, tmp_path)
    target = lab.root / "workspaces" / "proj" / "frontend" / "be-dev-9"

    with pytest.raises(OSError):
        remove_workspace_tree(target)
    remove_workspace_tree(target, ignore_errors=True)

    assert (elsewhere / "be-dev-9" / "keep").read_text() == "x"


def test_a_workspace_delete_removes_the_clone(lab: Lab) -> None:
    remove_workspace_tree(lab.ws)
    assert not lab.ws.exists()
    assert lab.ws.parent.is_dir()


@root_with_the_drop
def test_a_file_write_into_an_old_clone_gets_the_group_repair_first(
    lab: Lab,
) -> None:
    """Clones made before the git user existed carry the host's group
    (``admin`` on the NAS) with 755/775 modes: the git user, not in that
    group, could not write its hook-input file into ``.git`` until the clone
    is regrouped, as a git command would have done first."""
    for path in (lab.ws, *lab.ws.rglob("*")):
        if not path.is_symlink():
            os.chown(path, settings.agent_uid, 10)
            path.chmod(path.stat().st_mode & ~stat.S_IWGRP)
    workspace_git._regrouped_clones.discard(str(lab.ws))
    target = lab.ws / ".git" / "roboco-pre-push.stdin"

    workspace_git.write_workspace_file(target, "input\n")

    assert target.read_text() == "input\n"
    assert (lab.ws / ".git").stat().st_gid == settings.agent_gid
