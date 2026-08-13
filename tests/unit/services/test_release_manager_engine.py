"""Release-manager engine: propose a CEO-gated release, held + deduped, never publish.

Mirrors the self-heal engine tests. The engine proposes only past the threshold +
green gate, holds the proposal for the CEO (confirmed_by_human=False, owned by the
Secretary, never dispatched), dedupes to one open proposal, and NEVER publishes /
approves — asserted here against a real Postgres DB.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.git import GitService
from roboco.services.release_manager_engine import ReleaseAssessor, ReleaseManagerEngine
from roboco.services.release_readiness import (
    BumpKind,
    Gap,
    ReleaseReadinessReport,
    report_from_dict,
    report_to_dict,
)
from roboco.services.task import RELEASE_MANAGER_SOURCE, TaskService, get_task_service
from roboco.services.workspace import WorkspaceService
from roboco.utils.crypto import encrypt_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import QueuePool

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
SLUG = "roboco"
ONE = 1
MIN_COMMITS = 8
_VERSION = "0.13.0"


@pytest_asyncio.fixture
async def db_session(_test_database_url: str) -> AsyncIterator[AsyncSession]:
    """Savepoint-isolated override, shadowing tests/conftest.py's plain-
    rollback fixture for THIS module only (pytest resolves a module-level
    fixture over a conftest.py one of the same name).

    ``ReleaseManagerEngine.run_cycle`` now commits mid-cycle to release the
    pool connection before the readiness assessment (read-clone resolve,
    git subprocess calls, and a CI HTTP call, the 2026-07-29 pool-exhaustion
    fix). A plain rollback-at-teardown only undoes UNCOMMITTED state, so
    that mid-test commit would otherwise leak rows into the shared
    session-scoped test database. Mirrors
    tests/integration/services/conftest.py's fixture exactly: nests the
    test in one real transaction and gives the session a SAVEPOINT
    (``join_transaction_mode="create_savepoint"``); every commit under
    test only ends the savepoint, and the real transaction is what rolls
    back at teardown.
    """
    engine = create_async_engine(_test_database_url, future=True)
    async with engine.connect() as connection:
        await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()
        await connection.rollback()
    await engine.dispose()


def _report(
    *,
    bump: BumpKind = "minor",
    gate: str = "green",
    kind: str = "feat",
    n_commits: int = 10,
    gaps: list[Gap] | None = None,
) -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind=bump,
        change_summary=[f"{kind}: change {i}" for i in range(n_commits)],
        drafted_changelog=f"## [{_VERSION}] - 2026-06-25\n\n### Added\n- stuff (#1)\n",
        version_bump_plan=["pyproject.toml"],
        gaps=gaps or [],
        migration_notes=[],
        gate_state=gate,
    )


def _assessor(report: ReleaseReadinessReport | None) -> ReleaseAssessor:
    async def _a() -> ReleaseReadinessReport | None:
        return report

    return _a


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY, None),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            git_token_encrypted=encrypt_token("ghp_fake_test_token"),
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(cfg, "release_manager_enabled", True)
    monkeypatch.setattr(cfg, "release_min_commits", MIN_COMMITS)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)


def test_report_dict_round_trip() -> None:
    report = _report(gaps=[Gap("gate", "x"), Gap("changelog", "y")])
    assert report_from_dict(report_to_dict(report)) == report


@pytest.mark.asyncio
async def test_disabled_creates_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "release_manager_enabled", False)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_below_threshold_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    # Patch bump + few fix commits + no security → below the threshold.
    report = _report(bump="patch", kind="fix", n_commits=2)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(report))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_red_gate_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report(gate="red")))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


@pytest.mark.asyncio
async def test_proposes_held_proposal_past_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    task = await engine.run_cycle()
    assert task is not None

    open_proposals = await get_task_service(db_session).list_open_release_proposals()
    assert len(open_proposals) == ONE
    proposal = open_proposals[0]
    assert proposal.status == TS.PENDING
    assert proposal.confirmed_by_human is False  # HELD for the CEO, not dispatched
    assert proposal.assigned_to == SECRETARY_UUID
    assert proposal.source == RELEASE_MANAGER_SOURCE
    assert "0.13.0" in proposal.title
    stored = markers.get_release_report(proposal)
    assert stored is not None
    assert report_from_dict(stored).proposed_version == "0.13.0"


@pytest.mark.asyncio
async def test_security_only_patch_still_proposes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    # One security fix (patch bump, below the commit floor) still warrants a release.
    report = _report(bump="patch", kind="security", n_commits=1)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(report))
    assert await engine.run_cycle() is not None


@pytest.mark.asyncio
async def test_dedupe_one_open_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    assert len(await get_task_service(db_session).list_open_release_proposals()) == ONE


@pytest.mark.asyncio
async def test_loop_never_publishes_or_approves(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    approve = AsyncMock()
    ceo_approve = AsyncMock()
    monkeypatch.setattr(TaskService, "approve_and_start", approve)
    monkeypatch.setattr(TaskService, "ceo_approve", ceo_approve)
    await ReleaseManagerEngine(db_session, assessor=_assessor(_report())).run_cycle()
    approve.assert_not_awaited()
    ceo_approve.assert_not_awaited()
    proposals = await get_task_service(db_session).list_open_release_proposals()
    assert proposals[0].status == TS.PENDING  # never advanced by the loop


@pytest.mark.asyncio
async def test_proposes_sends_telegram_push(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly-originated release proposal fires the styled push DM
    (release kind, the proposal's id8, its version) alongside the existing
    in-app notification, and names the proposal task as related_task_id so
    the row can resolve once the CEO decides (DEFECT 2)."""
    await _seed(db_session)
    _enable(monkeypatch)
    notify = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.NotificationDeliveryService."
        "notify_ceo_of_queue_item",
        notify,
    )
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    task = await engine.run_cycle()
    assert task is not None
    notify.assert_awaited_once()
    assert notify.await_args is not None
    _args, kwargs = notify.await_args
    assert kwargs["kind"] == "release"
    assert kwargs["id8"] == str(task.id)[:8]
    assert _VERSION in kwargs["title"]
    assert kwargs["related_task_id"] == task.id


