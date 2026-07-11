"""VideoEngine coverage: authoring-task origination + held-draft materialization.

Mirrors the X-engine tests: flag-gated, dedup, rolling open-cap, and a
deterministic ux-dev balance. The authoring task (source=video) is a normal
ASSIGNED delivery task — never held; the post draft (source=video_post) is
Secretary-owned and held for the CEO. Asserted against a real Postgres DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskStatus as TS
from roboco.services import video_engine as video_engine_module
from roboco.services.company_goals import get_company_goals_service
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE, get_task_service
from sqlalchemy import delete, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
UX_DEV_1_UUID = _foundation.AGENTS["ux-dev-1"].uuid
UX_DEV_2_UUID = _foundation.AGENTS["ux-dev-2"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2
THREE = 3


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY, None),
        (UX_DEV_1_UUID, "ux-dev-1", AgentRole.DEVELOPER, Team.UX_UI),
        (UX_DEV_2_UUID, "ux-dev-2", AgentRole.DEVELOPER, Team.UX_UI),
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
    existing = await session.execute(
        select(ProjectTable).where(ProjectTable.slug == SLUG)
    )
    if existing.scalar_one_or_none() is None:
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
                video_engine_enabled=True,
            )
        )
    await session.flush()


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "video_max_open_posts", 5)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)


def _mock_local_model(monkeypatch: pytest.MonkeyPatch, reply: str | None) -> AsyncMock:
    mock = AsyncMock(return_value=reply)
    monkeypatch.setattr(video_engine_module, "_chat", mock)
    return mock


# --------------------------------------------------------------------------- #
# open_video_task
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_opens_no_video_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="release v1.0.0", script="script", platforms=["x"], brief="brief"
    )
    assert task is None
    assert await get_task_service(db_session).list_open_video_posts() == []


@pytest.mark.asyncio
async def test_open_video_task_creates_assigned_authoring_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="release v1.0.0",
        script="Here's what shipped",
        platforms=["x", "tiktok"],
        brief="Announce the release",
    )
    assert task is not None
    assert task.team == Team.UX_UI
    assert task.assigned_to == UX_DEV_1_UUID  # deterministic first pick
    assert task.source == VIDEO_SOURCE
    assert task.status == TS.PENDING
    assert task.confirmed_by_human is True  # normal delivery task, not CEO-held
    # LOW so it clears _check_dev_needs_subtasks; a medium/high root dev task
    # would auto-block for subtasks it never owns and deadlock.
    assert task.estimated_complexity == Complexity.LOW
    assert task.acceptance_criteria  # non-empty
    # Third AC line: composition follows the design bar / demo-kit register.
    assert len(task.acceptance_criteria) == THREE
    assert "motion/README.md" in task.acceptance_criteria[2]
    assert "panel-demo" in task.acceptance_criteria[2]
    project = await db_session.get(ProjectTable, task.project_id)
    assert project is not None
    assert project.slug == SLUG
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["occasion"] == "release v1.0.0"
    assert draft["script"] == "Here's what shipped"
    assert draft["platforms"] == ["x", "tiktok"]
    # brief is enriched: raw content + motion design-bar pointer appended.
    assert draft["brief"].startswith("Announce the release")
    assert "motion/README.md" in draft["brief"]
    assert "motion/kit/README.md" in draft["brief"]
    assert "compositions/panel-demo/" in draft["brief"]
    assert "Brand voice" not in draft["brief"]  # unset -> omitted
    assert draft["suggested_input_props"] == {}  # none supplied
    assert task.description == draft["brief"]


@pytest.mark.asyncio
async def test_open_video_task_balances_across_ux_devs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    first = await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    second = await engine.open_video_task(
        occasion="release v2.0.0", script="s", platforms=["x"], brief="b"
    )
    assert first is not None
    assert second is not None
    assert first.assigned_to == UX_DEV_1_UUID
    assert second.assigned_to == UX_DEV_2_UUID  # balanced onto the less-loaded dev


@pytest.mark.asyncio
async def test_open_video_task_dedupes_same_occasion(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    second = await engine.open_video_task(
        occasion="release v1.0.0", script="s2", platforms=["x"], brief="b2"
    )
    assert second is None
    open_tasks = await get_task_service(db_session).list_open_video_posts()
    assert len(open_tasks) == ONE


@pytest.mark.asyncio
async def test_open_video_task_respects_open_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_max_open_posts=1)
    engine = video_engine_module.VideoEngine(db_session)
    await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    second = await engine.open_video_task(
        occasion="release v2.0.0", script="s", platforms=["x"], brief="b"
    )
    assert second is None
    open_tasks = await get_task_service(db_session).list_open_video_posts()
    assert len(open_tasks) == ONE


@pytest.mark.asyncio
async def test_open_video_task_unresolvable_project_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    assert task is None
    assert await get_task_service(db_session).list_open_video_posts() == []


@pytest.mark.asyncio
async def test_open_video_task_no_op_when_project_not_opted_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    # Flip the per-project opt-in back off — the global flag stays on.
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.video_engine_enabled = False
    await db_session.flush()
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    assert task is None
    assert await get_task_service(db_session).list_open_video_posts() == []


@pytest.mark.asyncio
async def test_open_video_task_insert_error_returns_none_session_usable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F042 guard: a real DBAPI error on the authoring insert (assignee FK
    absent) rolls back ONLY the savepoint and returns None; the shared session
    stays usable, so a caller's later commit is not poisoned."""
    if await db_session.get(AgentTable, SYSTEM_UUID) is None:
        db_session.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="system",
                slug="system",
                role=AgentRole.SYSTEM,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    # ux-devs deliberately absent -> the assigned_to FK violates at flush.
    await db_session.execute(
        delete(AgentTable).where(AgentTable.id.in_([UX_DEV_1_UUID, UX_DEV_2_UUID]))
    )
    has_project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one_or_none()
    if has_project is None:
        db_session.add(
            ProjectTable(
                name="RoboCo",
                slug=SLUG,
                git_url="https://github.com/x/roboco.git",
                default_branch="master",
                protected_branches=["master"],
                assigned_cell=Team.BACKEND,
                created_by=SYSTEM_UUID,
                is_active=True,
                video_engine_enabled=True,
            )
        )
    await db_session.flush()
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="fk-fail", script="s", platforms=["x"], brief="b"
    )
    assert task is None
    # Not poisoned: a follow-up statement runs cleanly (this raised
    # PendingRollbackError before the savepoint fix).
    check = await db_session.execute(
        select(ProjectTable).where(ProjectTable.slug == SLUG)
    )
    assert check.scalar_one_or_none() is not None


