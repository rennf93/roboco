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

Real-spawn status: CUT for this release. ``StageSpawner`` is the seam
between a real container spawn and a scripted stand-in, and
``OrchestratorStageSpawner`` — what would be the default, real
implementation — raises ``NotImplementedError`` at construction: a spawned
container's MCP servers resolve their orchestrator URL via
``_generate_mcp_config`` (``PROJECT_HOST_PATH`` -> the REAL production
hostname, or ``settings.port``), never the patched ``settings.api_url`` this
harness's disposable stack listens on — combined with ``_seed_company``
seeding agents under their REAL production UUIDs, a real spawn here could
authenticate as e.g. be-dev-1 against the production orchestrator and act on
real tasks. Fixing the spawn-env wiring is a dedicated follow-up. The ONLY
working ``StageSpawner`` today is an injected scripted one (see
``tests/e2e_smoke/test_eval_bench.py``) that drives the SAME real MCP flow/do
tool functions e2e_smoke's ``ScriptedAgent`` uses — proving the runner's
polling/scoring/DB plumbing without touching Docker. ``python -m roboco.eval
run`` therefore does not work yet; it is wired for the day the follow-up
lands, not for use today.

Scope cut: only developer-role fixtures are supported (``run_cohort``
refuses any other role). QA/documenter/cell-PM only ever pick up a task a
developer has already advanced through the lifecycle — there is no
"freshly PENDING task assigned straight to QA" shape to bench them with the
same one-task-per-fixture design. A QA/PM-focused bench would need a
different fixture shape (a pre-built PR to review) and is future work.
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

# status -> the role responsible for advancing it. A bench fixture is a leaf
# task (no parent, no PR-review gate — see the module docstring), so every
# other status (backlog, awaiting_pr_review, ...) never legitimately occurs;
# reaching one is scored as a stall (`_STAGE_ROLE.get(status)` -> None).
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
    cell_id: UUID
    cell_branch: str


def _seed_company(stack: E2EStack, slugs: Iterable[str]) -> None:
    """Seed the canonical agents needed to run a fixture's whole lifecycle.

    Uses each slug's REAL fixed UUID from ``foundation.identity.AGENTS``
    (not a random one, unlike ``tests/e2e_smoke/arcs.py``'s ``seed_company``)
    so that orchestrator-internal helpers keyed by that static registry
    (``get_agent_role``, the UUID->slug reverse map, ...) resolve exactly as
    they would in a real deployment.
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
    the fixed company of agents needed to run a fixture end to end."""
    try:
        from tests.e2e_smoke.harness import build_e2e_stack
    except ImportError as exc:
        raise RuntimeError(
            "the eval CLI runs from a source checkout; tests/ is not shipped "
            "in containers or wheels — run `python -m roboco.eval` from a git "
            "clone of the repo, not an installed package"
        ) from exc

    team_str = get_agent_team(dev_slug)
    if team_str not in _TEAM_PREFIX:
        raise ValueError(f"{dev_slug!r} has no known cell team ({team_str!r})")
    team = Team(team_str)
    prefix = _TEAM_PREFIX[team_str]

    with _scratch_database() as db_url:
        root_path = Path(tempfile.mkdtemp(prefix="roboco-eval-"))
        try:
            stack_cm = contextlib.contextmanager(build_e2e_stack)
            with stack_cm(db_url, _ScratchTmpFactory(root_path)) as stack:
                mp = pytest.MonkeyPatch()
                mp.setattr(settings, "api_url", stack.base_url)
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
                    _seed_company(
                        stack,
                        [dev_slug, f"{prefix}-qa", f"{prefix}-doc", f"{prefix}-pm"],
                    )
                    project_id, project_slug = _seed_project(stack, team, dev_uuid)
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
    stack: E2EStack,
    project_id: UUID,
    dev_slug: str,
    fixture: BenchTaskSpec,
    team: Team,
    parent_task_id: UUID,
) -> UUID:
    """Create the real task (TaskService.create), pre-assigned to `dev_slug`
    — the "PM pre-assigned this" shape every dev-entry task in production
    already uses (e.g. the video engine's authoring tasks) — as a child of
    the environment's bench cell (see ``_seed_bench_cell``) so its eventual
    PR merges into a real cell branch, not the project's protected default
    branch."""
    from roboco.models.task import TaskCreateRequest
    from roboco.services.task import EVAL_BENCH_SOURCE, get_task_service

    dev_uuid = _foundation.AGENTS[dev_slug].uuid
    holder: dict[str, Any] = {}

    async def _run(session: Any) -> None:
        req = TaskCreateRequest(
            title=fixture.title,
            description=fixture.description,
            acceptance_criteria=list(fixture.acceptance_criteria),
            team=team,
            created_by=dev_uuid,
            task_type=fixture.task_type,
            nature=fixture.nature,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
            parent_task_id=parent_task_id,
            assigned_to=dev_uuid,
            source=EVAL_BENCH_SOURCE,
            confirmed_by_human=True,
        )
        task = await get_task_service(session).create(req)
        holder["id"] = task.id

    stack.run_db(_run)
    return cast("UUID", holder["id"])


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
    """CUT for this release — do not construct. See the ``NotImplementedError``
    raised below for exactly why, and the module docstring's "Real-spawn
    status" section.

    This was meant to be the default, real ``StageSpawner``: drive one turn
    via the REAL ``AgentOrchestrator.spawn_agent`` — the exact method the
    production dispatcher calls — reusing its own ``_get_prompt_for_agent`` /
    ``_task_git_context`` helpers so the prompt and workspace mount are
    byte-for-byte what a real dispatch tick would build, then wait for the
    container to exit (or the stage timeout). The ``run_stage`` body below is
    otherwise correct and is left in place for the follow-up that fixes the
    wiring (see ``__init__``) rather than deleted — re-enable it there by
    removing the raise.
    """

    _orchestrator: Any
    _stage_timeout_seconds: float

    def __init__(self, stage_timeout_seconds: float = 900.0) -> None:
        raise NotImplementedError(
            "OrchestratorStageSpawner (the real-spawn path) is cut from this "
            "release: a spawned container's MCP servers connect via "
            "_generate_mcp_config, which resolves the orchestrator URL from "
            "PROJECT_HOST_PATH ('http://roboco-orchestrator:8000', the REAL "
            "production hostname) or settings.port — NEVER the patched "
            "settings.api_url this harness's disposable stack listens on. "
            "Combined with _seed_company seeding agents under their REAL "
            "production UUIDs, a real spawn here would authenticate as e.g. "
            "be-dev-1 against the production orchestrator and could act on "
            "real tasks. Fixing this belongs in a dedicated follow-up that "
            "makes the spawn env honor the patched stack; until then only "
            "the injectable scripted StageSpawner (see "
            "tests/e2e_smoke/test_eval_bench.py) is a working path."
        )

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


