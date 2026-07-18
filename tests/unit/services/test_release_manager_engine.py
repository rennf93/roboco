"""Release-manager engine: propose a CEO-gated release, held + deduped, never publish.

Mirrors the self-heal engine tests. The engine proposes only past the threshold +
green gate, holds the proposal for the CEO (confirmed_by_human=False, owned by the
Secretary, never dispatched), dedupes to one open proposal, and NEVER publishes /
approves — asserted here against a real Postgres DB.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.notification import NotificationService
from roboco.services.release_manager_engine import ReleaseAssessor, ReleaseManagerEngine
from roboco.services.release_readiness import (
    BumpKind,
    Gap,
    ReleaseReadinessReport,
    report_from_dict,
    report_to_dict,
)
from roboco.services.task import RELEASE_MANAGER_SOURCE, TaskService, get_task_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
SLUG = "roboco"
ONE = 1
MIN_COMMITS = 8
_VERSION = "0.13.0"


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
    monkeypatch.setattr(NotificationService, "send_ack_notification", AsyncMock())


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
    in-app notification."""
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
    _args, kwargs = notify.await_args
    assert kwargs["kind"] == "release"
    assert kwargs["id8"] == str(task.id)[:8]
    assert _VERSION in kwargs["title"]


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
    result = subprocess.run(
        ["git", "-C", str(root), *args],
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

    async def _fake_ci(
        _self: object, _slug: str, **_kwargs: object
    ) -> dict[str, str] | None:
        captured["head_sha"] = _kwargs.get("head_sha")
        return {"conclusion": "success", "head_sha": str(_kwargs.get("head_sha") or "")}

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: type("GS", (), {"get_latest_ci_conclusion": _fake_ci})(),
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

    async def _fake_ci(
        _self: object, _slug: str, **_kwargs: object
    ) -> dict[str, str] | None:
        captured["head_sha"] = _kwargs.get("head_sha")
        return None  # no CI signal → unknown gate

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: type("GS", (), {"get_latest_ci_conclusion": _fake_ci})(),
    )

    engine = ReleaseManagerEngine(session=AsyncMock())
    report = await engine._production_assess()
    # head_sha=None flowed through; gate is unknown (a gap, no proposal).
    assert captured["head_sha"] is None
    assert report is not None
    assert report.gate_state == "unknown"
