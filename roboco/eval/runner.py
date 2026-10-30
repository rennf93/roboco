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

Catch-rate: a seeded-defect fixture (``roboco/eval/fixtures.py``) carries an
optional ``expected_catch_gate`` naming which verification layer (QA's
per-AC stamp, the architectural-conventions check, or the in-path PR gate)
is supposed to stop its seeded defect. ``CohortResult.as_dict()``'s
``"catch_rate"`` object — a sibling of "aggregate" and "judge", never nested
inside either — reports ``caught / seeded`` for this (role, model/provider,
doctrine) cohort's seeded-defect fixtures. The verdict is derived ONLY from
the revision-findings ledger (``task_review_findings``) and rejector-
attributed bounce audit events (``task.qa_fail`` / ``task.pr_fail`` / ...) —
see ``_score_catch`` / ``_catch_gate_evidence`` — NEVER from a diff
comparison. A fixture with no ``expected_catch_gate`` (every pre-existing
golden fixture) is excluded from both the numerator and denominator.

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
run`` works end to end for a developer-role cohort; it needs a Docker daemon
+ built agent images for the real spawn path.

Role scope: developer, qa, and cell_pm fixtures are supported.
Developer fixtures drive a parentless leaf task PENDING → developer → QA
→ docs → cell-PM review → completed. QA fixtures enter at awaiting_qa
with a pre-built PR containing an injected defect — the QA agent's job is
to catch the defect (fail_review) vs miss it (pass_review of a defective
PR). PM (cell_pm) fixtures enter as a PARENT task the PM must delegate with
covers_parent_criteria. main_pm is accepted by ``run_cohort`` but has no
fixture shape yet (org-level coordinator with no cell team — the non-cell
bench-environment path is handled explicitly via a skip-cell guard).
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
from roboco.models.base import Complexity, TaskType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from tests.e2e_smoke.harness import E2EStack

logger = structlog.get_logger()

_TERMINAL_STATUSES = {"completed", "cancelled"}
# Statuses this bench cannot progress past without a human (CEO) — scored as
# a stall, distinct from a genuine timeout, but never mistaken for success.
_HUMAN_GATED_STATUSES = {"awaiting_ceo_approval", "blocked", "paused"}

# status -> the role responsible for advancing it (default mapping). A bench
# fixture's target_role can override this: a cell_pm parent task drives the PM
# at pending/in_progress (not developer), and a QA fixture enters at
# awaiting_qa (already mapped to qa). Every other status (backlog,
# awaiting_pr_review, ...) never legitimately occurs; reaching one is scored
# as a stall (`_role_for_status` -> None).
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
# Roles run_cohort accepts. main_pm has no cell team — handled via a
# skip-cell guard in _bench_environment, not the _TEAM_PREFIX map.
_BENCH_ROLES = {"developer", "qa", "cell_pm", "main_pm"}

# ---------------------------------------------------------------------------
# Catch-rate scoring vocabulary — seeded-defect fixtures (roboco/eval/
# fixtures.py) name, via an OPTIONAL ``expected_catch_gate`` attribute (read
# with ``getattr``, default None — mirrors how ``injected_defect`` /
# ``expected_coverage`` started life before landing as real BenchTaskSpec
# fields), which verification layer is supposed to stop their seeded defect.
# A fixture with no ``expected_catch_gate`` (every pre-existing golden
# fixture) is not part of the catch-rate — see ``CohortResult.catch_rate_stats``.
# ---------------------------------------------------------------------------
CATCH_GATE_QA = "qa_ac_stamp"
CATCH_GATE_CONVENTIONS = "conventions_check"
CATCH_GATE_PR = "pr_gate"

# Rejector-attributed bounce events emitted at the SAME needs_revision
# transition that bumps revision_count (TaskService._audit_events_for).
_REWORK_AUDIT_EVENTS = (
    "task.qa_fail",
    "task.pr_fail",
    "task.request_changes",
    "task.ceo_reject",
)
_ALL_FINDING_ORIGINS = frozenset({"qa", "pr_gate", "pm", "ceo"})
_ALL_AUDIT_EVENTS = frozenset(_REWORK_AUDIT_EVENTS)

