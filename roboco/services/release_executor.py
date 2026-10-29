"""Release executor — prepares, gates, and publishes a release post-approval.

Runs ONLY after the CEO approves a proposal. It is fail-closed: a red
``make quality`` aborts before any commit, and a red release-commit CI aborts
before any publish. Idempotent: re-running an already-published version is a
no-op. The bump/gate/commit/publish steps live behind a ``ReleaseOps`` seam so
the fail-closed ORDERING (the correctness this feature exists to guarantee) is
unit-tested deterministically; the production ``_GitReleaseOps`` performs the
real git / ``make quality`` work on a writable clone and publishes the GitHub
release over REST (the orchestrator image ships no ``gh`` binary).

On GitHub the commits that land on prod (the env-chain merges and the release
commit) are written by GitHub's API from what the clone computed, so GitHub
signs them; a forge without that API gets the clone's commits by ``git push``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from roboco.services.git_origin import (
    OriginContext,
    is_origin_command,
    run_origin_command,
)
from roboco.services.git_relay import (
    GitRelay,
    relay_for,
    release_clone_path,
    run_private_git,
)
from roboco.services.release_readiness import project_version

logger = structlog.get_logger()

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.forge.base import GitProvider, RepoRef
    from roboco.services.release_readiness import ReleaseReadinessReport

# CHANGELOG.md is in the canonical bump set but is NOT version-string-replaced —
# it receives a NEW entry. The bump skips it (and any path ending in it).
_CHANGELOG_NAME = "CHANGELOG.md"


def _release_subject(version: str) -> str:
    """The release commit's subject; half-landed detection matches on it."""
    return f"chore(release): {version}"


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

    async def release_commit_sha(self, version: str) -> str | None: ...

    async def apply_version_bumps(
        self, plan: list[str], new_version: str
    ) -> list[str]: ...

    async def write_changelog_entry(self, entry: str) -> None: ...

    async def promote_env_chain(self) -> None: ...

    async def run_gate(self) -> tuple[bool, str]: ...

    async def commit_and_push(self, version: str) -> str: ...

    async def wait_for_ci(self, commit_sha: str) -> bool: ...

    async def publish_release(self, version: str, notes: str) -> str: ...


