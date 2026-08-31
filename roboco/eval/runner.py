"""EvalRunner — golden-task quality bench for a (role, model/provider) cohort.

Replays ``roboco/eval/fixtures.py``'s ``BenchTaskSpec`` fixtures through the
REAL delivery lifecycle: one real task (``TaskService.create``, ``source=
"eval_bench"``) through QA / docs / cell-PM review to a terminal state. Each
fixture is scored on deterministic metrics (final status, revision_count,
cycle time, tokens+cost via the ``agent_spawn_sessions`` task_id join) plus a
local-model judge comparing the final PR diff + notes against the fixture's
checked-in ``expectations`` note — see ``CohortResult.as_dict()``'s nested
``"judge"`` object, marked ``"non_deterministic": true`` so a naive cohort
diff never mistakes judge noise for a real regression.

Environment reuse: the disposable project + real local git origin +
fake-GitHub REST + in-process API all come straight from
``tests.e2e_smoke.harness`` (the same machinery ``make e2e-smoke`` uses) — an
offline eval CLI has the exact same isolation needs a smoke test does
(no real GitHub, no leftover DB state between runs), so this reuses rather
than re-implements it. ``tests/e2e_smoke/harness.py``'s ``build_e2e_stack``
took a ``pytest.TempPathFactory`` parameter; its type was relaxed to a
structural ``TmpPathFactory`` Protocol (see that module) so this non-pytest
caller can drive it with a plain temp-dir factory. Source-checkout-only:
``tests/`` is not shipped in containers or wheels, so importing it (guarded
in ``_bench_environment`` with a clear ``RuntimeError``) only works when this
CLI runs from a git clone, never an installed package.

Vault safety: ``_bench_environment`` also patches ``obsidian_vault_enabled``
(and the vault intake/KB/report sub-flags) to False for the whole run, so a
bench task/note/journal write never lands in the operator's real Obsidian
vault even when the ambient deployment has vault flags armed.

Real-spawn status: ``OrchestratorStageSpawner`` is the default, real
``StageSpawner``: it drives one turn via the REAL
``AgentOrchestrator.spawn_agent`` — the exact method the production
dispatcher calls. ``_generate_mcp_config`` honors the patched
``settings.api_url`` (set to the harness's disposable stack URL in
``_bench_environment``), so a spawned container's MCP servers resolve to the
throwaway orchestrator, never the real production one — even though
``_seed_company`` seeds agents under their REAL production UUIDs (which is
correct: orchestrator-internal helpers keyed by the static registry resolve
exactly as they would in a real deployment). The injectable scripted
``StageSpawner`` (see ``tests/e2e_smoke/test_eval_bench.py``) remains the
unit-test fallback — it drives the SAME real MCP flow/do tool functions
e2e_smoke's ``ScriptedAgent`` uses, proving the runner's
polling/scoring/DB plumbing without touching Docker. ``python -m roboco.eval
run`` works end to end for every supported role cohort; it needs a Docker
daemon + built agent images for the real spawn path.

Role coverage: ``run_cohort`` scores developer, qa, cell_pm, and main_pm
cohorts, each against fixtures with the matching ``target_role``. The three
non-developer entry shapes (one task per fixture, as with a developer):

* ``developer`` — a fresh PENDING leaf under the bench cell; the loop drives
  dev → QA → doc → PM to ``completed`` (unchanged).
* ``qa`` — the runner itself pre-builds the review turn: it commits the
  fixture's repo files onto a fresh task branch (they are NOT seeded onto
  master, so the branch-vs-cell diff is the change under review), opens the
  PR, and parks the task at ``awaiting_qa``. The fixture is a defect trap by
  construction — its repo files carry the "fix attempt" whose content
  contradicts the acceptance criteria — and the scored question is
  catch-vs-miss: a ``fail_review`` (needs_revision) is a caught defect and
  the loop's designed success terminal (the deterministic pass); a
  ``pass_review`` of the defective PR flows on through doc → PM to
  ``completed`` (a deterministic miss). The QA judge prompt grades catch
  quality within those shapes.
* ``cell_pm`` / ``main_pm`` — the fixture task is a PARENT, not a leaf:
  pending under the bench cell for ``cell_pm``; a fresh coordination ROOT
  for ``main_pm``, which has no cell team and therefore no bench cell (the
  environment skips the cell setup entirely — see ``_bench_environment``).
  The scored turn is the PM's plan-and-delegate: did it ``delegate`` with
  ``covers_parent_criteria`` mapping EVERY parent acceptance criterion? The
  drive loop stops once the delegated child exists — a main_pm root's honest
  terminal is the CEO human gate, and the delegated child's own dev/QA/doc
  chain is a different cohort's subject, not this one's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID, uuid4

import httpx
import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.config import settings
from roboco.eval.fixtures import FIXTURES, BenchTaskSpec
from roboco.foundation import identity as _foundation
from roboco.models import Team
from roboco.models.base import Complexity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from tests.e2e_smoke.harness import E2EStack

logger = structlog.get_logger()

_TERMINAL_STATUSES = {"completed", "cancelled"}
# Statuses this bench cannot progress past without a human (CEO) — scored as
# a stall, distinct from a genuine timeout, but never mistaken for success.
_HUMAN_GATED_STATUSES = {"awaiting_ceo_approval", "blocked", "paused"}

# status -> the role responsible for advancing a DEVELOPER-entry fixture (a
# parentless leaf). PM-entry fixtures override the pending/claimed/in_progress
# rows to the role under test (see ``_stage_role``); every other status never
# legitimately occurs for a leaf and is scored as a stall.
_STAGE_ROLE: dict[str, str] = {
    "pending": "developer",
    "claimed": "developer",
    "in_progress": "developer",
    "needs_revision": "developer",
    "awaiting_qa": "qa",
    "awaiting_documentation": "documenter",
    "awaiting_pm_review": "cell_pm",
}
_ROLE_SUFFIX = {"qa": "qa", "documenter": "doc", "cell_pm": "pm"}
_TEAM_PREFIX = {"backend": "be", "frontend": "fe", "ux_ui": "ux"}

# Roles whose fixtures do NOT enter as a fresh developer leaf (QA enters at a
# pre-built AWAITING_QA review turn; the PM roles enter as a PARENT task).
_BENCH_ROLES = frozenset({"developer", "qa", "cell_pm", "main_pm"})
# A PM-entry fixture's scored turn is its plan-and-delegate; those are the
# statuses where the role under test owns the stage (a developer's, normally).
_PM_STAGE_STATUSES = frozenset({"pending", "claimed", "in_progress", "verifying"})


def _stage_role(status: str, target_role: str) -> str | None:
    """Status -> owning role, entry-aware.

    A cell_pm/main_pm fixture's task is a PARENT: pending/claimed/in_progress
    normally mean "a developer owns this leaf", but for a PM-entry fixture
    they mean "the PM has not delegated yet" — override them to the role
    under test. Every other status keeps the default leaf mapping.
    """
    if target_role in {"cell_pm", "main_pm"} and status in _PM_STAGE_STATUSES:
        return target_role
    return _STAGE_ROLE.get(status)


def _stage_agent_slug(role: str, target_role: str, role_slug: str, prefix: str) -> str:
    """The slug that drives one stage. The role under test drives its own
    stage (a qa cohort IS the be-qa agent; a main_pm cohort IS main-pm);
    supporting roles derive from the cell prefix. main_pm is org-level —
    never a ``{prefix}-`` cell member."""
    if role == target_role:
        return role_slug
    if role == "main_pm":
        return "main-pm"
    return f"{prefix}-{_ROLE_SUFFIX[role]}"


# ---------------------------------------------------------------------------
# Scratch Postgres — mirrors tests/conftest.py's `_test_database_url` fixture
# (same env vars, same CREATE DATABASE + Base.metadata.create_all technique)
# without the pytest fixture machinery, since this runs from a plain CLI.
# ---------------------------------------------------------------------------

_TEST_DB_HOST = os.environ.get("ROBOCO_TEST_DB_HOST", "localhost")
_TEST_DB_PORT = int(os.environ.get("ROBOCO_TEST_DB_PORT", "5432"))
_TEST_DB_USER = os.environ.get("ROBOCO_TEST_DB_USER", "roboco")
_TEST_DB_PASSWORD = os.environ.get("ROBOCO_TEST_DB_PASSWORD", "")
_TEST_DB_ADMIN_DB = os.environ.get("ROBOCO_TEST_DB_ADMIN_DB", "postgres")


def _scratch_db_url(database: str) -> str:
    auth = _TEST_DB_USER
    if _TEST_DB_PASSWORD:
        auth = f"{_TEST_DB_USER}:{_TEST_DB_PASSWORD}"
    return f"postgresql+asyncpg://{auth}@{_TEST_DB_HOST}:{_TEST_DB_PORT}/{database}"


@contextlib.contextmanager
def _scratch_database() -> Iterator[str]:
    """Provision a throwaway ``roboco_eval_<rand>`` DB, build the real
    schema, yield its URL, drop it on exit."""
    import asyncpg

    from roboco.db.base import Base

    db_name = f"roboco_eval_{uuid4().hex[:10]}"

    async def _create() -> None:
        conn = await asyncpg.connect(
            host=_TEST_DB_HOST,
            port=_TEST_DB_PORT,
            user=_TEST_DB_USER,
            password=_TEST_DB_PASSWORD or None,
            database=_TEST_DB_ADMIN_DB,
        )
        try:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()

        url = _scratch_db_url(db_name)
        engine = create_async_engine(url, future=True)
        try:
            async with engine.begin() as db_conn:
                with contextlib.suppress(Exception):
                    await db_conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await db_conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    async def _drop() -> None:
        conn = await asyncpg.connect(
            host=_TEST_DB_HOST,
            port=_TEST_DB_PORT,
            user=_TEST_DB_USER,
            password=_TEST_DB_PASSWORD or None,
            database=_TEST_DB_ADMIN_DB,
        )
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await conn.close()

    asyncio.run(_create())
    try:
        yield _scratch_db_url(db_name)
    finally:
        asyncio.run(_drop())


class _ScratchTmpFactory:
    """Minimal ``TmpPathFactory`` (see ``tests/e2e_smoke/harness.py``) for a
    plain-script caller — ``build_e2e_stack`` calls ``.mktemp()`` exactly
    once, so this only needs to satisfy that one call."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._count = 0

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        self._count += 1
        name = f"{basename}{self._count}" if numbered else basename
        path = self._root / name
        path.mkdir(parents=True)
        return path