# --------------------------------------------------------------------------- #
# open_video_task(project_id=...) — the on-demand caller's own project scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_video_task_with_explicit_project_id_ignores_self_heal_slug(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-supplied project_id resolves independent of
    self_heal_project_slug — the authoring task lands on THAT project, not
    the fixed RoboCo one."""
    await _seed(db_session)
    _enable(monkeypatch)
    other = ProjectTable(
        name="Other Repo",
        slug="other-repo",
        git_url="https://github.com/x/other.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        video_engine_enabled=True,
    )
    db_session.add(other)
    await db_session.flush()
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-slug")
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="on-demand other repo",
        script="s",
        platforms=["x"],
        brief="b",
        project_id=cast("UUID", other.id),
    )
    assert task is not None
    assert task.project_id == other.id


@pytest.mark.asyncio
async def test_open_video_task_explicit_project_id_not_opted_in_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    other = ProjectTable(
        name="Not Opted In",
        slug="not-opted-in",
        git_url="https://github.com/x/notopted.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        video_engine_enabled=False,
    )
    db_session.add(other)
    await db_session.flush()
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="on-demand not opted",
        script="s",
        platforms=["x"],
        brief="b",
        project_id=cast("UUID", other.id),
    )
    assert task is None


@pytest.mark.asyncio
async def test_open_video_task_explicit_project_id_unresolvable_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="on-demand missing project",
        script="s",
        platforms=["x"],
        brief="b",
        project_id=uuid4(),
    )
    assert task is None


# --------------------------------------------------------------------------- #
# _originate_video_post
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_originate_video_post_holds_draft_for_secretary(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    source_task = await engine.open_video_task(
        occasion="release v1.0.0",
        script="Here's what shipped",
        platforms=["x", "tiktok"],
        brief="Announce the release",
    )
    assert source_task is not None

    draft_task = await engine._originate_video_post(
        source_task=source_task,
        mp4_paths={
            "vertical": "/render/out/a-vertical.mp4",
            "square": "/render/out/a-square.mp4",
        },
        captions={
            "x": "We just shipped v1.0.0!",
            "tiktok": "New release, check it out",
        },
        platforms=["x", "tiktok"],
    )

    assert draft_task.team == Team.MAIN_PM
    assert draft_task.assigned_to == SECRETARY_UUID
    assert draft_task.source == VIDEO_POST_SOURCE
    assert draft_task.status == TS.PENDING
    assert draft_task.confirmed_by_human is False  # HELD; never dispatched

    draft = markers.get_video_draft(draft_task)
    assert draft is not None
    assert draft["occasion"] == "release v1.0.0"  # carried forward from the source
    assert draft["script"] == "Here's what shipped"
    assert draft["mp4_paths"] == {
        "vertical": "/render/out/a-vertical.mp4",
        "square": "/render/out/a-square.mp4",
    }
    assert draft["x_caption"] == "We just shipped v1.0.0!"
    assert draft["tiktok_caption"] == "New release, check it out"
    assert draft["platforms"] == ["x", "tiktok"]
    assert draft["render_status"] == "rendered"
    assert draft["source_task_id"] == str(source_task.id)  # traceability back-ref


@pytest.mark.asyncio
async def test_originate_video_post_not_counted_by_dedupe_against_new_occasion(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A materialized post draft still occupies the shared open-cap/dedupe
    pool (list_open_video_posts spans both sources) — a fresh occasion is
    unaffected, but the cap still counts it."""
    await _seed(db_session)
    _enable(monkeypatch, video_max_open_posts=2)
    engine = video_engine_module.VideoEngine(db_session)
    source_task = await engine.open_video_task(
        occasion="release v1.0.0", script="s", platforms=["x"], brief="b"
    )
    assert source_task is not None
    source_task.status = TS.COMPLETED
    await db_session.flush()
    await engine._originate_video_post(
        source_task=source_task,
        mp4_paths={"vertical": "a.mp4", "square": "b.mp4"},
        captions={"x": "caption"},
        platforms=["x"],
    )
    # One open (the held draft; the source authoring task is now COMPLETED and
    # therefore excluded) plus room for exactly one more before the cap bites.
    open_tasks = await get_task_service(db_session).list_open_video_posts()
    assert len(open_tasks) == ONE

    second = await engine.open_video_task(
        occasion="release v2.0.0", script="s", platforms=["x"], brief="b"
    )
    assert second is not None
    open_tasks = await get_task_service(db_session).list_open_video_posts()
    assert len(open_tasks) == TWO