class ReleaseExecutor:
    """Orchestrate a fail-closed release from an approved readiness report."""

    def __init__(self, ops: ReleaseOps) -> None:
        self._ops = ops

    async def execute(self, report: ReleaseReadinessReport) -> ReleaseResult:
        """Bump → gate → commit/push → CI → publish, aborting on any red step.

        A half-landed (publish_failed) retry — a prior ``chore(release): {ver}``
        commit already on the branch but no tag — skips the bump/changelog/gate/
        commit pipeline (which would duplicate the changelog entry and land a
        second release commit) and rejoins the shared CI → publish tail on the
        existing commit.
        """
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

        # Half-landed detection (publish_failed retry): a prior release commit
        # on the branch means the pipeline already ran — only the publish step
        # failed. Re-running it would re-insert the changelog entry (duplicate,
        # above the already-present heading) and land a SECOND release commit.
        # Reuse the existing commit and rejoin the shared CI → publish tail.
        # None when no prior release commit exists (fresh release).
        existing_sha = await self._ops.release_commit_sha(version)
        if existing_sha is not None:
            commit_sha = existing_sha
            files: list[str] = []
        else:
            fresh = await self._run_fresh_release(version, report)
            if isinstance(fresh, ReleaseResult):
                return fresh  # promotion / gate / commit failed (fail-closed)
            commit_sha, files = fresh

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
            # The GitHub release POST failed (auth/quota/network). The commit
            # is already pushed and CI is green, so the release is half-landed
            # — surface it as a structured outcome (not a 500) so the CEO can
            # retry the publish for the same version.
            logger.error("release publish failed", error=str(exc)[:300])
            return ReleaseResult(
                status="publish_failed",
                version=version,
                files_changed=files,
                commit_sha=commit_sha,
                release_url=None,
                detail=f"release publish failed — not published (fail-closed): {exc}",
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

    async def _run_fresh_release(
        self, version: str, report: ReleaseReadinessReport
    ) -> ReleaseResult | tuple[str, list[str]]:
        """Fresh-release pipeline: promote → bump → changelog → gate → commit.

        Returns ``(commit_sha, files)`` on success, or a ``ReleaseResult`` on a
        fail-closed abort (promotion conflict / unresolvable version / red gate /
        commit-push failure) so ``execute`` surfaces it to the CEO as a
        structured outcome, not a 500. Every ops call that can raise is wrapped:
        an uncaught one here reaches the caller's generic handler and loses the
        status vocabulary this method exists to produce.
        """
        # Full-chain promotion: merge the env ladder head→…→prod into the prod
        # checkout before bumping, so the release commits the promoted state.
        try:
            await self._ops.promote_env_chain()
        except RuntimeError as exc:
            logger.error("env-chain promotion failed", error=str(exc)[:300])
            return ReleaseResult(
                status="promotion_failed",
                version=version,
                files_changed=[],
                commit_sha=None,
                release_url=None,
                detail=(
                    f"env-chain promotion failed — not published (fail-closed): {exc}"
                )[:280],
            )
        try:
            files = await self._ops.apply_version_bumps(
                report.version_bump_plan, version
            )
        except RuntimeError as exc:
            # Raised when the repo's current version is unresolvable, which would
            # otherwise let an empty-string replace corrupt every planned file.
            logger.error("release version bump failed", error=str(exc)[:300])
            return ReleaseResult(
                status="bump_failed",
                version=version,
                files_changed=[],
                commit_sha=None,
                release_url=None,
                detail=(f"version bump failed, not published (fail-closed): {exc}")[
                    :280
                ],
            )
        await self._ops.write_changelog_entry(report.drafted_changelog)

        gate_ok, gate_detail = await self._ops.run_gate()
        if not gate_ok:
            return ReleaseResult(
                status="gate_failed",
                version=version,
                files_changed=files,
                commit_sha=None,
                release_url=None,
                detail=(f"release gate refused — {gate_detail} (fail-closed).")[:280],
            )

        try:
            commit_sha = await self._ops.commit_and_push(version)
        except RuntimeError as exc:
            # The ops layer raises RuntimeError on a failed add/commit/push
            # (gpgsign/pre-commit reject/no-op bump/non-fast-forward push).
            logger.error("release commit/push failed", error=str(exc)[:300])
            detail = (
                f"release commit/push failed — not published (fail-closed): {exc}"
            )[:280]
            return ReleaseResult(
                status="commit_failed",
                version=version,
                files_changed=files,
                commit_sha=None,
                release_url=None,
                detail=detail,
            )
        return commit_sha, files


# --------------------------------------------------------------------------- #
# Production ops — real git / make on a writable clone; publish via REST.
# --------------------------------------------------------------------------- #

_CI_POLL_INTERVAL_SECONDS = 30
_CI_MAX_POLLS = 80  # ~40 min ceiling

# Subprocess deadlines. A hung git / make would otherwise block the
# CEO-gated release loop indefinitely. Each is generous enough that a
# legitimate, slow operation is never wrongly aborted — only a true hang fails
# closed. Mirrors the quality-gate ``_run_one`` kill-on-timeout idiom.
_GIT_OP_TIMEOUT_SECONDS = 300  # git add/commit/rev-parse/ls-remote/push
_RELEASE_GATE_TIMEOUT_SECONDS = 1800  # make quality — full ruff/mypy/pytest suite
_PUBLISH_TIMEOUT_SECONDS = 300  # GitHub release POST (httpx client timeout)
_CLONE_TIMEOUT_SECONDS = 600  # git clone / rm -rf the release clone

# The conventional non-zero rc a timed-out subprocess reports so every caller's
# fail-closed branch (rc != 0) fires instead of hanging the release loop.
_TIMEOUT_RC = 124

# GitHub REST "created" — the only success status for the release POST.
_HTTP_CREATED = 201
_HTTP_OK = 200
_HTTP_NO_CONTENT = 204
_HTTP_UNPROCESSABLE = 422
# A forge adapter's answer for a call its forge has no API for.
_HTTP_NOT_IMPLEMENTED = 501

# Where the GitHub path assembles a release before prod moves: GitHub writes
# the env-chain merges and the release commit here, then prod fast-forwards to
# the result in one ref update, the API twin of the git path's single push.
# Prod never holds a half-promoted state, and a moved prod refuses the update.
# The name sits outside the branch filters of RoboCo's own CI workflows.
_STAGING_BRANCH = "release-manager/staging"


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
    except asyncio.CancelledError:
        # An outer cancellation (e.g. the release loop's own task being
        # cancelled mid-op) throws in here instead of the TimeoutError above —
        # same orphaned child + leaked FDs if left unkilled. Mirrors
        # quality_gate.py's ``_run_one`` handler for the identical shape.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace")


@dataclass(frozen=True)
class _ReleaseContext:
    """Writable-clone coordinates for the production release ops."""

    slug: str
    prod_branch: str
    root: Path
    git_url: str
    # Every origin round-trip (fetch, ls-remote, push) goes through the
    # project's relay: the token never enters a process in the clone.
    relay: GitRelay
    ci_workflow: str | None
    # Env-ladder rung branches to merge into the prod checkout head→…→just-
    # below-prod before the bump (full-chain promotion). Empty for a
    # degenerate (head==prod) ladder → promote_env_chain is a no-op.
    env_chain: list[str]


@dataclass(frozen=True)
class _PromotionStep:
    """One env-chain rung merged into the prod checkout, as git did it locally.

    ``before`` and ``after`` are HEAD around the merge, ``source`` the rung tip
    merged: ``after == before`` was already up to date, ``after == source`` a
    fast-forward, anything else a merge commit GitHub re-creates.
    """

    branch: str
    before: str
    source: str
    after: str


@dataclass(frozen=True)
class _ForgeCommits:
    """The forge calls of the GitHub landing, each read into a sha or a
    ``RuntimeError``: transport stays in the forge layer, meaning lives here."""

    forge: GitProvider
    repo: RepoRef
    token: str

    async def open_staging(self, base: str) -> bool:
        """Point the staging branch at ``base``. False when the forge cannot
        write commits itself (a shaped 501); a staging branch an interrupted
        run left behind is reset."""
        resp = await self.forge.create_branch_ref(
            self.repo, self.token, _STAGING_BRANCH, sha=base
        )
        if resp.status_code == _HTTP_NOT_IMPLEMENTED:
            return False
        if resp.status_code == _HTTP_UNPROCESSABLE:
            resp = await self.forge.update_branch_ref(
                self.repo, self.token, _STAGING_BRANCH, sha=base, force=True
            )
            _require_status(resp, _HTTP_OK, "reset the staging branch")
            return True
        _require_status(resp, _HTTP_CREATED, "create the staging branch")
        return True

    async def fast_forward(self, branch: str, sha: str) -> None:
        """Move ``branch`` to ``sha``; GitHub refuses a non-fast-forward."""
        resp = await self.forge.update_branch_ref(
            self.repo, self.token, branch, sha=sha, force=False
        )
        _require_status(resp, _HTTP_OK, f"fast-forward {branch}")

    async def merge_into_staging(self, source: str, message: str) -> tuple[str, str]:
        """Merge commit ``source`` into staging; ``(merge sha, its tree)``."""
        resp = await self.forge.merge_branch(
            self.repo,
            self.token,
            base=_STAGING_BRANCH,
            head=source,
            commit_message=message,
        )
        _require_status(resp, _HTTP_CREATED, f"merge {source[:12]}")
        body = resp.json()
        return str(body["sha"]), str(body["commit"]["tree"]["sha"])

    async def commit_on_staging(
        self,
        expected_head: str,
        headline: str,
        additions: dict[str, bytes],
        deletions: list[str],
    ) -> tuple[str, str]:
        """Commit whole files on staging; ``(commit sha, its tree)``."""
        resp = await self.forge.create_commit_on_branch(
            self.repo,
            self.token,
            _STAGING_BRANCH,
            expected_head_oid=expected_head,
            headline=headline,
            additions=additions,
            deletions=deletions,
            timeout=_GIT_OP_TIMEOUT_SECONDS,
        )
        _require_status(resp, _HTTP_OK, "commit the release")
        payload = resp.json()
        if payload.get("errors"):
            errors = str(payload["errors"])[:200]
            raise RuntimeError(f"release push failed: commit the release: {errors}")
        commit = payload["data"]["createCommitOnBranch"]["commit"]
        return str(commit["oid"]), str(commit["tree"]["oid"])

    async def close_staging(self) -> None:
        """Delete the staging branch. Best effort: a leftover is reset by the
        next release's ``open_staging``."""
        import httpx

        try:
            resp = await self.forge.delete_branch_ref(
                self.repo, self.token, _STAGING_BRANCH
            )
        except httpx.HTTPError as exc:
            logger.warning("release staging branch not deleted", error=str(exc))
            return
        if resp.status_code != _HTTP_NO_CONTENT:
            logger.warning(
                "release staging branch not deleted", status=resp.status_code
            )


def _require_status(resp: Any, expected: int, step: str) -> None:
    """Raise the release's fail-closed ``RuntimeError`` unless the forge's
    answer to ``step`` carries the ``expected`` status."""
    if resp.status_code != expected:
        raise RuntimeError(
            f"release push failed: {step}: HTTP {resp.status_code}: "
            f"{str(resp.text)[:200]}"
        )


class _GitReleaseOps:
    """Real release steps on a dispatcher-owned writable clone of RoboCo."""

    def __init__(self, session: AsyncSession, ctx: _ReleaseContext) -> None:
        self._session = session
        self._slug = ctx.slug
        self._default_branch = ctx.prod_branch
        self._root = ctx.root
        self._git_url = ctx.git_url
        self._relay = ctx.relay
        self._head_branch = ctx.env_chain[0] if ctx.env_chain else ctx.prod_branch
        self._ci_workflow = ctx.ci_workflow
        self._env_chain = ctx.env_chain
        # What promote_env_chain did, for the GitHub path to re-create.
        self._promotion: list[_PromotionStep] = []

    async def _git(self, *args: str) -> tuple[int, str]:
        """``git <args>`` in the release clone; ``(rc, stdout+stderr)``.

        An ``origin`` command goes through the relay; anything else runs as
        root in the clone (dispatcher-owned, under the private root). A hang
        past the deadline reports rc 124 so every fail-closed branch fires.
        """
        root = self._root

        def _local(local_args: Sequence[str]) -> subprocess.CompletedProcess[str]:
            return run_private_git(
                root, local_args, timeout=_GIT_OP_TIMEOUT_SECONDS, check=False
            )

        def _run() -> subprocess.CompletedProcess[str]:
            if not is_origin_command(args):
                return _local(args)
            ctx = OriginContext(
                relay=self._relay,
                repo=root,
                local_git=_local,
                from_workspace=False,
                timeout=_GIT_OP_TIMEOUT_SECONDS,
            )
            return run_origin_command(ctx, args)

        try:
            result = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return _TIMEOUT_RC, f"subprocess timed out after {_GIT_OP_TIMEOUT_SECONDS}s"
        return result.returncode, result.stdout + result.stderr

    async def is_already_published(self, version: str) -> bool:
        rc, out = await self._git("ls-remote", "--tags", "origin", f"v{version}")
        return rc == 0 and bool(out.strip())

    async def release_commit_sha(self, version: str) -> str | None:
        """Detect a half-landed release: a ``chore(release): {version}`` commit
        already on the branch (a publish_failed retry — commit pushed, no tag
        yet). Returns that commit's sha, or None when the clone is not yet at
        ``version`` (no bump happened) or no such commit exists.

        The clone is fresh per execute(), so if the working version already
        equals ``version`` AND the release commit is in history, the pipeline
        ran in a prior attempt and must not re-run (it would duplicate the
        changelog entry and land a second release commit).
        """
        if self._current_version() != version:
            return None
        rc, out = await self._git("log", "-n", "50", "--format=%H%x00%s")
        if rc != 0:
            return None
        wanted = _release_subject(version)
        for line in out.splitlines():
            sha, _, subject = line.partition("\x00")
            if sha and subject.strip() == wanted:
                return sha.strip()
        return None

    def _current_version(self) -> str:
        """Whichever manifest this repo actually ships, not pyproject.toml only.

        Readiness probes pyproject/package.json/Cargo.toml/VERSION to BUILD the
        bump plan; reading only pyproject here made the executor blind to a
        version readiness had already found on a non-Python repo.
        """
        return project_version(self._root)

    async def apply_version_bumps(self, plan: list[str], new_version: str) -> list[str]:
        """Replace the current version with ``new_version`` across the plan.

        CHANGELOG.md is skipped here (it gets a new entry, not a string-replace)
        but is kept in the returned list since it IS changed by the entry. uv.lock
        is bumped only within this repo's OWN package block to avoid clobbering a
        dependency that happens to share the version string.

        Fail-closed on an unresolvable current version: ``str.replace("", x)``
        inserts ``x`` between every character of every planned file, so an empty
        ``old`` must abort the release, never fall through to the loop.
        """
        old = self._current_version()
        if not old:
            raise RuntimeError(
                "release bump aborted: no current version found in the repo's "
                "manifest (pyproject.toml / package.json / Cargo.toml / VERSION)"
            )
        package = _lock_package_name(self._root)
        for rel in plan:
            if rel.endswith(_CHANGELOG_NAME):
                continue
            path = self._root / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if rel.endswith("uv.lock"):
                text = _bump_uv_lock(text, old, new_version, package)
            else:
                text = text.replace(old, new_version)
            path.write_text(text, encoding="utf-8")
        return list(plan)

    async def write_changelog_entry(self, entry: str) -> None:
        path = self._root / _CHANGELOG_NAME
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(_insert_changelog_entry(existing, entry), encoding="utf-8")

    async def promote_env_chain(self) -> None:
        """Merge the env ladder head→…→just-below-prod into the prod checkout.

        Brings the dev trunk's unreleased work down to prod before the version
        bump, so the release commits + tags the promoted state (generalizes the
        slave→master promotion to the full declared chain). ``--no-edit``,
        fail-closed on a fetch or merge conflict — a divergent rung aborts the
        release before any bump rather than landing a half-promoted prod. No-op
        for a degenerate (head==prod) ladder (empty chain).

        Each merge is recorded as a ``_PromotionStep``: on GitHub, prod never
        receives these local merges, GitHub re-creates them at commit time.
        """
        if not self._env_chain:
            return
        # The container has no global git identity (commit_and_push sets it
        # per-clone too, but only AFTER promotion). The merge below creates a
        # merge commit, so set identity first or it refuses outright.
        from roboco.config import settings

        await self._git("config", "user.name", settings.release_git_name)
        await self._git("config", "user.email", settings.release_git_email)
        # Fresh ``--branch <prod>`` clone only has prod's history — fetch every
        # rung branch so ``origin/<branch>`` resolves for the merges.
        rc, out = await self._git("fetch", "origin")
        if rc != 0:
            raise RuntimeError(f"env-chain fetch failed: {out.strip()[:200]}")
        for branch in self._env_chain:
            before = await self._rev_parse("HEAD")
            source = await self._rev_parse(f"origin/{branch}")
            rc, out = await self._git("merge", "--no-edit", f"origin/{branch}")
            if rc != 0:
                raise RuntimeError(
                    f"env-chain merge {branch}→prod failed: {out.strip()[:200]}"
                )
            after = await self._rev_parse("HEAD")
            self._promotion.append(_PromotionStep(branch, before, source, after))

    async def run_gate(self) -> tuple[bool, str]:
        """Re-verify the head rung's CI verdict at execute time.

        The readiness sweep already required a green run on the head rung to
        propose at all; re-checking the same signal here keeps the gate
        fail-closed without re-running the suite inside the production
        container, whose env (armed flags, host mounts, live Redis) breaks
        clean-env test assumptions wholesale. The pushed release commit still
        gets its own ``wait_for_ci`` before publish.
        """
        rc, sha_out = await self._git("rev-parse", f"origin/{self._head_branch}")
        if rc != 0:
            return False, f"cannot resolve origin/{self._head_branch}"
        head_sha = sha_out.strip()
        from roboco.services.git import get_git_service

        git = get_git_service(self._session)
        ci = await git.get_latest_ci_conclusion(
            self._slug, workflow=self._ci_workflow, head_sha=head_sha
        )
        conclusion = ((ci or {}).get("conclusion") or "absent").lower()
        detail = f"CI on {self._head_branch}@{head_sha[:8]} is {conclusion}"
        if conclusion != "success":
            logger.warning("release gate refused", detail=detail)
        return conclusion == "success", detail

    async def commit_and_push(self, version: str) -> str:
        """Commit the release in the clone, then land it on prod; the sha
        that landed.

        On GitHub, GitHub's API re-creates the env-chain merges and the
        release commit (so GitHub signs them) and prod fast-forwards to them;
        a forge without that API gets the clone's own commits by ``git push``.
        Either way prod moves once and only by fast-forward, so a prod that
        moved since the clone refuses and nothing lands.
        """
        await self._commit_release_locally(version)
        landed = await self._land_through_forge_api(version)
        if landed is not None:
            return landed
        return await self._push_with_git()

    async def _commit_release_locally(self, version: str) -> None:
        from roboco.config import settings

        add_rc, add_out = await self._git("add", "-A")
        if add_rc != 0:
            logger.error("release git add failed", error=add_out.strip()[:300])
            raise RuntimeError(f"release git add failed: {add_out.strip()[:200]}")
        # The clone is fresh per execute and the container has no global git
        # identity — set it per-clone or the commit refuses outright.
        await self._git("config", "user.name", settings.release_git_name)
        await self._git("config", "user.email", settings.release_git_email)
        # Only a git-pushed commit keeps this signature; GitHub's path
        # re-creates the commit and signs it itself.
        sign = ["-S"] if settings.release_sign_commits else []
        commit_rc, commit_out = await self._git(
            "commit", *sign, "-m", _release_subject(version)
        )
        if commit_rc != 0:
            # A failed commit (gpgsign/pre-commit reject/no-op bump) must abort
            # before push — otherwise the pre-bump base gets tagged as the release.
            logger.error("release commit failed", error=commit_out.strip()[:300])
            raise RuntimeError(f"release commit failed: {commit_out.strip()[:200]}")

    async def _push_with_git(self) -> str:
        _, out = await self._git("rev-parse", "HEAD")
        sha = out.strip()
        push_rc, push_out = await self._git(
            "push", "origin", f"HEAD:{self._default_branch}"
        )
        if push_rc != 0:
            logger.error("release push failed", error=push_out.strip()[:300])
            raise RuntimeError(f"release push failed: {push_out.strip()[:200]}")
        return sha

    async def _land_through_forge_api(self, version: str) -> str | None:
        """Have the forge write the release's commits and fast-forward prod to
        them; the release sha it wrote, or None when the remote has no such
        API (the adapter answers 501, or the forge layer does not resolve the
        URL) and the caller pushes with git.

        GitHub re-creates the local commits on the staging branch, the
        env-chain merges through its merges API and the release commit
        through ``createCommitOnBranch``, each checked to hold exactly the
        tree the clone built. Prod moves last, in one fast-forward.
        """
        import httpx

        from roboco.exceptions import GitError
        from roboco.services.forge.router import ForgeRouter
        from roboco.services.git import GitService

        try:
            repo = GitService._parse_git_url(self._git_url)
        except GitError:
            return None
        token = await self._forge_token()
        if not token:
            raise RuntimeError(f"release push failed: no git token for {self._slug}")
        if self._promotion:
            base = self._promotion[0].before
        else:
            base = await self._rev_parse("HEAD^")
        commits = _ForgeCommits(ForgeRouter(), repo, token)
        try:
            if not await commits.open_staging(base):
                return None
            try:
                head = await self._recreate_promotion(commits, base)
                landed = await self._recreate_release_commit(commits, head, version)
                await commits.fast_forward(self._default_branch, landed)
            finally:
                await commits.close_staging()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            # A transport failure or an answer missing the expected fields.
            logger.error("release push failed", error=str(exc)[:300])
            raise RuntimeError(f"release push failed: {exc!s}"[:280]) from exc
        return landed

    async def _recreate_promotion(self, commits: _ForgeCommits, base: str) -> str:
        """Replay ``promote_env_chain``'s merges on staging; the head after."""
        head = base
        for step in self._promotion:
            if step.after == step.before:
                continue
            if step.after == step.source:
                await commits.fast_forward(_STAGING_BRANCH, step.source)
                head = step.source
                continue
            message = await self._commit_message(step.after)
            head, tree = await commits.merge_into_staging(step.source, message)
            await self._require_same_tree(step.after, tree, f"{step.branch} merge")
        return head

    async def _recreate_release_commit(
        self, commits: _ForgeCommits, head: str, version: str
    ) -> str:
        """Commit the local release commit's file changes on staging; its sha."""
        local_release = await self._rev_parse("HEAD")
        additions, deletions = await self._file_changes(
            await self._rev_parse("HEAD^"), local_release
        )
        sha, tree = await commits.commit_on_staging(
            head, _release_subject(version), additions, deletions
        )
        await self._require_same_tree(local_release, tree, "release commit")
        return sha

    async def _file_changes(
        self, old: str, new: str
    ) -> tuple[dict[str, bytes], list[str]]:
        """What ``new`` changes against ``old``: whole contents of the files it
        adds or modifies (read from the clone, which has ``new`` checked out)
        and the paths it deletes. A checkout filter (eol, clean/smudge) on a
        changed file would make these bytes differ from the committed blob;
        the tree check after the commit then refuses the landing."""
        rc, out = await self._git(
            "diff-tree", "-r", "--no-renames", "--name-status", "-z", old, new
        )
        if rc != 0:
            raise RuntimeError(f"release push failed: diff-tree: {out.strip()[:200]}")
        # -z ends every field, status and path alike, with a NUL.
        fields = out.split("\0")[:-1]
        additions: dict[str, bytes] = {}
        deletions: list[str] = []
        for status, path in zip(fields[0::2], fields[1::2], strict=True):
            if status == "D":
                deletions.append(path)
                continue
            try:
                additions[path] = (self._root / path).read_bytes()
            except OSError as exc:
                raise RuntimeError(f"release push failed: read {path}: {exc}") from exc
        return additions, deletions

    async def _require_same_tree(
        self, local_commit: str, remote_tree: str, what: str
    ) -> None:
        """Fail closed unless GitHub's commit holds the tree the clone built.
        Trees are content-addressed: equal ids mean byte-identical files."""
        local_tree = await self._rev_parse(f"{local_commit}^{{tree}}")
        if remote_tree != local_tree:
            raise RuntimeError(
                f"release push failed: GitHub's {what} has tree "
                f"{remote_tree[:12]}, the clone's {local_tree[:12]}; prod not moved"
            )

    async def _rev_parse(self, ref: str) -> str:
        rc, out = await self._git("rev-parse", "--verify", "--quiet", ref)
        words = out.split()
        if rc != 0 or not words:
            raise RuntimeError(f"cannot resolve {ref} in the release clone")
        return words[0]

    async def _commit_message(self, sha: str) -> str:
        rc, out = await self._git("show", "-s", "--format=%B", sha)
        if rc != 0:
            raise RuntimeError(f"cannot read the message of {sha[:12]}")
        return out.strip()

    async def _forge_token(self) -> str | None:
        """The project's forge token, resolved per call: a GitHub App
        installation token lasts an hour, and a release can outlive one."""
        from roboco.services.project import ProjectService

        return await ProjectService(self._session).get_decrypted_token_by_slug(
            self._slug
        )

    async def wait_for_ci(self, commit_sha: str) -> bool:
        from roboco.services.git import get_git_service

        git = get_git_service(self._session)
        for _ in range(_CI_MAX_POLLS):
            ci = await git.get_latest_ci_conclusion(
                self._slug,
                workflow=self._ci_workflow,
                head_sha=commit_sha,
                branch=self._default_branch,
            )
            if ci and ci.get("head_sha") == commit_sha:
                conclusion = (ci.get("conclusion") or "").lower()
                if conclusion == "success":
                    return True
                # A non-success on the same sha may be a failed first attempt
                # while a re-run is still in_progress (GitHub's
                # status=completed filter excludes it). Keep polling through
                # the window; only loop exhaustion returns False.
            await asyncio.sleep(_CI_POLL_INTERVAL_SECONDS)
        logger.warning("release CI poll timed out", sha=commit_sha)
        return False

    async def publish_release(self, version: str, notes: str) -> str:
        # REST, not `gh release create` — the orchestrator image ships no gh
        # binary, so the CLI path fails at publish time with a missing binary.
        import httpx

        from roboco.services.forge.github import GitHubProvider
        from roboco.services.git import GitService

        tag = f"v{version}"
        token = await self._forge_token()
        if not token:
            raise RuntimeError(f"release publish failed: no git token for {self._slug}")
        repo_ref = GitService._parse_git_url(self._git_url)
        try:
            resp = await GitHubProvider().create_release(
                repo_ref,
                token,
                tag_name=tag,
                name=tag,
                body=notes,
                target_commitish=self._default_branch,
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"release publish failed: {e}") from e
        if resp.status_code != _HTTP_CREATED:
            detail = resp.text[:200]
            logger.error(
                "release publish failed", status=resp.status_code, error=detail
            )
            raise RuntimeError(
                f"release publish failed: HTTP {resp.status_code}: {detail}"
            )
        return str(resp.json().get("html_url") or "")


def _lock_package_name(root: Path) -> str:
    """This repo's own distribution name, for scoping the uv.lock bump.

    Empty when there is no readable ``[project] name`` (a non-Python repo has
    no uv.lock in its plan either, so the bump no-ops rather than guessing).

    PEP 503-normalized (lowercase, runs of ``-_.`` collapsed to a single ``-``):
    uv.lock always stores the normalized form, so a raw pyproject name like
    ``Acme_Api`` would build a regex in ``_bump_uv_lock`` that never matches
    the lockfile's ``acme-api`` entry, silently no-opping the bump.
    """
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    name = data.get("project", {}).get("name")
    if not isinstance(name, str):
        return ""
    return re.sub(r"[-_.]+", "-", name).lower()


def _bump_uv_lock(text: str, old: str, new: str, package: str) -> str:
    """Bump only ``package``'s own version block inside uv.lock, leaving a
    dependency that happens to share the version string untouched.

    ``package`` used to be the hardcoded literal ``roboco``, which silently
    no-opped the lockfile bump for every other uv project. An unresolvable
    name still no-ops, deliberately: a wrong lockfile edit is worse than none.
    """
    if not package:
        return text
    pattern = re.compile(
        r'(\[\[package\]\]\nname = "'
        + re.escape(package)
        + r'"\nversion = )"'
        + re.escape(old)
        + '"'
    )
    return pattern.sub(rf'\1"{new}"', text)


def _insert_changelog_entry(existing: str, entry: str) -> str:
    """Insert ``entry`` above the first released version heading (Keep a
    Changelog), emptying ``[Unreleased]``'s body — the readiness drafter uses
    that body as the entry when it's curated, so leaving it in place would
    ship the same content twice."""
    block = entry.rstrip() + "\n"
    if not existing.strip():
        return block
    lines = existing.splitlines(keepends=True)
    unreleased_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("## [") and "Unreleased" in line:
            unreleased_idx = idx
        elif line.startswith("## ["):
            if unreleased_idx is None:
                return "".join(lines[:idx]) + block + "\n" + "".join(lines[idx:])
            prefix = "".join(lines[: unreleased_idx + 1]).rstrip("\n") + "\n\n"
            return prefix + block + "\n" + "".join(lines[idx:])
    if unreleased_idx is not None:
        prefix = "".join(lines[: unreleased_idx + 1]).rstrip("\n") + "\n\n"
        return prefix + block
    return existing.rstrip() + "\n\n" + block


def _resolve_release_ci_workflow() -> str:
    """The release CI gate's workflow, decoupled from self_heal_ci_workflow.

    ``self_heal_ci_workflow`` documents an empty-string mode for single-workflow
    repos; inheriting that here would degrade the release fail-closed gate to
    the all-workflows mode ``_fetch_latest_ci_run`` itself flags as unreliable
    (a green secondary workflow masking a red primary CI). The release gate
    always uses a NAMED workflow — empty falls back to ``ci.yml``, never None.
    """
    from roboco.config import settings

    return settings.release_ci_workflow or "ci.yml"


async def get_release_executor(session: AsyncSession) -> ReleaseExecutor:
    """Build a ReleaseExecutor with a production ops over a fresh writable clone."""
    from roboco.config import settings
    from roboco.models.env_branches import prod_branch, promotion_chain
    from roboco.services.project import get_project_service

    slug = (settings.self_heal_project_slug or "roboco-api").strip()
    project_svc = get_project_service(session)
    project = await project_svc.get_by_slug(slug)
    if project is None:
        raise RuntimeError(f"release executor: project {slug!r} not found")
    token = await project_svc.get_decrypted_token_by_slug(slug)
    git_url = str(project.git_url)
    # Release target = prod rung (W-H decouple): releases land on prod regardless
    # of the dev retarget. default_branch stays as the legacy/shim source.
    default_branch = prod_branch(project)
    # Full-chain promotion: rung branches head→…→just-below-prod to merge into
    # the prod checkout before bumping. Degenerate (head==prod) → [] → no-op.
    env_chain = promotion_chain(project)
    relay = relay_for(project, token)
    root = await _prepare_release_clone(relay, default_branch)
    ctx = _ReleaseContext(
        slug=slug,
        prod_branch=default_branch,
        root=root,
        git_url=git_url,
        relay=relay,
        ci_workflow=_resolve_release_ci_workflow(),
        env_chain=env_chain,
    )
    return ReleaseExecutor(_GitReleaseOps(session, ctx))


async def _prepare_release_clone(relay: GitRelay, default_branch: str) -> Path:
    """Fresh, writable clone of the relay's ``default_branch`` for the release.

    Dispatcher-owned (under the private root, never mounted into an agent)
    and checked out with ``core.symlinks=false``: the release step rewrites
    CHANGELOG / version files as root, so a symlink committed to the repo
    must stay a plain file here, never a path out of the clone.
    """
    root = release_clone_path(relay.project_slug)
    if root.exists():
        await _run(["rm", "-rf", str(root)])
    root.parent.mkdir(parents=True, exist_ok=True)

    def _clone() -> subprocess.CompletedProcess[str]:
        fetched = relay.fetch_from_origin(
            [f"+refs/heads/{default_branch}:refs/heads/{default_branch}"],
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
        if fetched.returncode != 0:
            return fetched
        return run_private_git(
            root.parent,
            [
                "clone",
                "--quiet",
                "-c",
                "core.symlinks=false",
                "--branch",
                default_branch,
                str(relay.path),
                str(root),
            ],
            timeout=_CLONE_TIMEOUT_SECONDS,
            check=False,
        )

    cloned = await asyncio.to_thread(_clone)
    if cloned.returncode != 0:
        raise RuntimeError(f"release clone failed: {cloned.stderr.strip()[:200]}")
    return root


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    return await _await_proc(proc, _CLONE_TIMEOUT_SECONDS)
