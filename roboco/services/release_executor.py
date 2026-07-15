"""Release executor — prepares, gates, and publishes a release post-approval.

Runs ONLY after the CEO approves a proposal. It is fail-closed: a red
``make quality`` aborts before any commit, and a red release-commit CI aborts
before any publish. Idempotent: re-running an already-published version is a
no-op. The bump/gate/commit/publish steps live behind a ``ReleaseOps`` seam so
the fail-closed ORDERING (the correctness this feature exists to guarantee) is
unit-tested deterministically; the production ``_GitReleaseOps`` performs the
real git / ``make quality`` work on a writable clone and publishes the GitHub
release over REST (the orchestrator image ships no ``gh`` binary).
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

    async def release_commit_sha(self, version: str) -> str | None: ...

    async def apply_version_bumps(
        self, plan: list[str], new_version: str
    ) -> list[str]: ...

    async def write_changelog_entry(self, entry: str) -> None: ...

    async def promote_env_chain(self) -> None: ...

    async def run_gate(self) -> bool: ...

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
        fail-closed abort (promotion conflict / red gate / commit-push failure)
        so ``execute`` surfaces it to the CEO as a structured outcome, not a 500.
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
    git_url: str
    # Per-call ``-c http.extraheader=Authorization: Basic …`` prefix so the PAT
    # never lands in the clone/push argv (``/proc/<pid>/cmdline`` would expose
    # a URL-embedded token). Empty for SSH / tokenless repos.
    git_prefix: list[str]
    ci_workflow: str | None
    # Env-ladder rung branches to merge into the prod checkout head→…→just-
    # below-prod before the bump (full-chain promotion). Empty for a
    # degenerate (head==prod) ladder → promote_env_chain is a no-op.
    env_chain: list[str]


class _GitReleaseOps:
    """Real release steps on a writable clone of RoboCo (token-authenticated)."""

    def __init__(self, session: AsyncSession, ctx: _ReleaseContext) -> None:
        self._session = session
        self._slug = ctx.slug
        self._default_branch = ctx.default_branch
        self._root = ctx.root
        self._git_url = ctx.git_url
        self._git_prefix = ctx.git_prefix
        self._ci_workflow = ctx.ci_workflow
        self._env_chain = ctx.env_chain

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
        rc, out = await self._git(
            *self._git_prefix, "ls-remote", "--tags", "origin", f"v{version}"
        )
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
        wanted = f"chore(release): {version}"
        for line in out.splitlines():
            sha, _, subject = line.partition("\x00")
            if sha and subject.strip() == wanted:
                return sha.strip()
        return None

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

    async def promote_env_chain(self) -> None:
        """Merge the env ladder head→…→just-below-prod into the prod checkout.

        Brings the dev trunk's unreleased work down to prod before the version
        bump, so the release commits + tags the promoted state (generalizes the
        slave→master promotion to the full declared chain). ``--no-edit``,
        fail-closed on a fetch or merge conflict — a divergent rung aborts the
        release before any bump rather than landing a half-promoted prod. No-op
        for a degenerate (head==prod) ladder (empty chain).
        """
        if not self._env_chain:
            return
        # Fresh ``--branch <prod>`` clone only has prod's history — fetch every
        # rung branch so ``origin/<branch>`` resolves for the merges.
        rc, out = await self._git(*self._git_prefix, "fetch", "origin")
        if rc != 0:
            raise RuntimeError(f"env-chain fetch failed: {out.strip()[:200]}")
        for branch in self._env_chain:
            rc, out = await self._git("merge", "--no-edit", f"origin/{branch}")
            if rc != 0:
                raise RuntimeError(
                    f"env-chain merge {branch}→prod failed: {out.strip()[:200]}"
                )

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
            *self._git_prefix, "push", self._git_url, f"HEAD:{self._default_branch}"
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
                self._slug, workflow=self._ci_workflow, head_sha=commit_sha
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

        from roboco.config import settings
        from roboco.services.git import GitService
        from roboco.services.project import ProjectService

        tag = f"v{version}"
        token = await ProjectService(self._session).get_decrypted_token_by_slug(
            self._slug
        )
        if not token:
            raise RuntimeError(f"release publish failed: no git token for {self._slug}")
        owner, repo = GitService._parse_git_url(self._git_url)
        api_base = settings.github_api_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_PUBLISH_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{api_base}/repos/{owner}/{repo}/releases",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={
                        "tag_name": tag,
                        "name": tag,
                        "body": notes,
                        "target_commitish": self._default_branch,
                    },
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
    # PAT rides a per-call ``-c http.extraheader=Authorization: Basic …`` config
    # (mirrors workspace.py :642 / :1285) so it never lands in the clone/push
    # argv — ``/proc/<pid>/cmdline`` would otherwise expose a URL-embedded token.
    git_prefix: list[str] = []
    using_token = bool(token) and git_url.startswith("https://")
    if using_token:
        import base64

        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        git_prefix = ["-c", f"http.extraheader=Authorization: Basic {basic}"]
    root = await _prepare_release_clone(slug, git_url, git_prefix, default_branch)
    ctx = _ReleaseContext(
        slug=slug,
        default_branch=default_branch,
        root=root,
        git_url=git_url,
        git_prefix=git_prefix,
        ci_workflow=_resolve_release_ci_workflow(),
        env_chain=env_chain,
    )
    return ReleaseExecutor(_GitReleaseOps(session, ctx))


async def _prepare_release_clone(
    slug: str, git_url: str, git_prefix: list[str], default_branch: str
) -> Path:
    """Fresh, writable, token-authenticated clone for the release commit + push."""
    from roboco.config import settings

    root = Path(settings.workspaces_root) / "_release" / slug
    if root.exists():
        await _run(["rm", "-rf", str(root)])
    root.parent.mkdir(parents=True, exist_ok=True)
    rc, out = await _run(
        ["git", *git_prefix, "clone", "--branch", default_branch, git_url, str(root)]
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