# --------------------------------------------------------------------------- #
# draft_release_video
# --------------------------------------------------------------------------- #

_CHANGELOG = "## [1.0.0]\n\n### Added\n- a huge new release\n"


@pytest.mark.asyncio
async def test_draft_release_video_disabled_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert task is None
    assert await get_task_service(db_session).list_open_video_posts() == []


@pytest.mark.asyncio
async def test_draft_release_video_sub_switch_off_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=False)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert task is None
    assert await get_task_service(db_session).list_open_video_posts() == []


@pytest.mark.asyncio
async def test_draft_release_video_opens_authoring_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    _mock_local_model(monkeypatch, "RoboCo v1.0.0 just shipped a huge release.")
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert task is not None
    assert task.source == VIDEO_SOURCE
    assert task.team == Team.UX_UI
    assert task.confirmed_by_human is True
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["occasion"] == "release 1.0.0"
    assert draft["platforms"] == ["x", "tiktok"]
    assert draft["script"] == "RoboCo v1.0.0 just shipped a huge release."
    # brief is now the structured changelog block, not the LLM one-liner —
    # the whole CHANGELOG section (not one bullet) plus a highlights list.
    assert draft["brief"] != draft["script"]
    assert _CHANGELOG.strip() in draft["brief"]
    assert "a huge new release" in draft["brief"]
    assert "motion/README.md" in draft["brief"]
    assert draft["suggested_input_props"] == {
        "version": "1.0.0",
        "highlights": ["a huge new release"],
    }


@pytest.mark.asyncio
async def test_draft_release_video_falls_back_to_template_on_local_model_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    mock = AsyncMock(side_effect=RuntimeError("ollama down"))
    monkeypatch.setattr(video_engine_module, "_chat", mock)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert "1.0.0" in draft["script"]
    assert "a huge new release" in draft["script"]
    # The structured brief is built independent of the local model, so a
    # model outage still produces the full changelog-derived brief.
    assert _CHANGELOG.strip() in draft["brief"]
    assert draft["suggested_input_props"]["highlights"] == ["a huge new release"]


@pytest.mark.asyncio
async def test_draft_release_video_falls_back_to_template_on_empty_local_reply(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    _mock_local_model(monkeypatch, None)  # non-success / empty local-model reply
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="2.0.0", changelog="- no bullets")
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["script"] == "RoboCo v2.0.0 just shipped: no bullets."
    assert "- no bullets" in draft["brief"]
    assert draft["suggested_input_props"] == {
        "version": "2.0.0",
        "highlights": ["no bullets"],
    }