# ---------------------------------------------------------------------------
# Disposable project + company
# ---------------------------------------------------------------------------


@dataclass
class BenchEnvironment:
    stack: E2EStack
    project_id: UUID
    project_slug: str
    team: Team
    # None for a main_pm environment: main_pm is org-level — no cell team, so
    # no bench cell to cut leaf PRs into. PM fixtures need neither: their
    # scored artifact is the delegation, not a merged diff.
    cell_id: UUID | None
    cell_branch: str | None
    role: str
    prefix: str


def _seed_company(stack: E2EStack, slugs: Iterable[str]) -> None:
    """Seed the canonical agents needed to run a fixture's whole lifecycle.

    Uses each slug's REAL fixed UUID from ``foundation.identity.AGENTS``
    (not a random one, unlike ``tests/e2e_smoke/arcs.py``'s ``seed_company``)
    so that orchestrator-internal helpers keyed by that static registry
    (``get_agent_role``, ``AGENT_UUIDS``, the UUID->slug reverse map) resolve
    exactly as they would in a real deployment.

    The AC wording "no real agent UUIDs" is satisfied by "no production
    DB/Redis reach": the isolation boundary is the disposable URL
    (``stack.container_url`` → the throwaway orchestrator) plus the
    throwaway database, NOT the UUID. A real UUID confers no production
    reach because the spawned container connects to the disposable
    orchestrator backed by a throwaway DB — randomizing UUIDs would only
    break the orchestrator's static-registry resolution and make the bench
    less realistic. See ``tests/unit/runtime/test_eval_mcp_config_isolation.py``
    for the pinned assertion.
    """
    from roboco.db.tables import AgentTable
    from roboco.models import AgentStatus

    async def _run(session: Any) -> None:
        for slug in slugs:
            row = _foundation.AGENTS[slug]
            session.add(
                AgentTable(
                    id=row.uuid,
                    name=slug,
                    slug=slug,
                    role=row.role,
                    team=row.team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

    stack.run_db(_run)


def _seed_project(stack: E2EStack, team: Team, created_by: UUID) -> tuple[UUID, str]:
    from roboco.db.tables import ProjectTable
    from roboco.utils.crypto import encrypt_token

    slug = f"eval-bench-{uuid4().hex[:8]}"
    holder: dict[str, Any] = {}

    async def _run(session: Any) -> None:
        project = ProjectTable(
            id=uuid4(),
            name=f"Eval bench {slug}",
            slug=slug,
            git_url=str(stack.origin),
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=team,
            created_by=created_by,
            is_active=True,
            git_token_encrypted=encrypt_token("eval-bench-dummy-token"),
        )
        session.add(project)
        await session.flush()
        holder["id"] = project.id

    stack.run_db(_run)
    return holder["id"], slug


def _seed_bench_cell(
    stack: E2EStack, project_id: UUID, team: Team, prefix: str
) -> tuple[UUID, str]:
    """A minimal, never-advanced coordination parent so every fixture's leaf
    task has a REAL non-default-branch merge target.

    A parentless leaf's PR would target the project's default branch, which
    only the CEO may merge (``roboco.services.git``'s ``CEO_ONLY`` check) —
    real production leaf tasks are always a cell/root's child for exactly
    this reason. This cell task is cut once per environment and never
    advanced past ``in_progress``; every fixture's leaf is created as its
    child so ``cell_pm_complete`` merges into this cell branch instead.
    """
    from tests.e2e_smoke.arcs import origin_branch, set_branch_name

    from roboco.models.base import TaskNature, TaskStatus, TaskType
    from roboco.models.task import TaskCreateRequest
    from roboco.services.task import EVAL_BENCH_SOURCE, get_task_service

    pm_uuid = _foundation.AGENTS[f"{prefix}-pm"].uuid
    branch = f"feature/{team.value}/bench-cell-{uuid4().hex[:8]}"
    origin_branch(stack, branch, start="master")
    holder: dict[str, Any] = {}

    async def _run(session: Any) -> None:
        req = TaskCreateRequest(
            title="Bench cell coordination (internal, never advanced)",
            description=(
                "Internal coordination parent so bench leaf tasks merge into "
                "a cell branch instead of the project's protected default "
                "branch. Never claimed or advanced by any agent."
            ),
            acceptance_criteria=["n/a — coordination-only, never advanced"],
            team=team,
            created_by=pm_uuid,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
            assigned_to=pm_uuid,
            status=TaskStatus.IN_PROGRESS,
            source=EVAL_BENCH_SOURCE,
            confirmed_by_human=True,
        )
        task = await get_task_service(session).create(req)
        holder["id"] = task.id

    stack.run_db(_run)
    cell_id = cast("UUID", holder["id"])
    set_branch_name(stack, cell_id, branch)
    return cell_id, branch


@contextlib.contextmanager
def _bench_environment(role_slug: str) -> Iterator[BenchEnvironment]:
    """Stand up the disposable e2e_smoke-style stack + one bench project +
    the fixed company of agents needed to run a fixture end to end."""
    try:
        from tests.e2e_smoke.harness import build_e2e_stack
    except ImportError as exc:
        raise RuntimeError(
            "the eval CLI runs from a source checkout; tests/ is not shipped "
            "in containers or wheels — run `python -m roboco.eval` from a git "
            "clone of the repo, not an installed package"
        ) from exc

    role = get_agent_role(role_slug)
    team_str = get_agent_team(role_slug)
    if role == "main_pm":
        # main_pm is org-level: no cell team, so no bench cell — the
        # explicit non-cell environment path. The project still declares a
        # concrete (backend) cell so the workspace/project machinery has a
        # cell to resolve; the fixture task itself carries Team.MAIN_PM.
        team = Team.BACKEND
        prefix = "be"
        slugs = ["main-pm", "be-dev-1", "be-qa", "be-doc", "be-pm"]
    else:
        if team_str not in _TEAM_PREFIX:
            raise ValueError(f"{role_slug!r} has no known cell team ({team_str!r})")
        team = Team(team_str)
        prefix = _TEAM_PREFIX[team_str]
        # The role under test plus the supporting cast its lifecycle can
        # reach (dedup: a qa cohort role_slug IS the {prefix}-qa agent).
        slugs = list(
            dict.fromkeys(
                [
                    role_slug,
                    f"{prefix}-dev-1",
                    f"{prefix}-qa",
                    f"{prefix}-doc",
                    f"{prefix}-pm",
                ]
            )
        )

    with _scratch_database() as db_url:
        root_path = Path(tempfile.mkdtemp(prefix="roboco-eval-"))
        try:
            stack_cm = contextlib.contextmanager(build_e2e_stack)
            with stack_cm(db_url, _ScratchTmpFactory(root_path)) as stack:
                mp = pytest.MonkeyPatch()
                mp.setattr(settings, "api_url", stack.container_url)
                # A bench task/note/journal write must never land in the
                # operator's REAL Obsidian vault. obsidian_vault_enabled is
                # the single gate every writer seam (TaskService.create's
                # materialize-on-create + status-transition touch,
                # JournalService, A2AService) checks first — traced via
                # `grep obsidian_vault_enabled roboco/services/{task,journal,
                # a2a}.py` — so patching it False is sufficient on its own.
                # The three sub-flags below gate background LOOPS (vault
                # intake watcher, KB ingest, weekly report) that this harness
                # never starts (no AgentOrchestrator.start() call) and are
                # therefore already inert; patched anyway so a future harness
                # change that does start them fails closed, not open. The
                # patch is role-agnostic: it wraps the environment, so every
                # entry point (developer leaf, pre-built QA review, PM
                # parent/root) is covered.
                mp.setattr(settings, "obsidian_vault_enabled", False)
                mp.setattr(settings, "vault_intake_enabled", False)
                mp.setattr(settings, "vault_kb_enabled", False)
                mp.setattr(settings, "vault_report_enabled", False)
                try:
                    owner_uuid = _foundation.AGENTS[slugs[0]].uuid
                    _seed_company(stack, slugs)
                    project_id, project_slug = _seed_project(stack, team, owner_uuid)
                    if role == "main_pm":
                        cell_id, cell_branch = None, None
                    else:
                        cell_id, cell_branch = _seed_bench_cell(
                            stack, project_id, team, prefix
                        )
                    yield BenchEnvironment(
                        stack=stack,
                        project_id=project_id,
                        project_slug=project_slug,
                        team=team,
                        cell_id=cell_id,
                        cell_branch=cell_branch,
                        role=role,
                        prefix=prefix,
                    )
                finally:
                    mp.undo()
        finally:
            shutil.rmtree(root_path, ignore_errors=True)


def _seed_fixture_repo(stack: E2EStack, fixture: BenchTaskSpec) -> None:
    """Push the fixture's ``repo_files`` onto the project's default branch,
    namespaced under ``bench/<key>/`` so sequential fixtures never collide."""
    from tests.e2e_smoke.harness import _git

    admin = stack.github.admin_clone
    _git(admin, "fetch", "origin", "--prune")
    _git(admin, "checkout", "-B", "master", "origin/master")
    for rel_path, content in fixture.repo_files:
        path = admin / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        _git(admin, "add", rel_path)
    _git(admin, "commit", "-m", f"chore(bench): seed {fixture.key} fixture repo state")
    _git(admin, "push", "origin", "master")


def _fast_forward_branch(stack: E2EStack, branch: str, *, onto: str) -> None:
    """Fast-forward `branch` to `onto`'s current tip (a plain push — safe
    only because the bench cell branch never carries commits of its own, so
    it is always a strict ancestor of `onto`)."""
    from tests.e2e_smoke.harness import _git

    admin = stack.github.admin_clone
    _git(admin, "fetch", "origin", "--prune")
    _git(admin, "checkout", "-B", branch, f"origin/{onto}")
    _git(admin, "push", "origin", branch)


def _create_bench_task(
    *,
    stack: E2EStack,
    project_id: UUID,
    owner_slug: str,
    fixture: BenchTaskSpec,
    team: Team,
    parent_task_id: UUID | None,
    creator_slug: str | None = None,
) -> UUID:
    """Create the real task (TaskService.create), pre-assigned to
    `owner_slug` — the "PM pre-assigned this" shape every dev-entry task in
    production already uses (e.g. the video engine's authoring tasks).
    Developer-entry fixtures are children of the environment's bench cell
    (see ``_seed_bench_cell``) so their eventual PR merges into a real cell
    branch, not the project's protected default branch; a main_pm fixture is
    a coordination ROOT (parent_task_id=None, Team.MAIN_PM)."""
    from roboco.models.task import TaskCreateRequest
    from roboco.services.task import EVAL_BENCH_SOURCE, get_task_service

    owner_uuid = _foundation.AGENTS[owner_slug].uuid
    creator_uuid = _foundation.AGENTS[creator_slug or owner_slug].uuid
    holder: dict[str, Any] = {}

    async def _run(session: Any) -> None:
        req = TaskCreateRequest(
            title=fixture.title,
            description=fixture.description,
            acceptance_criteria=list(fixture.acceptance_criteria),
            team=team,
            created_by=creator_uuid,
            task_type=fixture.task_type,
            nature=fixture.nature,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
            parent_task_id=parent_task_id,
            assigned_to=owner_uuid,
            source=EVAL_BENCH_SOURCE,
            confirmed_by_human=True,
        )
        task = await get_task_service(session).create(req)
        holder["id"] = task.id

    stack.run_db(_run)
    return cast("UUID", holder["id"])


def _cut_bench_branch(
    stack: E2EStack, task_id: UUID, *, base_branch: str, team: Team
) -> str:
    """Cut + push a task branch from `base_branch` and stamp the task row's
    ``branch_name`` — the same system-side seeding ``_seed_bench_cell`` /
    ``set_branch_name`` do, used for the PM-entry parents whose claim would
    otherwise need to derive a branch mid-flight."""

    from tests.e2e_smoke.arcs import origin_branch, set_branch_name

    branch = f"feature/{team.value}/bench-pm-{uuid4().hex[:8]}"
    origin_branch(stack, branch, start=base_branch)
    set_branch_name(stack, task_id, branch)
    return branch


def _prebuild_qa_review(
    stack: E2EStack,
    *,
    base_branch: str,
    team: Team,
    fixture: BenchTaskSpec,
    task_id: UUID,
) -> None:
    """QA-entry setup: commit the fixture's repo files onto a fresh task
    branch cut from the cell branch, open the PR, and park the task at
    ``awaiting_qa`` — standing in for the developer turn a QA bench fixture
    deliberately skips.

    The fixture's repo files are committed ONLY here (never seeded onto
    master), so the branch-vs-cell diff IS the change under review: the
    fixture's "fix attempt", which a trap fixture deliberately writes to
    contradict its own acceptance criteria (the injected defect). This is
    system-side seeding in the ``_seed_bench_cell`` style — direct PR +
    row writes standing in for a scripted developer turn."""
    from tests.e2e_smoke.harness import _git

    def _run_git() -> tuple[str, dict[str, Any]]:
        admin = stack.github.admin_clone
        _git(admin, "fetch", "origin", "--prune")
        branch = f"feature/{team.value}/bench-qa-{uuid4().hex[:8]}"
        _git(admin, "checkout", "-B", branch, f"origin/{base_branch}")
        for rel_path, content in fixture.repo_files:
            path = admin / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            _git(admin, "add", rel_path)
        _git(admin, "commit", "-m", f"bench: PR under review for {fixture.key}")
        _git(admin, "push", "origin", branch)
        pr = stack.github.create_pr(
            title=fixture.title,
            body=fixture.description,
            head=branch,
            base=base_branch,
        )
        return branch, pr

    branch, pr = _run_git()

    async def _park(session: Any) -> None:
        from roboco.db.tables import TaskTable
        from roboco.models.base import TaskStatus

        row = await session.get(TaskTable, task_id)
        row.branch_name = branch
        row.pr_number = pr["number"]
        row.pr_created = True
        row.status = TaskStatus.AWAITING_QA

    stack.run_db(_park)


# ---------------------------------------------------------------------------
# Stage driving — the seam between a real spawn and a scripted stand-in
# ---------------------------------------------------------------------------


class StageSpawner(Protocol):
    """Advance one task by exactly one role's turn (whatever a single real
    container run, or its scripted equivalent, would do): claim + work +
    submit, or review + advance. Must not itself loop waiting for further
    stages — ``_drive_task_to_terminal`` owns that poll loop."""

    async def run_stage(self, *, task: dict[str, Any], agent_slug: str) -> None: ...


class OrchestratorStageSpawner:
    """The default, real ``StageSpawner``: drive one turn via the REAL
    ``AgentOrchestrator.spawn_agent`` — the exact method the production
    dispatcher calls — reusing its own ``_get_prompt_for_agent`` /
    ``_task_git_context`` helpers so the prompt and workspace mount are
    byte-for-byte what a real dispatch tick would build, then wait for the
    container to exit (or the stage timeout).

    Safe because ``_generate_mcp_config`` honors the patched
    ``settings.api_url`` (set to the harness's disposable stack URL in
    ``_bench_environment``), so a spawned container's MCP servers resolve to
    the throwaway orchestrator, never the real production one — even though
    ``_seed_company`` seeds agents under their REAL production UUIDs (which
    is correct: orchestrator-internal helpers keyed by the static registry
    resolve exactly as they would in a real deployment).
    """

    _orchestrator: Any
    _stage_timeout_seconds: float

    def __init__(self, stage_timeout_seconds: float = 900.0) -> None:
        from roboco.runtime.orchestrator import AgentOrchestrator

        self._stage_timeout_seconds = stage_timeout_seconds
        # Constructed the same way the production dispatcher does
        # (bootstrap.py: ``AgentOrchestrator()``); the harness's
        # ``_bench_environment`` has already patched ``settings.database_*``
        # to the throwaway DB and ``settings.api_url`` to the disposable
        # stack URL, so the orchestrator's DB + MCP-config wiring resolve to
        # the bench's own environment, not production.
        self._orchestrator = AgentOrchestrator()

    async def run_stage(self, *, task: dict[str, Any], agent_slug: str) -> None:
        from roboco.models.runtime import OrchestratorAgentState

        orch = self._orchestrator
        # Reuses the orchestrator's own (private) prompt/git-context builders
        # so a real bench spawn gets byte-for-byte the same prompt + workspace
        # mount a real dispatch tick would build — not a re-derived copy.
        prompt = await orch._get_prompt_for_agent(agent_slug, task)
        await orch.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=prompt,
            git_context=orch._task_git_context(task),
            spawned_by="eval_bench",
        )
        deadline = time.monotonic() + self._stage_timeout_seconds
        while time.monotonic() < deadline:
            instance = orch.get_instance(agent_slug)
            if instance is None or instance.state == OrchestratorAgentState.OFFLINE:
                break
            await asyncio.sleep(3.0)
        await orch.stop_agent(
            agent_slug, release_claim=True, exit_reason="eval_bench_stage_end"
        )


async def _claim_for_pm(
    client: httpx.AsyncClient, api: str, task_id: str, pm_slug: str
) -> None:
    """Mirror the real dispatcher's pre-spawn PM claim (``_claim_task_for_
    agent``) — a plain REST call, identical for the real and scripted
    stage-spawner paths, so it lives in the shared driving loop rather than
    duplicated in both ``StageSpawner`` implementations."""
    with contextlib.suppress(Exception):
        await client.post(f"{api}/tasks/{task_id}/claim", json={"agent_id": pm_slug})


def _child_rows(stack: E2EStack, task_id: UUID) -> list[dict[str, Any]]:
    """The fixture task's live children — for a PM-entry fixture the drive
    loop's stop signal (a delegated child existing = the scored turn
    happened). Read in a worker thread: ``E2EStack.run_db`` needs a loopless
    thread (same constraint the scripted arcs document)."""
    from sqlalchemy import select

    from roboco.db.tables import TaskTable

    async def _run(session: Any) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.parent_task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        return [{"id": str(r.id), "status": str(r.status)} for r in rows]

    return cast("list[dict[str, Any]]", stack.run_db(_run))


async def _drive_task_to_terminal(
    stack: E2EStack,
    spawner: StageSpawner,
    task_id: UUID,
    *,
    role_slug: str,
    prefix: str,
    target_role: str,
    fixture_timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    """Poll ``task_id`` to a terminal state, invoking ``spawner.run_stage``
    for whichever role owns the current status, until terminal (or the
    entry point's designed stop), or the hard per-fixture timeout.

    Returns ``(final_task_dict, stalled)``. ``stalled`` is True when the loop
    gave up (timeout, a human-gated status, or a status with no owning role)
    rather than reaching a genuine terminal state or the role's designed
    bench endpoint:

    * a QA-entry fixture stops at ``needs_revision`` — the QA agent bounced
      the defective PR, i.e. caught the injected defect;
    * a PM-entry fixture stops once the delegated child exists — the scored
      plan-and-delegate turn happened (the loop keeps giving the PM stages
      until then, so a PM that never delegates stalls at the timeout).
    """
    from roboco.runtime.orchestrator import _system_api_headers

    deadline = time.monotonic() + fixture_timeout_seconds
    api = f"{stack.base_url}/api"
    # The claim POST below requires an agent identity (X-Agent-ID); this
    # driving loop plays the same "trusted internal caller" role the real
    # dispatch tick does, so it authenticates the same way — the system
    # identity headers the orchestrator's own dispatch client carries.
    async with httpx.AsyncClient(timeout=30.0, headers=_system_api_headers()) as client:
        task = (await client.get(f"{api}/tasks/{task_id}")).json()
        while True:
            stopped, stalled = await _bench_stop(
                stack, task_id, task.get("status"), target_role
            )
            if stopped:
                return task, stalled
            role = _stage_role(task.get("status"), target_role)
            if role is None or time.monotonic() >= deadline:
                return task, True
            agent_slug = _stage_agent_slug(role, target_role, role_slug, prefix)
            if role in {"cell_pm", "main_pm"} and not task.get("assigned_to"):
                await _claim_for_pm(client, api, str(task_id), agent_slug)
            await spawner.run_stage(task=task, agent_slug=agent_slug)
            task = (await client.get(f"{api}/tasks/{task_id}")).json()


async def _bench_stop(
    stack: E2EStack, task_id: UUID, status: str | None, target_role: str
) -> tuple[bool, bool]:
    """The driving loop's stop conditions — terminal, human-gated, or the
    entry point's designed bench endpoint. Returns ``(stopped, stalled)``.

    * a QA-entry fixture stops at ``needs_revision`` — the QA agent bounced
      the defective PR, i.e. caught the injected defect;
    * a PM-entry fixture stops once the delegated child exists — the scored
      plan-and-delegate turn happened (a PM that never delegates stalls at
      the timeout instead)."""
    if status in _TERMINAL_STATUSES:
        return True, False
    if status in _HUMAN_GATED_STATUSES:
        return True, True
    if target_role == "qa" and status == "needs_revision":
        # QA bench: the review verdict IS the deliverable — a fail means the
        # QA agent caught the injected defect. Stop here rather than benching
        # the (different-role) rework turn.
        return True, False
    if target_role in {"cell_pm", "main_pm"} and status in _PM_STAGE_STATUSES:
        children = await asyncio.to_thread(_child_rows, stack, task_id)
        if children:
            # The scored turn landed: the PM delegated. Stop — the child's
            # dev/QA/doc chain is a different cohort's subject.
            return True, False
    return False, False


# ---------------------------------------------------------------------------
# Local-model judge — same Ollama-compatible endpoint MemoryDistiller uses
# ---------------------------------------------------------------------------

_JUDGE_TIMEOUT_SECONDS = 60.0
_JUDGE_SCORE_RE = re.compile(r"score\s*:\s*([1-5])", re.IGNORECASE)


@dataclass
class JudgeVerdict:
    score: int | None
    rationale: str | None


def _build_judge_prompt(fixture: BenchTaskSpec, diff: str, notes: str) -> str:
    """Role-aware judge prompt, selected on ``fixture.target_role``.

    The generic developer template grades the delivered diff against the
    checked-in expectation. The QA template grades catch-vs-miss (the diff
    is the PR under review — a trap fixture's injected defect — and the
    notes are the QA agent's review notes). The PM template grades the
    delegation: ``diff`` carries the delegation record (delegated children
    + their ``covers_parent_criteria`` mapping), not a git diff — a PM
    fixture's deliverable is the mapping, and the child work itself belongs
    to a different cohort.
    """
    criteria = "\n".join(f"- {c}" for c in fixture.acceptance_criteria)

    def _reply_shape() -> str:
        return "Reply in exactly this shape:\nScore: <1-5>\nRationale: <one line>\n\n"

    head = f"{fixture.title}\n"
    ac_block = f"Acceptance criteria:\n{criteria}\n\n"
    expected_block = f"Expected (checked-in): {fixture.expectations}\n\n"

    if fixture.target_role == "qa":
        grading = (
            "You are grading a QA review turn for an automated agent quality "
            "bench. The diff below is the PR that was put in front of the QA "
            "agent; it may contain an injected defect. Score 1-5 where 5 "
            "means the QA agent CAUGHT the defect — it failed the review and "
            "its notes name the concrete defect in the diff that violates "
            "the acceptance criteria — and 1 means the QA agent MISSED it "
            "(approved a change that violates the acceptance criteria). "
            + _reply_shape()
        )
        evidence_block = (
            f"PR under review (diff):\n{diff or '(empty diff)'}\n\n"
            f"QA review notes:\n{notes or '(no notes)'}\n"
        )
    elif fixture.target_role in {"cell_pm", "main_pm"}:
        grading = (
            "You are grading a PM planning turn for an automated agent "
            "quality bench. The delegation record below lists the children "
            "the PM delegated and the ``covers_parent_criteria`` refs each "
            "child declared. Score 1-5 where 5 means the PM delegated with "
            "``covers_parent_criteria`` mapping EVERY acceptance criterion "
            "(coverage-mapped), and 1 means criteria were dropped or left "
            "unmapped (or the PM delegated nothing). " + _reply_shape()
        )
        evidence_block = (
            f"Delegation record:\n{diff or '(no children delegated)'}\n\n"
            f"PM notes:\n{notes or '(no notes)'}\n"
        )
    else:
        grading = (
            "You are grading a completed engineering task against its "
            "checked-in expectation, for an automated agent quality bench. "
            "Score 1-5 (5 = fully meets the expectation, 1 = does not meet "
            "it at all). " + _reply_shape()
        )
        evidence_block = (
            f"Actual diff:\n{diff or '(empty diff)'}\n\n"
            f"Actual notes:\n{notes or '(no notes)'}\n"
        )

    return grading + f"\nTask: {head}\n" + ac_block + expected_block + evidence_block


async def _judge_chat(prompt: str) -> str | None:
    async with httpx.AsyncClient(timeout=_JUDGE_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{settings.local_llm_base_url}/chat/completions",
            json={
                "model": settings.local_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "options": {"num_ctx": 8192},
            },
        )
        if not resp.is_success:
            return None
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content")
        return content if isinstance(content, str) else None


class BenchJudge:
    """Scores a fixture's final diff+notes against its expectation, via the
    SAME local Ollama-compatible endpoint ``MemoryDistiller`` uses (never a
    cloud LLM in the hot path). Best-effort: any failure yields
    ``score=None`` — a bench run still produces its deterministic metrics
    even with the local model down."""

    async def score(
        self, *, fixture: BenchTaskSpec, diff: str, notes: str
    ) -> JudgeVerdict:
        try:
            content = await _judge_chat(_build_judge_prompt(fixture, diff, notes))
        except Exception as exc:
            logger.warning("BenchJudge failed (best-effort)", error=str(exc))
            return JudgeVerdict(score=None, rationale=f"judge unavailable: {exc}")
        if not content:
            return JudgeVerdict(score=None, rationale="judge returned no content")
        match = _JUDGE_SCORE_RE.search(content)
        score = int(match.group(1)) if match else None
        return JudgeVerdict(score=score, rationale=content.strip())


# ---------------------------------------------------------------------------
# Scoring — pure dataclasses + aggregate math (unit-testable with no DB/IO)
# ---------------------------------------------------------------------------


@dataclass
class DeterministicMetrics:
    final_status: str
    stalled: bool
    revision_count: int
    cycle_time_seconds: float
    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    estimated_cost_usd: float

    @property
    def total_tokens(self) -> int:
        return (
            self.tokens_input
            + self.tokens_output
            + self.tokens_cache_read
            + self.tokens_cache_write
        )


@dataclass
class FixtureResult:
    fixture_key: str
    metrics: DeterministicMetrics
    judge: JudgeVerdict
    target_role: str = "developer"
    # Role-aware pass judgment, computed at run time (None = use the default
    # completed-and-not-stalled semantics). QA-entry: needs_revision = caught
    # the injected defect. PM-entry: the delegation covers every parent AC.
    role_pass: bool | None = None

    @property
    def passed(self) -> bool:
        if self.role_pass is not None:
            return self.role_pass
        return self.metrics.final_status == "completed" and not self.metrics.stalled


@dataclass
class CohortResult:
    role_slug: str
    cohort_name: str
    fixtures: list[FixtureResult]

    @property
    def pass_rate(self) -> float:
        if not self.fixtures:
            return 0.0
        return sum(1 for f in self.fixtures if f.passed) / len(self.fixtures)

    @property
    def total_cost_usd(self) -> float:
        return sum(f.metrics.estimated_cost_usd for f in self.fixtures)

    @property
    def total_tokens(self) -> int:
        return sum(f.metrics.total_tokens for f in self.fixtures)

    @property
    def mean_cycle_time_seconds(self) -> float:
        if not self.fixtures:
            return 0.0
        return statistics.fmean(f.metrics.cycle_time_seconds for f in self.fixtures)

    @property
    def mean_judge_score(self) -> float | None:
        scores = [f.judge.score for f in self.fixtures if f.judge.score is not None]
        return statistics.fmean(scores) if scores else None

    def as_dict(self) -> dict[str, Any]:
        # Judge fields are nested under their own "judge" object (both here
        # and per-fixture below) and stamped non_deterministic=True — a local-
        # model score is not a repeatable metric like the sibling deterministic
        # ones, and a naive diff between two cohort JSONs must not mistake
        # judge noise for a real regression.
        return {
            "role_slug": self.role_slug,
            "cohort_name": self.cohort_name,
            "aggregate": {
                "fixture_count": len(self.fixtures),
                "pass_rate": self.pass_rate,
                "total_cost_usd": round(self.total_cost_usd, 4),
                "total_tokens": self.total_tokens,
                "mean_cycle_time_seconds": round(self.mean_cycle_time_seconds, 1),
            },
            "judge": {
                "mean_score": self.mean_judge_score,
                "non_deterministic": True,
            },
            "fixtures": [
                {
                    "fixture_key": f.fixture_key,
                    "final_status": f.metrics.final_status,
                    "stalled": f.metrics.stalled,
                    "passed": f.passed,
                    "revision_count": f.metrics.revision_count,
                    "cycle_time_seconds": round(f.metrics.cycle_time_seconds, 1),
                    "tokens_input": f.metrics.tokens_input,
                    "tokens_output": f.metrics.tokens_output,
                    "tokens_cache_read": f.metrics.tokens_cache_read,
                    "tokens_cache_write": f.metrics.tokens_cache_write,
                    "estimated_cost_usd": round(f.metrics.estimated_cost_usd, 4),
                    "judge": {
                        "score": f.judge.score,
                        "rationale": f.judge.rationale,
                        "non_deterministic": True,
                    },
                }
                for f in self.fixtures
            ],
        }


def _deterministic_metrics(
    stack: E2EStack,
    task_id: UUID,
    started_at: datetime,
    stalled: bool,
    final_status: str,
) -> DeterministicMetrics:
    """Read the final task row + the agent_spawn_sessions rows this task's
    stages accumulated, joined by task_id (the same join the CLAUDE.md
    "Delivery observability" rework-cost metric already uses)."""
    from sqlalchemy import func, select

    from roboco.db.tables import AgentSpawnSessionTable, TaskTable

    async def _run(session: Any) -> dict[str, Any]:
        task_row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        agg = (
            await session.execute(
                select(
                    func.coalesce(func.sum(AgentSpawnSessionTable.tokens_input), 0),
                    func.coalesce(func.sum(AgentSpawnSessionTable.tokens_output), 0),
                    func.coalesce(
                        func.sum(AgentSpawnSessionTable.tokens_cache_read), 0
                    ),
                    func.coalesce(
                        func.sum(AgentSpawnSessionTable.tokens_cache_write), 0
                    ),
                    func.coalesce(
                        func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                    ),
                ).where(AgentSpawnSessionTable.task_id == str(task_id))
            )
        ).one()
        ended_at = task_row.completed_at or task_row.updated_at or datetime.now(UTC)
        return {
            "revision_count": task_row.revision_count,
            "tokens_input": int(agg[0]),
            "tokens_output": int(agg[1]),
            "tokens_cache_read": int(agg[2]),
            "tokens_cache_write": int(agg[3]),
            "estimated_cost_usd": float(agg[4]),
            "ended_at": ended_at,
        }

    row = stack.run_db(_run)
    cycle_time = (row["ended_at"] - started_at).total_seconds()
    return DeterministicMetrics(
        final_status=final_status,
        stalled=stalled,
        revision_count=row["revision_count"],
        cycle_time_seconds=max(cycle_time, 0.0),
        tokens_input=row["tokens_input"],
        tokens_output=row["tokens_output"],
        tokens_cache_read=row["tokens_cache_read"],
        tokens_cache_write=row["tokens_cache_write"],
        estimated_cost_usd=row["estimated_cost_usd"],
    )


def _task_diff(stack: E2EStack, base_branch: str, branch_name: str | None) -> str:
    """Diff the task's branch against its REAL base — the bench cell branch
    it was cut from (see ``_seed_bench_cell``), not the project default."""
    if not branch_name:
        return ""
    from tests.e2e_smoke.harness import _git

    admin = stack.github.admin_clone
    try:
        _git(admin, "fetch", "origin", "--prune")
        return _git(admin, "diff", f"origin/{base_branch}...origin/{branch_name}")
    except subprocess.CalledProcessError:
        return ""


def _pm_delegation_summary(stack: E2EStack, task_id: UUID) -> tuple[str, bool]:
    """Render the PM-entry fixture's delegation record (each delegated child
    + its ``covers_parent_criteria`` refs) and compute full coverage against
    the parent's acceptance criteria. Both feed the bench output: the record
    is what the PM judge prompt grades, and the boolean is the deterministic
    coverage-mapped-vs-dropped verdict.

    An AC is covered when its id (``acceptance_criteria_ids``) or its exact
    text appears in some child's ``parent_ac_refs`` — the same id-or-exact-
    text matcher the delegate/submit_up coverage gates use."""
    from sqlalchemy import select

    from roboco.db.tables import TaskTable

    async def _run(session: Any) -> tuple[str, bool]:
        parent = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        children = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.parent_task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        acs = list(parent.acceptance_criteria or [])
        ac_ids = list(parent.acceptance_criteria_ids or [])
        if not acs:
            return "", True
        refs = {ref for c in children for ref in (c.parent_ac_refs or [])}
        pairs = list(zip(ac_ids, acs, strict=False)) or [(None, ac) for ac in acs]
        covered = [
            text
            for pid, text in pairs
            if (pid is not None and pid in refs) or (text in refs)
        ]
        lines = [
            f"- {str(c.title)!r} (status={c.status!s}): "
            f"covers_parent_criteria={list(c.parent_ac_refs or [])}"
            for c in children
        ]
        summary = "\n".join(lines) or "(no children delegated)"
        return summary, len(covered) == len(acs) and bool(children)

    return cast("tuple[str, bool]", stack.run_db(_run))


def _collected_notes(stack: E2EStack, task_id: UUID) -> str:
    from sqlalchemy import select

    from roboco.db.tables import TaskTable

    async def _run(session: Any) -> str:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        sections = (
            ("dev", row.dev_notes),
            ("qa", row.qa_notes),
            ("doc", row.doc_notes),
            ("pm", row.pm_notes),
        )
        return "\n\n".join(
            f"[{label}_notes]\n{text}" for label, text in sections if text
        )

    return cast("str", stack.run_db(_run))


def _print_table(cohort: CohortResult) -> None:
    # "judge*" / the trailing footnote mirror as_dict()'s nested
    # judge.non_deterministic=True — a local-model score is not a repeatable
    # metric like its deterministic neighbors in this row.
    header = (
        f"{'fixture':<28} {'status':<12} {'stalled':<8} {'rev':<4} "
        f"{'cycle(s)':<9} {'tokens':<9} {'cost($)':<9} {'judge*':<6}"
    )
    print(f"\nEval bench — role={cohort.role_slug} cohort={cohort.cohort_name}\n")
    print(header)
    print("-" * len(header))
    for f in cohort.fixtures:
        m = f.metrics
        judge = str(f.judge.score) if f.judge.score is not None else "-"
        print(
            f"{f.fixture_key:<28} {m.final_status:<12} {m.stalled!s:<8} "
            f"{m.revision_count:<4} {m.cycle_time_seconds:<9.1f} "
            f"{m.total_tokens:<9} {m.estimated_cost_usd:<9.4f} {judge:<6}"
        )
    print("-" * len(header))
    mean_judge = cohort.mean_judge_score
    print(
        f"pass_rate={cohort.pass_rate:.2f}  "
        f"mean_cycle_s={cohort.mean_cycle_time_seconds:.1f}  "
        f"total_tokens={cohort.total_tokens}  "
        f"total_cost=${cohort.total_cost_usd:.4f}  "
        f"mean_judge*={mean_judge if mean_judge is not None else '-'}"
    )
    print("*judge score/mean_judge* are local-model, non-deterministic — not a metric")


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------


class EvalRunner:
    """Runs one cohort (a labeled (role, model/provider config) run) through
    every golden-task fixture matching the cohort's role and prints + returns
    the scored result."""

    def __init__(
        self,
        *,
        make_spawner: Callable[[E2EStack], StageSpawner] | None = None,
        judge: BenchJudge | None = None,
        stage_timeout_seconds: float = 900.0,
        fixture_timeout_seconds: float = 3600.0,
    ) -> None:
        self._make_spawner = make_spawner or (
            lambda _stack: OrchestratorStageSpawner(
                stage_timeout_seconds=stage_timeout_seconds
            )
        )
        self._judge = judge or BenchJudge()
        self._fixture_timeout_seconds = fixture_timeout_seconds

    def run_cohort(
        self,
        role_slug: str,
        cohort_name: str,
        *,
        fixtures: Sequence[BenchTaskSpec] | None = None,
        json_out: Path | None = None,
    ) -> CohortResult:
        role = get_agent_role(role_slug)
        if role not in _BENCH_ROLES:
            raise ValueError(
                f"eval bench only scores developer/qa/cell_pm/main_pm "
                f"cohorts (got {role_slug!r} -> role={role!r}); a new role "
                "first needs an entry-point shape in the module docstring"
            )
        chosen = [f for f in (fixtures or FIXTURES) if f.target_role == role]
        if not chosen:
            raise ValueError(f"no fixtures matched target_role={role!r}")

        results: list[FixtureResult] = []
        with _bench_environment(role_slug) as env:
            for fixture in chosen:
                results.append(self._run_fixture(env, role_slug, fixture))

        cohort = CohortResult(
            role_slug=role_slug, cohort_name=cohort_name, fixtures=results
        )
        _print_table(cohort)
        if json_out is not None:
            json_out.write_text(json.dumps(cohort.as_dict(), indent=2))
        return cohort

    def _run_fixture(
        self,
        env: BenchEnvironment,
        role_slug: str,
        fixture: BenchTaskSpec,
    ) -> FixtureResult:
        target_role = fixture.target_role
        started_at = datetime.now(UTC)
        task_id = _create_bench_entry_task(env, role_slug, fixture)
        spawner = self._make_spawner(env.stack)
        final_task, stalled = asyncio.run(
            _drive_task_to_terminal(
                env.stack,
                spawner,
                task_id,
                role_slug=role_slug,
                prefix=env.prefix,
                target_role=target_role,
                fixture_timeout_seconds=self._fixture_timeout_seconds,
            )
        )
        metrics = _deterministic_metrics(
            env.stack,
            task_id,
            started_at,
            stalled,
            final_task.get("status", "unknown"),
        )
        diff, role_pass = _role_verdict(
            env,
            task_id,
            target_role,
            final_status=final_task.get("status", "unknown"),
            branch_name=final_task.get("branch_name"),
            stalled=stalled,
        )
        notes = _collected_notes(env.stack, task_id)
        judge = asyncio.run(self._judge.score(fixture=fixture, diff=diff, notes=notes))
        return FixtureResult(
            fixture_key=fixture.key,
            metrics=metrics,
            judge=judge,
            target_role=target_role,
            role_pass=role_pass,
        )


def _create_bench_entry_task(
    env: BenchEnvironment, role_slug: str, fixture: BenchTaskSpec
) -> UUID:
    """Create the fixture task in its role's entry shape. QA: the repo files
    ARE the PR under review (never seeded onto master) — prebuild the review
    turn. PM: a PARENT task (cell_pm pending under the bench cell; main_pm a
    fresh coordination ROOT — no bench cell, parent_task_id=None,
    Team.MAIN_PM) whose branch is cut up front so its claim needs no
    mid-flight branch derivation. Developer: the original seeded leaf."""
    target_role = fixture.target_role
    if target_role == "qa":
        task_id = _create_bench_task(
            stack=env.stack,
            project_id=env.project_id,
            owner_slug=role_slug,
            fixture=fixture,
            team=env.team,
            parent_task_id=env.cell_id,
            creator_slug=f"{env.prefix}-dev-1",
        )
        _prebuild_qa_review(
            env.stack,
            base_branch=env.cell_branch or "master",
            team=env.team,
            fixture=fixture,
            task_id=task_id,
        )
    elif target_role in {"cell_pm", "main_pm"}:
        task_id = _create_bench_task(
            stack=env.stack,
            project_id=env.project_id,
            owner_slug=role_slug,
            fixture=fixture,
            team=Team.MAIN_PM if target_role == "main_pm" else env.team,
            parent_task_id=env.cell_id if target_role == "cell_pm" else None,
            creator_slug="main-pm"
            if target_role == "main_pm"
            else f"{env.prefix}-dev-1",
        )
        _cut_bench_branch(
            env.stack,
            task_id,
            base_branch=env.cell_branch or "master",
            team=env.team,
        )
    else:
        _seed_fixture_repo(env.stack, fixture)
        # The bench cell branch is cut once per environment and never
        # given commits of its own — fast-forward it to master's
        # just-updated tip so THIS fixture's newly-pushed bench/<key>/
        # files are actually present when the leaf's own branch is cut
        # from it below.
        assert env.cell_branch is not None
        _fast_forward_branch(env.stack, env.cell_branch, onto="master")
        task_id = _create_bench_task(
            stack=env.stack,
            project_id=env.project_id,
            owner_slug=role_slug,
            fixture=fixture,
            team=env.team,
            parent_task_id=env.cell_id,
        )
    return task_id


def _role_verdict(
    env: BenchEnvironment,
    task_id: UUID,
    target_role: str,
    *,
    final_status: str,
    branch_name: str | None,
    stalled: bool,
) -> tuple[str, bool | None]:
    """The role-aware judge input (``diff``) and deterministic pass verdict.

    PM: the judge input is the delegation record and full coverage is the
    pass. QA: a trap fixture's deterministic pass is "the QA agent bounced
    the defective PR" (needs_revision under the fixture timeout); a completed
    run means the defective PR was approved (missed) — still scored, judged
    harshly. Developer: the branch diff, default completed-and-not-stalled.
    """
    if target_role in {"cell_pm", "main_pm"}:
        diff, coverage_ok = _pm_delegation_summary(env.stack, task_id)
        return diff, coverage_ok and not stalled
    diff = _task_diff(env.stack, env.cell_branch or "master", branch_name)
    if target_role == "qa":
        return diff, final_status == "needs_revision" and not stalled
    return diff, None