# Each expected-catch-gate name maps to the task_review_findings ``origin``
# values and the audit event types that count as "this gate fired". Both
# "conventions_check" and "pr_gate" accept a "qa" origin too: today the bench
# harness only drives QA-entry fixtures to a real terminal turn (see
# _prepare_qa_entry) — a conventions/security defect surfaces as a QA finding
# citing the violation (QA's claim_review evidence already carries
# convention_findings) until/unless a future leaf wires a real
# awaiting_pr_review driving stage.
_CATCH_GATE_FINDING_ORIGINS: dict[str, frozenset[str]] = {
    CATCH_GATE_QA: frozenset({"qa"}),
    CATCH_GATE_CONVENTIONS: frozenset({"qa", "pr_gate"}),
    CATCH_GATE_PR: frozenset({"pr_gate"}),
}
_CATCH_GATE_AUDIT_EVENTS: dict[str, frozenset[str]] = {
    CATCH_GATE_QA: frozenset({"task.qa_fail"}),
    CATCH_GATE_CONVENTIONS: frozenset({"task.qa_fail", "task.pr_fail"}),
    CATCH_GATE_PR: frozenset({"task.pr_fail"}),
}

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
    # cell_id / cell_branch are None for the main_pm non-cell path (main_pm is
    # org-level with no cell team — see _bench_environment's skip-cell guard).
    cell_id: UUID | None
    cell_branch: str | None


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
def _bench_environment(dev_slug: str) -> Iterator[BenchEnvironment]:
    """Stand up the disposable e2e_smoke-style stack + one bench project +
    the fixed company of agents needed to run a fixture end to end.

    For cell roles (developer, qa, cell_pm) the path seeds a full cell company
    [dev, qa, doc, pm] and cuts a bench cell branch via ``_seed_bench_cell``.

    For main_pm (no cell team — ``main_pm`` is not in ``_TEAM_PREFIX``) the
    path skips cell setup entirely: no cell branch, no cell agents, just the
    main_pm itself. This is the explicit non-cell design — main_pm is an
    org-level coordinator, not a cell member, so the cell infrastructure does
    not apply. ``cell_id`` and ``cell_branch`` are ``None`` in this mode.
    """
    try:
        from tests.e2e_smoke.harness import build_e2e_stack
    except ImportError as exc:
        raise RuntimeError(
            "the eval CLI runs from a source checkout; tests/ is not shipped "
            "in containers or wheels — run `python -m roboco.eval` from a git "
            "clone of the repo, not an installed package"
        ) from exc

    role = get_agent_role(dev_slug)
    if role not in _BENCH_ROLES:
        raise ValueError(
            f"eval bench only scores {_BENCH_ROLES} roles "
            f"(got {dev_slug!r} -> role={role!r})"
        )
    team_str = get_agent_team(dev_slug)

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
                # change that does start them fails closed, not open.
                mp.setattr(settings, "obsidian_vault_enabled", False)
                mp.setattr(settings, "vault_intake_enabled", False)
                mp.setattr(settings, "vault_kb_enabled", False)
                mp.setattr(settings, "vault_report_enabled", False)
                try:
                    dev_uuid = _foundation.AGENTS[dev_slug].uuid

                    if team_str in _TEAM_PREFIX:
                        # Cell role path — seed the full cell company and
                        # cut a bench cell branch for leaf PRs to merge into.
                        # Deduplicate: when dev_slug IS a cell agent (e.g.
                        # ``be-qa`` for a QA bench), the naive list would add
                        # it twice and miss ``be-dev-1`` (the PM delegate
                        # target).  The set union always includes all four cell
                        # roles plus dev_slug.
                        team = Team(team_str)
                        prefix = _TEAM_PREFIX[team_str]
                        _seed_company(
                            stack,
                            {
                                dev_slug,
                                f"{prefix}-dev-1",
                                f"{prefix}-qa",
                                f"{prefix}-doc",
                                f"{prefix}-pm",
                            },
                        )
                        project_id, project_slug = _seed_project(stack, team, dev_uuid)
                        cell_id, cell_branch = _seed_bench_cell(
                            stack, project_id, team, prefix
                        )
                    else:
                        # Non-cell path (main_pm) — no cell branch, no cell
                        # agents. main_pm is org-level; the bench environment
                        # provides only the main_pm agent and a project.
                        team = Team(team_str or "main_pm")
                        _seed_company(stack, [dev_slug])
                        project_id, project_slug = _seed_project(stack, team, dev_uuid)
                        cell_id, cell_branch = None, None

                    yield BenchEnvironment(
                        stack=stack,
                        project_id=project_id,
                        project_slug=project_slug,
                        team=team,
                        cell_id=cell_id,
                        cell_branch=cell_branch,
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
    dev_slug: str,
    fixture: BenchTaskSpec,
    team: Team,
    parent_task_id: UUID | None,
    assignee_slug: str | None = None,
) -> UUID:
    """Create the real task (TaskService.create), pre-assigned to the
    assignee (default: dev_slug) — the "PM pre-assigned this" shape every
    dev-entry task in production already uses (e.g. the video engine's
    authoring tasks) — as a child of the environment's bench cell (see
    ``_seed_bench_cell``) so its eventual PR merges into a real cell branch,
    not the project's protected default branch.

    When ``parent_task_id`` is None the task is parentless (main_pm root or
    a PM parent-task fixture). When ``assignee_slug`` differs from
    ``dev_slug`` the task is pre-assigned to a different agent (e.g. the
    cell_pm for a PM parent-task fixture).
    """
    from roboco.models.task import TaskCreateRequest
    from roboco.services.task import EVAL_BENCH_SOURCE, get_task_service

    assignee = assignee_slug or dev_slug
    creator_uuid = _foundation.AGENTS[dev_slug].uuid
    assignee_uuid = _foundation.AGENTS[assignee].uuid
    # A PM parent-task fixture is a coordination task — PMs coordinate and
    # never execute code (TaskService's PM_NO_CODE guard), so a code-typed
    # parent pre-assigned to the cell_pm is rejected at create. A parent the
    # PM delegates WITH covers_parent_criteria is a planning task by nature.
    task_type = TaskType.PLANNING if fixture.is_parent else fixture.task_type
    holder: dict[str, Any] = {}

    async def _run(session: Any) -> None:
        req = TaskCreateRequest(
            title=fixture.title,
            description=fixture.description,
            acceptance_criteria=list(fixture.acceptance_criteria),
            team=team,
            created_by=creator_uuid,
            task_type=task_type,
            nature=fixture.nature,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
            parent_task_id=parent_task_id,
            assigned_to=assignee_uuid,
            source=EVAL_BENCH_SOURCE,
            confirmed_by_human=True,
        )
        task = await get_task_service(session).create(req)
        holder["id"] = task.id

    stack.run_db(_run)
    return cast("UUID", holder["id"])


