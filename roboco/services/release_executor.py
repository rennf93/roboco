"""Release executor — prepares, gates, and publishes a release post-approval.

Runs ONLY after the CEO approves a proposal. It is fail-closed: a red
``make quality`` aborts before any commit, and a red release-commit CI aborts
before any publish. Idempotent: re-running an already-published version is a
no-op. The bump/gate/commit/publish steps live behind a ``ReleaseOps`` seam so
the fail-closed ORDERING (the correctness this feature exists to guarantee) is
unit-tested deterministically; the production ``_GitReleaseOps`` performs the
real git / ``make quality`` / ``gh`` work on a writable clone.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

logger = structlog.get_logger()

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.release_readiness import ReleaseReadinessReport

# CHANGELOG.md is in the canonical bump set but is NOT version-string-replaced —
# it receives a NEW entry. The bump skips it (and any path ending in it).
_CHANGELOG_NAME = "CHANGELOG.md"


@dataclass(frozen=True)
class ReleaseResult:
    """The outcome of an execution attempt — what happened and where it stopped."""

    # published | gate_failed | ci_failed | already_published | no_change
    status: str
    version: str
    files_changed: list[str]
    commit_sha: str | None
    release_url: str | None
    detail: str


class ReleaseOps(Protocol):
    """The side-effecting steps of a release, injected so the executor is testable."""

    async def is_already_published(self, version: str) -> bool: ...

    async def apply_version_bumps(
        self, plan: list[str], new_version: str
    ) -> list[str]: ...

    async def write_changelog_entry(self, entry: str) -> None: ...

    async def run_gate(self) -> bool: ...

    async def commit_and_push(self, version: str) -> str: ...

    async def wait_for_ci(self, commit_sha: str) -> bool: ...

    async def publish_release(self, version: str, notes: str) -> str: ...


class ReleaseExecutor:
    """Orchestrate a fail-closed release from an approved readiness report."""

    def __init__(self, ops: ReleaseOps) -> None:
        self._ops = ops

    async def execute(self, report: ReleaseReadinessReport) -> ReleaseResult:
        """Bump → gate → commit/push → CI → publish, aborting on any red step."""
        version = report.proposed_version
        if await self._ops.is_already_published(version):
            return ReleaseResult(
                status="already_published",
                version=version,
                files_changed=[],
                commit_sha=None,
                release_url=None,
                detail=f"v{version} is already published; nothing to do.",
            )

        files = await self._ops.apply_version_bumps(report.version_bump_plan, version)
        await self._ops.write_changelog_entry(report.drafted_changelog)

        if not await self._ops.run_gate():
            return ReleaseResult(
                status="gate_failed",
                version=version,
                files_changed=files,
                commit_sha=None,
                release_url=None,
                detail="make quality failed — aborted before commit (fail-closed).",
            )

        try:
            commit_sha = await self._ops.commit_and_push(version)
        except RuntimeError as exc:
            # The ops layer raises RuntimeError on a failed add/commit/push
            # (gpgsign/pre-commit reject/no-op bump/non-fast-forward push). That
            # is the correct fail-closed abort at the ops layer; the EXECUTOR
            # turns it into a structured outcome so the CEO sees the cause
            # instead of a 500 bubbling out of ``approve``.
            logger.error("release commit/push failed", error=str(exc)[:300])
            return ReleaseResult(
                status="commit_failed",
                version=version,
                files_changed=files,
                commit_sha=None,
                release_url=None,
                detail=(
                    f"release commit/push failed — not published (fail-closed): {exc}"
                )[:280],
            )

        if not await self._ops.wait_for_ci(commit_sha):
            return ReleaseResult(
                status="ci_failed",
                version=version,
                files_changed=files,
                commit_sha=commit_sha,
                release_url=None,
                detail="release-commit CI was not green — not published (fail-closed).",
            )

        try:
            release_url = await self._ops.publish_release(
                version, report.drafted_changelog
            )
        except RuntimeError as exc:
            # ``gh release create`` failed (auth/quota/network). The commit is
            # already pushed and CI is green, so the release is half-landed —
            # surface it as a structured outcome (not a 500) so the CEO can
            # retry ``gh release create`` for the same version.
            logger.error("release publish failed", error=str(exc)[:300])
            return ReleaseResult(
                status="publish_failed",
                version=version,
                files_changed=files,
                commit_sha=commit_sha,
                release_url=None,
                detail=f"gh release create failed — not published (fail-closed): {exc}",
            )
        logger.info(
            "release published",
            version=version,
            commit=commit_sha,
            url=release_url,
        )
        return ReleaseResult(
            status="published",
            version=version,
            files_changed=files,
            commit_sha=commit_sha,
            release_url=release_url,
            detail=f"Published v{version}.",
        )


# --------------------------------------------------------------------------- #
# Production ops — real git / make / gh on a writable clone.
# --------------------------------------------------------------------------- #

_CI_POLL_INTERVAL_SECONDS = 30
_CI_MAX_POLLS = 80  # ~40 min ceiling

# Subprocess deadlines. A hung git / make / gh would otherwise block the
# CEO-gated release loop indefinitely. Each is generous enough that a
# legitimate, slow operation is never wrongly aborted — only a true hang fails
# closed. Mirrors the quality-gate ``_run_one`` kill-on-timeout idiom.
_GIT_OP_TIMEOUT_SECONDS = 300  # git add/commit/rev-parse/ls-remote/push
_RELEASE_GATE_TIMEOUT_SECONDS = 1800  # make quality — full ruff/mypy/pytest suite
_PUBLISH_TIMEOUT_SECONDS = 300  # gh release create
_CLONE_TIMEOUT_SECONDS = 600  # git clone / rm -rf the release clone

# The conventional non-zero rc a timed-out subprocess reports so every caller's
# fail-closed branch (rc != 0) fires instead of hanging the release loop.
_TIMEOUT_RC = 124


async def _await_proc(
    proc: asyncio.subprocess.Process, timeout: float
) -> tuple[int, str]:
    """Communicate with a subprocess under a deadline; on expiry ``kill()`` the
    child so a hang fails closed instead of wedging the release loop, and
    return a non-zero rc so every caller's fail-closed branch fires."""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        # Reap the killed child so its PID/PGID and the pipe transport are
        # released — a kill without a wait leaves a zombie that wedges the
        # release loop's next clone (and leaks FDs over a long release session).
        with contextlib.suppress(ProcessLookupError):
            proc.kill()  # already-exited between the timeout and the kill is fine
        await proc.wait()
        return _TIMEOUT_RC, f"subprocess timed out after {int(timeout)}s"
    return proc.returncode or 0, out.decode("utf-8", "replace")