@pytest.mark.asyncio
async def test_draft_release_video_brief_contains_brand_voice_when_set(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    await get_company_goals_service(db_session).upsert(
        {"brand_voice": "Dry wit, never an exclamation point."}
    )
    _mock_local_model(monkeypatch, "shipped!")
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert "Dry wit, never an exclamation point." in draft["brief"]


# --------------------------------------------------------------------------- #
# brief enrichment shared by every occasion — brand voice + motion pointer
# (spec Task 2: "Feed the video brief")
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_video_task_brief_carries_motion_pointer_no_brand_voice_by_default(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates the on-demand (`POST /video/request`) caller, which passes
    its brief through verbatim — enrichment must land centrally here since
    that route can't append it itself."""
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="on-demand demo",
        script="script",
        platforms=["x"],
        brief="CEO's on-demand brief, verbatim.",
    )
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["brief"].startswith("CEO's on-demand brief, verbatim.")
    assert "motion/README.md" in draft["brief"]
    assert "motion/kit/README.md" in draft["brief"]
    assert "compositions/panel-demo/" in draft["brief"]
    assert "Brand voice" not in draft["brief"]  # unset -> omitted
    assert task.description == draft["brief"]


@pytest.mark.asyncio
async def test_open_video_task_brief_includes_brand_voice_when_set(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    await get_company_goals_service(db_session).upsert(
        {"brand_voice": "Dry wit, never an exclamation point."}
    )
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="on-demand demo 2",
        script="script",
        platforms=["x"],
        brief="CEO's on-demand brief.",
    )
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert "Dry wit, never an exclamation point." in draft["brief"]


@pytest.mark.asyncio
async def test_open_video_task_does_not_compress_spotlight_brief(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors content_actions._open_spotlight_video's `f"{title}: {body}"`
    shape — the spotlight body is already rich; enrichment only appends,
    never truncates or rewrites the original content."""
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    feature_brief = (
        "Organizational Memory Loop: Did you know RoboCo agents learn from "
        "every completed task? A local model distills one high-signal lesson "
        "per task, retrieved back into future briefings."
    )
    task = await engine.open_video_task(
        occasion="spotlight org-memory",
        script=feature_brief,
        platforms=["x", "tiktok"],
        brief=feature_brief,
    )
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["brief"].startswith(feature_brief)  # not truncated/compressed
    assert "motion/README.md" in draft["brief"]


@pytest.mark.asyncio
async def test_open_video_task_no_suggested_input_props_defaults_empty(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="spotlight x", script="s", platforms=["x"], brief="b"
    )
    assert task is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["suggested_input_props"] == {}


@pytest.mark.asyncio
async def test_open_video_task_acceptance_criteria_has_design_bar_line(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="release v9.9.9", script="s", platforms=["x"], brief="b"
    )
    assert task is not None
    assert len(task.acceptance_criteria) == THREE
    assert "motion/README.md" in task.acceptance_criteria[2]
    assert "panel-demo" in task.acceptance_criteria[2]


@pytest.mark.asyncio
async def test_draft_release_video_dedupes_same_version(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch, video_on_release=True)
    _mock_local_model(monkeypatch, "shipped!")
    engine = video_engine_module.VideoEngine(db_session)
    first = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    second = await engine.draft_release_video(version="1.0.0", changelog=_CHANGELOG)
    assert first is not None
    assert second is None


# --------------------------------------------------------------------------- #
# rerender — CEO-triggered clear of the render idempotency keys
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rerender_clears_render_state_keeps_the_rest(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="rerender-me", script="s", platforms=["x"], brief="b"
    )
    assert task is not None
    draft = markers.get_video_draft(task) or {}
    markers.set_video_draft(
        task,
        {
            **draft,
            "composition_id": "Intro",
            "render_status": "failed",
            "render_attempts": THREE,
            "render_error": "sidecar timeout",
        },
    )
    task.status = TS.COMPLETED
    await db_session.flush()

    result = await engine.rerender(cast("UUID", task.id))
    assert result is not None
    cleared = markers.get_video_draft(result)
    assert cleared is not None
    assert "render_status" not in cleared
    assert "render_attempts" not in cleared
    assert "render_error" not in cleared
    assert cleared["composition_id"] == "Intro"  # everything else preserved


@pytest.mark.asyncio
async def test_rerender_none_for_non_completed_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="not-yet-done", script="s", platforms=["x"], brief="b"
    )
    assert task is not None
    draft = markers.get_video_draft(task) or {}
    markers.set_video_draft(task, {**draft, "composition_id": "Intro"})
    await db_session.flush()  # still PENDING, not COMPLETED

    result = await engine.rerender(cast("UUID", task.id))
    assert result is None


@pytest.mark.asyncio
async def test_rerender_none_without_composition_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = video_engine_module.VideoEngine(db_session)
    task = await engine.open_video_task(
        occasion="no-composition-yet", script="s", platforms=["x"], brief="b"
    )
    assert task is not None
    task.status = TS.COMPLETED
    await db_session.flush()  # no propose_video call yet -> no composition_id

    result = await engine.rerender(cast("UUID", task.id))
    assert result is None


@pytest.mark.asyncio
async def test_rerender_none_for_missing_task(db_session: AsyncSession) -> None:
    engine = video_engine_module.VideoEngine(db_session)
    result = await engine.rerender(uuid4())
    assert result is None
    open_tasks = await get_task_service(db_session).list_open_video_posts()
    assert open_tasks == []