def _prepare_qa_entry(
    stack: E2EStack,
    task_id: UUID,
    fixture: BenchTaskSpec,
    env: BenchEnvironment,
) -> None:
    """Pre-advance a task to ``awaiting_qa`` with a pre-built PR containing
    the fixture's ``repo_files`` — the "defective fix" the QA agent must
    review. The ``injected_defect`` field (if set) describes the defect the
    QA agent should catch via ``fail_review`` vs miss via ``pass_review``.

    Creates a feature branch from the base (cell branch or master), commits
    the repo_files onto it, pushes, and sets the task's ``branch_name``,
    ``pr_number``, and ``status`` directly — standing in for the developer
    turn that would normally advance the task to awaiting_qa.
    """
    from tests.e2e_smoke.arcs import origin_branch, set_branch_name
    from tests.e2e_smoke.harness import _git

    from roboco.models.base import TaskStatus

    base = env.cell_branch or "master"
    # The branch must satisfy the repo's branch convention
    # ({type}/{team}/seg1--seg2 — merge_chain._BRANCH_RE): the gateway
    # validates it on the QA claim path, so a free-form name like
    # ``feature/bench/<key>/<hex>`` raises "invalid branch" on claim_review.
    branch = f"bug/{env.team.value}/{fixture.key}--{uuid4().hex[:8]}"
    origin_branch(stack, branch, start=base)

    # Commit the fixture's repo_files as the "developer's fix" (with defect)
    admin = stack.github.admin_clone
    _git(admin, "fetch", "origin", "--prune")
    _git(admin, "checkout", "-B", branch, f"origin/{branch}")
    for rel_path, content in fixture.repo_files:
        path = admin / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        _git(admin, "add", rel_path)
    _git(
        admin,
        "commit",
        "-m",
        f"fix: {fixture.title} (pre-built PR for QA review)",
    )
    _git(admin, "push", "origin", branch)

    set_branch_name(stack, task_id, branch)

    async def _run(session: Any) -> None:
        from sqlalchemy import select

        from roboco.db.tables import TaskTable

        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        row.pr_number = 1
        row.pr_created = True
        row.status = TaskStatus.AWAITING_QA

    stack.run_db(_run)


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