@pytest.mark.asyncio
async def test_proposes_does_not_double_notify_via_send_ack_notification(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT 3 regression: _notify_ceo used to ALSO fire a separate
    NotificationService.send_ack_notification ALERT for the same event
    (from_agent="system", distinct from notify_ceo_of_queue_item's own
    CEO-self-addressed row): two pending-ack rows per proposal, with no
    dedup path merging them since the sender differed. Only the queue-item
    push fires now."""
    await _seed(db_session)
    _enable(monkeypatch)
    send_ack = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification.NotificationService.send_ack_notification",
        send_ack,
    )
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    task = await engine.run_cycle()
    assert task is not None
    send_ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_proposes_survives_telegram_push_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Telegram send failure must never block origination itself."""
    await _seed(db_session)
    _enable(monkeypatch)
    monkeypatch.setattr(
        "roboco.services.notification_delivery.NotificationDeliveryService."
        "notify_ceo_of_queue_item",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(_report()))
    task = await engine.run_cycle()
    assert task is not None
    assert await get_task_service(db_session).list_open_release_proposals()


@pytest.mark.asyncio
async def test_run_cycle_releases_pool_before_assessment(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_cycle commits (releasing the pool connection) before calling the
    assessor - the assessor does a read-clone resolve/fetch, git subprocess
    calls, and a CI status HTTP call that must not hold a checked-out
    connection (2026-07-29 pool-exhaustion incident class). Without the
    release, commit is never called before the assessor runs (the preceding
    work is a read-only SELECT), so this fails without the fix."""
    await _seed(db_session)
    _enable(monkeypatch)
    order: list[str] = []
    real_commit = db_session.commit

    async def _tracked_commit() -> None:
        order.append("commit")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", _tracked_commit)

    async def _assessor() -> ReleaseReadinessReport | None:
        order.append("assess")
        return _report()

    engine = ReleaseManagerEngine(db_session, assessor=_assessor)
    await engine.run_cycle()
    assert "commit" in order
    assert "assess" in order
    assert order.index("commit") < order.index("assess")


def _checkedout(engine: object) -> int:
    """``engine.pool.checkedout()`` - a QueuePool-only method typed on the
    concrete class, not the ``Pool`` base ``AsyncEngine.pool`` is annotated
    with; asyncpg always backs onto AsyncAdaptedQueuePool at runtime. The
    cast's target types are string literals, so no runtime import is needed."""
    return cast("QueuePool", cast("AsyncEngine", engine).pool).checkedout()


def _init_fake_read_clone(workspace: Path) -> None:
    """A minimal real git repo AT workspace (not a new subdir) - stands in
    for what a real clone/fetch would leave behind, without any network
    call."""
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "config", "user.email", "test@example.com")
    (workspace / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (workspace / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "feat: seed")


@pytest.mark.asyncio
async def test_production_assess_releases_pool_before_each_slow_io_call(
    _test_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Real Postgres pool + the REAL _production_assess call path: the
    project resolve, WorkspaceService.ensure_read_clone's own internal DB
    resolve, and GitService's CI-query resolve must each release before
    their own slow git/HTTP work - not just once at run_cycle's top. Before
    the fix, _production_assess's first statement (get_by_slug) and
    ensure_read_clone's own token read each re-check-out a connection and
    hold it straight through the read-clone clone/fetch and the CI HTTP
    call, regardless of the top-level release. Only the true IO boundaries
    (WorkspaceService's clone/fetch subprocess calls, GitService's CI HTTP
    fetch) are faked below; every DB read in between runs for real against
    a real, tightly-capped (size 1) pool."""
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "workspaces_root", str(tmp_path))

    engine_db = create_async_engine(
        _test_database_url, pool_size=1, max_overflow=0, future=True
    )
    factory = async_sessionmaker(
        bind=engine_db, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as seed_session:
            await _seed(seed_session)
            await seed_session.commit()

        checkouts: dict[str, list[int]] = {"clone": [], "ci": []}

        async def _fake_clone_repo(
            _self: object,
            workspace: Path,
            _git_url: str,
            _default_branch: str,
            _git_token: str | None = None,
            **_kwargs: object,  # absorbs the real signature's agent= kwarg
        ) -> None:
            checkouts["clone"].append(_checkedout(engine_db))
            _init_fake_read_clone(workspace)

        def _fake_sync_read_clone(
            _self: object,
            _workspace: Path,
            _git_url: str,
            _default_branch: str,
            _git_token: str | None,
        ) -> None:
            # Already-cloned above; just record the checkout state at this
            # (also real, in production) subprocess boundary.
            checkouts["clone"].append(_checkedout(engine_db))

        async def _fake_fetch_latest_ci_run(
            _self: object, _query: object, _workflow: object, _head_sha: object
        ) -> None:
            checkouts["ci"].append(_checkedout(engine_db))

        monkeypatch.setattr(WorkspaceService, "_clone_repo", _fake_clone_repo)
        monkeypatch.setattr(WorkspaceService, "_sync_read_clone", _fake_sync_read_clone)
        monkeypatch.setattr(
            GitService, "_fetch_latest_ci_run", _fake_fetch_latest_ci_run
        )

        async with factory() as db:
            engine = ReleaseManagerEngine(db)
            report = await engine._production_assess()

        assert checkouts["clone"]  # the clone/fetch boundary actually ran
        assert checkouts["ci"]  # the CI-fetch boundary actually ran
        assert all(c == 0 for c in checkouts["clone"])
        assert all(c == 0 for c in checkouts["ci"])
        assert report is not None
    finally:
        async with factory() as cleanup:
            row = (
                await cleanup.execute(
                    select(ProjectTable).where(ProjectTable.slug == SLUG)
                )
            ).scalar_one_or_none()
            if row is not None:
                await cleanup.delete(row)
                await cleanup.commit()
        await engine_db.dispose()


@pytest.mark.asyncio
async def test_none_assessment_no_proposal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = ReleaseManagerEngine(db_session, assessor=_assessor(None))
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_release_proposals() == []


# --- _production_assess: pass head_sha to the CI gate (M8) ---


def _git(root: Path, *args: str) -> str:
    # core.hooksPath="" bypasses a developer machine's own global git hooks
    # (e.g. a commit-identity guard) for this throwaway scratch repo only -
    # never touches the real repo's hooks or committed history.
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _read_clone_repo(tmp_path: Path) -> Path:
    """A minimal git repo standing in for the read clone with a known HEAD."""
    root = tmp_path / "read-clone"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: seed")
    return root


class _FakeProjectService:
    def __init__(self, project: object) -> None:
        self._project = project

    async def get_by_slug(self, _slug: str) -> object:
        return self._project


class _FakeGitService:
    """Stands in for GitService's resolve_ci_query + get_latest_ci_conclusion_for
    split - a small typed class instead of ``type("GS", (), {...})()``, which
    mypy can't unify across two differently-shaped callables in one dict."""

    def __init__(self, ci_for: Callable[..., Awaitable[dict[str, str] | None]]) -> None:
        self._ci_for = ci_for

    async def resolve_ci_query(self, _slug: str, **_kwargs: object) -> object:
        return object()  # non-None sentinel: get_latest_ci_conclusion_for must run

    async def get_latest_ci_conclusion_for(
        self, _query: object, **kwargs: object
    ) -> dict[str, str] | None:
        return await self._ci_for(**kwargs)


@pytest.mark.asyncio
async def test_production_assess_passes_head_sha_to_ci_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    clone = _read_clone_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    project = type("P", (), {"slug": SLUG, "git_url": "https://x/y.git"})()
    monkeypatch.setattr(
        "roboco.services.release_manager_engine.get_project_service",
        lambda _session: _FakeProjectService(project),
    )
    monkeypatch.setattr(
        "roboco.services.workspace.get_workspace_service",
        lambda _session: type(
            "WS",
            (),
            {"ensure_read_clone": AsyncMock(return_value=clone)},
        )(),
    )
    captured: dict[str, object] = {}

    async def _fake_ci_for(**kwargs: object) -> dict[str, str] | None:
        captured["head_sha"] = kwargs.get("head_sha")
        return {"conclusion": "success", "head_sha": str(kwargs.get("head_sha") or "")}

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: _FakeGitService(_fake_ci_for),
    )

    engine = ReleaseManagerEngine(session=AsyncMock())  # real _production_assess
    report = await engine._production_assess()
    assert report is not None
    assert captured["head_sha"] == head_sha
    assert report.gate_state == "green"


@pytest.mark.asyncio
async def test_production_assess_head_unresolvable_passes_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch)
    # A non-git directory: `git rev-parse HEAD` fails, so head_sha is None.
    bogus = tmp_path / "not-a-repo"
    bogus.mkdir()
    # gather_snapshot reads pyproject.toml + CHANGELOG.md before any git call;
    # populate them so the snapshot build doesn't crash on file reads (git
    # commands run with check=False and silently return "" on a non-repo).
    (bogus / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (bogus / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")

    project = type("P", (), {"slug": SLUG, "git_url": "https://x/y.git"})()
    monkeypatch.setattr(
        "roboco.services.release_manager_engine.get_project_service",
        lambda _session: _FakeProjectService(project),
    )
    monkeypatch.setattr(
        "roboco.services.workspace.get_workspace_service",
        lambda _session: type(
            "WS",
            (),
            {"ensure_read_clone": AsyncMock(return_value=bogus)},
        )(),
    )
    captured: dict[str, object] = {}

    async def _fake_ci_for(**kwargs: object) -> dict[str, str] | None:
        captured["head_sha"] = kwargs.get("head_sha")
        return None  # no CI signal → unknown gate

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: _FakeGitService(_fake_ci_for),
    )

    engine = ReleaseManagerEngine(session=AsyncMock())
    report = await engine._production_assess()
    # head_sha=None flowed through; gate is unknown (a gap, no proposal).
    assert captured["head_sha"] is None
    assert report is not None
    assert report.gate_state == "unknown"