@dataclass(frozen=True)
class _ReleaseContext:
    """Writable-clone coordinates for the production release ops."""

    slug: str
    default_branch: str
    root: Path
    auth_url: str
    ci_workflow: str | None


class _GitReleaseOps:
    """Real release steps on a writable clone of RoboCo (token-authenticated)."""

    def __init__(self, session: AsyncSession, ctx: _ReleaseContext) -> None:
        self._session = session
        self._slug = ctx.slug
        self._default_branch = ctx.default_branch
        self._root = ctx.root
        self._auth_url = ctx.auth_url
        self._ci_workflow = ctx.ci_workflow

    async def _git(self, *args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self._root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return await _await_proc(proc, _GIT_OP_TIMEOUT_SECONDS)

    async def is_already_published(self, version: str) -> bool:
        rc, out = await self._git("ls-remote", "--tags", "origin", f"v{version}")
        return rc == 0 and bool(out.strip())

    def _current_version(self) -> str:
        text = (self._root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return match.group(1) if match else ""

    async def apply_version_bumps(self, plan: list[str], new_version: str) -> list[str]:
        """Replace the current version with ``new_version`` across the plan.

        CHANGELOG.md is skipped here (it gets a new entry, not a string-replace)
        but is kept in the returned list since it IS changed by the entry. uv.lock
        is bumped only within the ``roboco`` package block to avoid clobbering a
        dependency that happens to share the version string.
        """
        old = self._current_version()
        for rel in plan:
            if rel.endswith(_CHANGELOG_NAME):
                continue
            path = self._root / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if rel.endswith("uv.lock"):
                text = _bump_uv_lock(text, old, new_version)
            else:
                text = text.replace(old, new_version)
            path.write_text(text, encoding="utf-8")
        return list(plan)

    async def write_changelog_entry(self, entry: str) -> None:
        path = self._root / _CHANGELOG_NAME
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(_insert_changelog_entry(existing, entry), encoding="utf-8")

    async def run_gate(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "make",
            "quality",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        rc, out = await _await_proc(proc, _RELEASE_GATE_TIMEOUT_SECONDS)
        if rc != 0:
            logger.warning(
                "release gate (make quality) failed or timed out",
                tail=out[-2000:],
            )
        return rc == 0

    async def commit_and_push(self, version: str) -> str:
        add_rc, add_out = await self._git("add", "-A")
        if add_rc != 0:
            logger.error("release git add failed", error=add_out.strip()[:300])
            raise RuntimeError(f"release git add failed: {add_out.strip()[:200]}")
        commit_rc, commit_out = await self._git(
            "commit", "-S", "-m", f"chore(release): {version}"
        )
        if commit_rc != 0:
            # A failed commit (gpgsign/pre-commit reject/no-op bump) must abort
            # before push — otherwise the pre-bump base gets tagged as the release.
            logger.error("release commit failed", error=commit_out.strip()[:300])
            raise RuntimeError(f"release commit failed: {commit_out.strip()[:200]}")
        _, out = await self._git("rev-parse", "HEAD")
        sha = out.strip()
        push_rc, push_out = await self._git(
            "push", self._auth_url, f"HEAD:{self._default_branch}"
        )
        if push_rc != 0:
            logger.error("release push failed", error=push_out.strip()[:300])
            raise RuntimeError(f"release push failed: {push_out.strip()[:200]}")
        return sha

    async def wait_for_ci(self, commit_sha: str) -> bool:
        from roboco.services.git import get_git_service

        git = get_git_service(self._session)
        for _ in range(_CI_MAX_POLLS):
            ci = await git.get_latest_ci_conclusion(
                self._slug, workflow=self._ci_workflow
            )
            if ci and ci.get("head_sha") == commit_sha:
                conclusion = (ci.get("conclusion") or "").lower()
                if conclusion == "success":
                    return True
                if conclusion:
                    logger.warning("release CI not green", conclusion=conclusion)
                    return False
            await asyncio.sleep(_CI_POLL_INTERVAL_SECONDS)
        logger.warning("release CI poll timed out", sha=commit_sha)
        return False

    async def publish_release(self, version: str, notes: str) -> str:
        tag = f"v{version}"
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "release",
            "create",
            tag,
            "--title",
            tag,
            "--notes",
            notes,
            "--target",
            self._default_branch,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        rc, out = await _await_proc(proc, _PUBLISH_TIMEOUT_SECONDS)
        text = out.strip()
        if rc != 0:
            logger.error("gh release create failed", error=text[:300])
            raise RuntimeError(f"gh release create failed: {text[:200]}")
        url = next(
            (line.strip() for line in text.splitlines() if line.startswith("http")),
            "",
        )
        return url


def _bump_uv_lock(text: str, old: str, new: str) -> str:
    """Bump only the ``roboco`` package's version inside uv.lock."""
    pattern = re.compile(
        r'(\[\[package\]\]\nname = "roboco"\nversion = )"' + re.escape(old) + '"'
    )
    return pattern.sub(rf'\1"{new}"', text)


def _insert_changelog_entry(existing: str, entry: str) -> str:
    """Insert ``entry`` above the first released version heading (Keep a Changelog)."""
    block = entry.rstrip() + "\n"
    if not existing.strip():
        return block
    lines = existing.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.startswith("## [") and "Unreleased" not in line:
            return "".join(lines[:idx]) + block + "\n" + "".join(lines[idx:])
    return existing.rstrip() + "\n\n" + block


async def get_release_executor(session: AsyncSession) -> ReleaseExecutor:
    """Build a ReleaseExecutor with a production ops over a fresh writable clone."""
    from roboco.config import settings
    from roboco.services.project import get_project_service
    from roboco.services.workspace import _inject_token_into_url

    slug = (settings.self_heal_project_slug or "roboco-api").strip()
    project_svc = get_project_service(session)
    project = await project_svc.get_by_slug(slug)
    if project is None:
        raise RuntimeError(f"release executor: project {slug!r} not found")
    token = await project_svc.get_decrypted_token_by_slug(slug)
    git_url = str(project.git_url)
    default_branch = str(project.default_branch or "master")
    auth_url = _inject_token_into_url(git_url, token)
    root = await _prepare_release_clone(slug, auth_url, default_branch)
    ctx = _ReleaseContext(
        slug=slug,
        default_branch=default_branch,
        root=root,
        auth_url=auth_url,
        ci_workflow=(settings.self_heal_ci_workflow or None),
    )
    return ReleaseExecutor(_GitReleaseOps(session, ctx))


async def _prepare_release_clone(slug: str, auth_url: str, default_branch: str) -> Path:
    """Fresh, writable, token-authenticated clone for the release commit + push."""
    from roboco.config import settings

    root = Path(settings.workspaces_root) / "_release" / slug
    if root.exists():
        await _run(["rm", "-rf", str(root)])
    root.parent.mkdir(parents=True, exist_ok=True)
    rc, out = await _run(
        ["git", "clone", "--branch", default_branch, auth_url, str(root)]
    )
    if rc != 0:
        raise RuntimeError(f"release clone failed: {out.strip()[:200]}")
    return root


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    return await _await_proc(proc, _CLONE_TIMEOUT_SECONDS)