def _role_for_status(status: str, target_role: str) -> str | None:
    """Map a task status to the responsible role, with target_role override.

    For cell_pm parent-task fixtures, the PM owns pending/claimed/in_progress/
    needs_revision (not developer — the PM is the one planning and delegating).
    For QA fixtures, awaiting_qa already maps to qa. For developer fixtures,
    the default _STAGE_ROLE mapping is unchanged.
    """
    if target_role == "cell_pm" and status in (
        "pending",
        "claimed",
        "in_progress",
        "needs_revision",
    ):
        return "cell_pm"
    return _STAGE_ROLE.get(status)


def _agent_slug_for_role(role: str, dev_slug: str, prefix: str | None) -> str:
    """Build the agent slug for a role. Developer uses dev_slug directly;
    cell roles use ``{prefix}-{suffix}``. main_pm has no prefix — dev_slug
    IS the main-pm slug."""
    if role == "developer":
        return dev_slug
    if prefix is None:
        # main_pm or any non-cell role — the slug is the dev_slug itself
        return dev_slug
    return f"{prefix}-{_ROLE_SUFFIX[role]}"


async def _drive_task_to_terminal(
    stack: E2EStack,
    spawner: StageSpawner,
    task_id: UUID,
    *,
    dev_slug: str,
    prefix: str | None,
    fixture_timeout_seconds: float,
    target_role: str = "developer",
    max_stages: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Poll ``task_id`` to a terminal state, invoking ``spawner.run_stage``
    for whichever role owns the current status, until terminal, the hard
    per-fixture timeout, or ``max_stages`` is reached.

    Returns ``(final_task_dict, stalled)``. ``stalled`` is True when the loop
    gave up (timeout, a human-gated status, max_stages reached, or a status
    with no owning role) rather than reaching a genuine terminal state.

    ``target_role`` overrides the status→role mapping for non-developer entry
    points (e.g. cell_pm parent-task fixtures where the PM owns pending). When
    ``max_stages`` is set, the loop stops after that many stage invocations —
    used for PM fixtures where one delegation turn is the whole bench.
    """
    from roboco.runtime.orchestrator import _system_api_headers

    deadline = time.monotonic() + fixture_timeout_seconds
    api = f"{stack.base_url}/api"
    stages_run = 0
    # The claim POST below requires an agent identity (X-Agent-ID); this
    # driving loop plays the same "trusted internal caller" role the real
    # dispatch tick does, so it authenticates the same way — the system
    # identity headers the orchestrator's own dispatch client carries.
    async with httpx.AsyncClient(timeout=30.0, headers=_system_api_headers()) as client:
        task = (await client.get(f"{api}/tasks/{task_id}")).json()
        while True:
            status = task.get("status")
            if status in _TERMINAL_STATUSES:
                return task, False
            if status in _HUMAN_GATED_STATUSES:
                return task, True
            role = _role_for_status(status, target_role)
            if role is None or time.monotonic() >= deadline:
                return task, True
            if max_stages is not None and stages_run >= max_stages:
                return task, True
            agent_slug = _agent_slug_for_role(role, dev_slug, prefix)
            if role == "cell_pm" and not task.get("assigned_to"):
                await _claim_for_pm(client, api, str(task_id), agent_slug)
            await spawner.run_stage(task=task, agent_slug=agent_slug)
            stages_run += 1
            task = (await client.get(f"{api}/tasks/{task_id}")).json()


# ---------------------------------------------------------------------------
# Local-model judge — same Ollama-compatible endpoint MemoryDistiller uses
# ---------------------------------------------------------------------------

_JUDGE_TIMEOUT_SECONDS = 60.0
_JUDGE_SCORE_RE = re.compile(r"score\s*:\s*([1-5])", re.IGNORECASE)


@dataclass
class JudgeVerdict:
    score: int | None
    rationale: str | None


_JUDGE_HEADER = (
    "You are grading a completed task for an automated agent quality "
    "bench. Score 1-5 (5 = fully meets the expectation, 1 = does not "
    "meet it at all). Reply in exactly this shape:\n"
    "Score: <1-5>\n"
    "Rationale: <one line>\n\n"
)


def _qa_judge_prompt(
    fixture: BenchTaskSpec,
    criteria: str,
    notes: str,
    *,
    injected_defect: str | None = None,
) -> str:
    defect_desc = injected_defect or "(unspecified defect)"
    return (
        _JUDGE_HEADER + "You are grading a QA review. The QA agent reviewed a PR that "
        "contained an INJECTED DEFECT. A score of 5 means the QA agent "
        "CAUGHT the defect (via fail_review with findings naming the "
        "defect). A score of 1 means the QA agent MISSED the defect "
        "(pass_review of a defective PR).\n\n"
        f"Task: {fixture.title}\n"
        f"Acceptance criteria:\n{criteria}\n\n"
        f"Injected defect: {defect_desc}\n\n"
        f"Expected (checked-in): {fixture.expectations}\n\n"
        f"Actual notes:\n{notes or '(no notes)'}\n"
    )


def _pm_judge_prompt(
    fixture: BenchTaskSpec,
    criteria: str,
    notes: str,
    *,
    expected_coverage: tuple[str, ...] = (),
) -> str:
    coverage_list = (
        "\n".join(f"- {c}" for c in expected_coverage)
        if expected_coverage
        else "(no explicit coverage criteria — check if the PM delegated at all)"
    )
    return (
        _JUDGE_HEADER
        + "You are grading a PM delegation. The PM was given a parent task "
        "and must delegate with covers_parent_criteria mapping every "
        "acceptance criterion. A score of 5 means the PM delegated with "
        "full coverage of every acceptance criterion. A score of 1 means "
        "the PM dropped criteria or failed to delegate.\n\n"
        f"Task: {fixture.title}\n"
        f"Acceptance criteria:\n{criteria}\n\n"
        f"Expected coverage:\n{coverage_list}\n\n"
        f"Expected (checked-in): {fixture.expectations}\n\n"
        f"Actual notes:\n{notes or '(no notes)'}\n"
    )


def _dev_judge_prompt(
    fixture: BenchTaskSpec,
    criteria: str,
    notes: str,
    *,
    diff: str = "",
) -> str:
    return (
        _JUDGE_HEADER + f"Task: {fixture.title}\n"
        f"Acceptance criteria:\n{criteria}\n\n"
        f"Expected (checked-in): {fixture.expectations}\n\n"
        f"Actual diff:\n{diff or '(empty diff)'}\n\n"
        f"Actual notes:\n{notes or '(no notes)'}\n"
    )


def _build_judge_prompt(fixture: BenchTaskSpec, diff: str, notes: str) -> str:
    """Build the judge prompt, selecting a role-specific template via
    ``fixture.target_role``. Developer fixtures use the generic diff+notes
    grading prompt. QA fixtures grade defect-caught-vs-missed. PM fixtures
    grade coverage-mapped-vs-missing. Each builder receives only the kwargs
    it actually consumes (the project's ARG001 lint forbids unused args, so
    the builders keep narrow signatures and the dispatcher branches on
    ``fixture.target_role``) — passing a full unified kwarg set raised
    ``TypeError`` at dispatch, which ``BenchJudge.score`` swallowed as
    ``score=None``."""
    criteria = "\n".join(f"- {c}" for c in fixture.acceptance_criteria)
    target_role = fixture.target_role
    if target_role == "qa":
        return _qa_judge_prompt(
            fixture, criteria, notes, injected_defect=fixture.injected_defect
        )
    if target_role in ("cell_pm", "main_pm"):
        return _pm_judge_prompt(
            fixture, criteria, notes, expected_coverage=fixture.expected_coverage
        )
    return _dev_judge_prompt(fixture, criteria, notes, diff=diff)


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
class CatchVerdict:
    """Whether a seeded-defect fixture's expected verification gate fired,
    derived ONLY from findings-ledger rows + bounce audit events (see
    ``_score_catch`` / ``_catch_gate_evidence``) — never from a diff."""

    expected_gate: str
    caught: bool
    evidence: list[str]


@dataclass
class FixtureResult:
    fixture_key: str
    metrics: DeterministicMetrics
    judge: JudgeVerdict
    # None for every pre-existing golden fixture (no expected_catch_gate);
    # set only for a seeded-defect fixture — see CatchVerdict.
    catch: CatchVerdict | None = None

    @property
    def passed(self) -> bool:
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

    @property
    def catch_rate_stats(self) -> tuple[int, int]:
        """``(caught, seeded)`` — "seeded" counts only fixtures carrying a
        catch verdict (i.e. a seeded-defect fixture with an
        ``expected_catch_gate``); the pre-existing golden fixtures have
        ``catch=None`` and are excluded, exactly like ``mean_judge_score``
        excludes an unscored fixture rather than counting it as a 0."""
        scored = [f.catch for f in self.fixtures if f.catch is not None]
        caught = sum(1 for c in scored if c.caught)
        return caught, len(scored)

    @property
    def catch_rate(self) -> float | None:
        caught, seeded = self.catch_rate_stats
        return (caught / seeded) if seeded else None

    def as_dict(self) -> dict[str, Any]:
        # Judge fields are nested under their own "judge" object (both here
        # and per-fixture below) and stamped non_deterministic=True — a local-
        # model score is not a repeatable metric like the sibling deterministic
        # ones, and a naive diff between two cohort JSONs must not mistake
        # judge noise for a real regression. catch_rate is a NEW sibling of
        # both "aggregate" and "judge" (never nested inside either) — it is
        # deterministic (findings-ledger + audit rows, never a diff) but is
        # its own metric family, computed for this (role, model/provider,
        # doctrine) cohort's seeded-defect fixtures only.
        caught, seeded = self.catch_rate_stats
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
            "catch_rate": {
                "caught": caught,
                "seeded": seeded,
                "rate": self.catch_rate,
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
                    "catch": (
                        {
                            "expected_gate": f.catch.expected_gate,
                            "caught": f.catch.caught,
                            "evidence": f.catch.evidence,
                        }
                        if f.catch is not None
                        else None
                    ),
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


def _catch_gate_evidence(
    stack: E2EStack, task_id: UUID
) -> tuple[frozenset[str], frozenset[str]]:
    """Read the two NEVER-diff-comparison signals a catch verdict is derived
    from: the revision-findings ledger's per-finding ``origin``
    (task_review_findings — CLAUDE.md's "Revision findings ledger") and the
    rejector-attributed bounce audit events on this task (task.qa_fail /
    task.pr_fail / task.request_changes / task.ceo_reject)."""
    from sqlalchemy import select

    from roboco.db.tables import AuditLogTable
    from roboco.services.repositories.review_findings import ReviewFindingsRepository

    async def _run(session: Any) -> tuple[frozenset[str], frozenset[str]]:
        findings = await ReviewFindingsRepository(session).list_for_task(task_id)
        origins = frozenset(f.origin for f in findings)
        result = await session.execute(
            select(AuditLogTable.event_type).where(
                AuditLogTable.target_type == "task",
                AuditLogTable.target_id == task_id,
                AuditLogTable.event_type.in_(_REWORK_AUDIT_EVENTS),
            )
        )
        events = frozenset(row[0] for row in result.all())
        return origins, events

    return cast("tuple[frozenset[str], frozenset[str]]", stack.run_db(_run))


def _score_catch(
    expected_gate: str | None,
    finding_origins: frozenset[str],
    audit_events: frozenset[str],
) -> CatchVerdict | None:
    """Pure catch/miss derivation — no DB, no diff. ``None`` means "not a
    seeded-defect fixture" (no expected_catch_gate), so it never enters the
    catch-rate denominator. An unrecognized gate name (the sibling fixture
    leaf drifted from this module's vocabulary) degrades to "did ANY
    verification signal fire at all" rather than silently mis-scoring every
    such fixture a miss."""
    if not expected_gate:
        return None
    origins = _CATCH_GATE_FINDING_ORIGINS.get(expected_gate, _ALL_FINDING_ORIGINS)
    events = _CATCH_GATE_AUDIT_EVENTS.get(expected_gate, _ALL_AUDIT_EVENTS)
    matched_origins = sorted(finding_origins & origins)
    matched_events = sorted(audit_events & events)
    caught = bool(matched_origins or matched_events)
    evidence = [f"finding_origin:{o}" for o in matched_origins] + [
        f"audit_event:{e}" for e in matched_events
    ]
    return CatchVerdict(expected_gate=expected_gate, caught=caught, evidence=evidence)


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
    caught, seeded = cohort.catch_rate_stats
    if seeded:
        print(
            f"catch_rate={caught}/{seeded} ({cohort.catch_rate:.2f}) — "
            "seeded-defect fixtures only, derived from findings-ledger rows "
            "+ bounce events, never a diff"
        )


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------


class EvalRunner:
    """Runs one cohort (a labeled (role, model/provider config) run) through
    every matching golden-task fixture and prints + returns the scored
    result. Supports developer, qa, cell_pm, and main_pm role fixtures."""

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
                f"eval bench only scores {_BENCH_ROLES} roles "
                f"(got {role_slug!r} -> role={role!r})"
            )
        team_str = get_agent_team(role_slug)
        prefix = _TEAM_PREFIX.get(team_str) if team_str in _TEAM_PREFIX else None
        chosen = [f for f in (fixtures or FIXTURES) if f.target_role == role]
        if not chosen:
            raise ValueError(f"no fixtures matched target_role={role!r}")

        results: list[FixtureResult] = []
        with _bench_environment(role_slug) as env:
            for fixture in chosen:
                results.append(self._run_fixture(env, prefix, role_slug, fixture))

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
        prefix: str | None,
        dev_slug: str,
        fixture: BenchTaskSpec,
    ) -> FixtureResult:
        entry_status = fixture.entry_status
        is_parent = fixture.is_parent
        target_role = fixture.target_role

        # QA fixtures: skip _seed_fixture_repo (the repo_files go on the
        # task's pre-built PR branch instead of master — see
        # _prepare_qa_entry). Developer/PM fixtures seed master as usual.
        if entry_status != "awaiting_qa":
            _seed_fixture_repo(env.stack, fixture)
        if env.cell_branch:
            _fast_forward_branch(env.stack, env.cell_branch, onto="master")

        started_at = datetime.now(UTC)

        if is_parent:
            # PM parent-task fixture: assign to the cell_pm, no parent.
            pm_slug = f"{prefix}-pm" if prefix else dev_slug
            task_id = _create_bench_task(
                stack=env.stack,
                project_id=env.project_id,
                dev_slug=dev_slug,
                fixture=fixture,
                team=env.team,
                parent_task_id=None,
                assignee_slug=pm_slug,
            )
        else:
            task_id = _create_bench_task(
                stack=env.stack,
                project_id=env.project_id,
                dev_slug=dev_slug,
                fixture=fixture,
                team=env.team,
                parent_task_id=env.cell_id,
            )

        # QA entry: pre-advance to awaiting_qa with a pre-built PR
        if entry_status == "awaiting_qa":
            _prepare_qa_entry(env.stack, task_id, fixture, env)

        spawner = self._make_spawner(env.stack)
        # PM parent-task: drive one delegation turn, then score (the parent
        # won't go terminal from one PM turn — max_stages=1 stops the loop).
        # QA entry: drive one QA review turn, then score — the QA agent either
        # catches the defect (fail_review → needs_revision) or misses it
        # (pass_review → awaiting_documentation); either way the bench is
        # about the QA turn, not the downstream lifecycle.
        max_stages = 1 if (is_parent or entry_status == "awaiting_qa") else None
        final_task, stalled = asyncio.run(
            _drive_task_to_terminal(
                env.stack,
                spawner,
                task_id,
                dev_slug=dev_slug,
                prefix=prefix,
                fixture_timeout_seconds=self._fixture_timeout_seconds,
                target_role=target_role,
                max_stages=max_stages,
            )
        )
        metrics = _deterministic_metrics(
            env.stack, task_id, started_at, stalled, final_task.get("status", "unknown")
        )
        base_branch = env.cell_branch or "master"
        diff = _task_diff(env.stack, base_branch, final_task.get("branch_name"))
        notes = _collected_notes(env.stack, task_id)
        judge = asyncio.run(self._judge.score(fixture=fixture, diff=diff, notes=notes))

        catch: CatchVerdict | None = None
        expected_gate = getattr(fixture, "expected_catch_gate", None)
        if expected_gate:
            finding_origins, audit_events = _catch_gate_evidence(env.stack, task_id)
            catch = _score_catch(expected_gate, finding_origins, audit_events)

        return FixtureResult(
            fixture_key=fixture.key, metrics=metrics, judge=judge, catch=catch
        )