async def _drive_task_to_terminal(
    stack: E2EStack,
    spawner: StageSpawner,
    task_id: UUID,
    *,
    dev_slug: str,
    prefix: str,
    fixture_timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    """Poll ``task_id`` to a terminal state, invoking ``spawner.run_stage``
    for whichever role owns the current status, until terminal or the hard
    per-fixture timeout.

    Returns ``(final_task_dict, stalled)``. ``stalled`` is True when the loop
    gave up (timeout, a human-gated status, or a status with no owning
    role) rather than reaching a genuine terminal state.
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
            status = task.get("status")
            if status in _TERMINAL_STATUSES:
                return task, False
            if status in _HUMAN_GATED_STATUSES:
                return task, True
            role = _STAGE_ROLE.get(status)
            if role is None or time.monotonic() >= deadline:
                return task, True
            agent_slug = (
                dev_slug if role == "developer" else f"{prefix}-{_ROLE_SUFFIX[role]}"
            )
            if role == "cell_pm" and not task.get("assigned_to"):
                await _claim_for_pm(client, api, str(task_id), agent_slug)
            await spawner.run_stage(task=task, agent_slug=agent_slug)
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


def _build_judge_prompt(fixture: BenchTaskSpec, diff: str, notes: str) -> str:
    criteria = "\n".join(f"- {c}" for c in fixture.acceptance_criteria)
    return (
        "You are grading a completed engineering task against its checked-in "
        "expectation, for an automated agent quality bench. Score 1-5 (5 = "
        "fully meets the expectation, 1 = does not meet it at all). Reply in "
        "exactly this shape:\n"
        "Score: <1-5>\n"
        "Rationale: <one line>\n\n"
        f"Task: {fixture.title}\n"
        f"Acceptance criteria:\n{criteria}\n\n"
        f"Expected (checked-in): {fixture.expectations}\n\n"
        f"Actual diff:\n{diff or '(empty diff)'}\n\n"
        f"Actual notes:\n{notes or '(no notes)'}\n"
    )


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
    every developer-role golden-task fixture and prints + returns the
    scored result."""

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
        if role != "developer":
            raise ValueError(
                f"eval bench only scores developer-role agents right now "
                f"(got {role_slug!r} -> role={role!r}); see the module "
                "docstring's scope-cut note"
            )
        team_str = get_agent_team(role_slug)
        if team_str not in _TEAM_PREFIX:
            raise ValueError(f"{role_slug!r} has no known cell team ({team_str!r})")
        prefix = _TEAM_PREFIX[team_str]
        chosen = [f for f in (fixtures or FIXTURES) if f.target_role == "developer"]
        if not chosen:
            raise ValueError("no fixtures matched target_role='developer'")

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
        prefix: str,
        dev_slug: str,
        fixture: BenchTaskSpec,
    ) -> FixtureResult:
        _seed_fixture_repo(env.stack, fixture)
        # The bench cell branch is cut once per environment and never given
        # commits of its own — fast-forward it to master's just-updated tip
        # so THIS fixture's newly-pushed bench/<key>/ files are actually
        # present when the leaf's own branch is cut from it below.
        _fast_forward_branch(env.stack, env.cell_branch, onto="master")
        started_at = datetime.now(UTC)
        task_id = _create_bench_task(
            env.stack, env.project_id, dev_slug, fixture, env.team, env.cell_id
        )

        spawner = self._make_spawner(env.stack)
        final_task, stalled = asyncio.run(
            _drive_task_to_terminal(
                env.stack,
                spawner,
                task_id,
                dev_slug=dev_slug,
                prefix=prefix,
                fixture_timeout_seconds=self._fixture_timeout_seconds,
            )
        )
        metrics = _deterministic_metrics(
            env.stack, task_id, started_at, stalled, final_task.get("status", "unknown")
        )
        diff = _task_diff(env.stack, env.cell_branch, final_task.get("branch_name"))
        notes = _collected_notes(env.stack, task_id)
        judge = asyncio.run(self._judge.score(fixture=fixture, diff=diff, notes=notes))
        return FixtureResult(fixture_key=fixture.key, metrics=metrics, judge=judge)
