"""Scenario: the video-generation pipeline, render loop through CEO approval.

Regression coverage for the video engine's cross-layer wiring (Phase H): a
completed ``source=video`` authoring task is rendered by the orchestrator's
render loop into a held ``source=video_post`` draft, which the CEO approves
through ``VideoPostService``. Exercises the REAL dispatcher skip-predicates
(``video_post`` must never reach a dev/PM dispatcher — the authoring source
itself is the contrast case, since it dispatches normally), the REAL
``propose_video`` do-tool via the REAL do_server registry (mirrors
``test_feature_spotlight.py``'s guard against a verb wired at
role_config/content_actions but dropped from ``do_server._TOOLS``), the REAL
render -> materialize chain (only the remotion-renderer sidecar client + the
workspace read-clone are mocked — the external-I/O boundary), and the REAL
``VideoPostService.approve`` (only the X-v2 + TikTok posters are mocked)
including its already-posted idempotency.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from roboco.runtime.orchestrator import _is_held_ceo_source, _is_non_dev_dispatch_source
from roboco.services.heartbeat_mutex import HeartbeatMutex
from roboco.services.video_post_service import (
    TikTokPoster,
    TikTokUploadResult,
    XVideoPoster,
    XVideoPostResult,
)
from tests.e2e_smoke.arcs import seed_company, seed_project, seed_task
from tests.e2e_smoke.harness import ScriptedAgent, expect_error

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack


def _seed_video_agents(stack: E2EStack) -> None:
    """Seed ``system`` / ``secretary-1`` / ``ux-dev-1`` / ``ux-dev-2`` at their
    FIXED foundation UUIDs — the video engine writes ``created_by`` /
    ``assigned_to`` straight from the static identity registry (not a
    role-keyed DB lookup), so those exact ids must exist as real agent rows
    for the FK to resolve. Idempotent (safe if ever called more than once
    against the same stack), mirroring
    ``test_feature_spotlight._seed_system_and_secretary``.
    """
    from roboco.db.tables import AgentTable
    from roboco.foundation import identity as _foundation
    from roboco.models import AgentRole, AgentStatus, Team

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["secretary-1"].uuid,
                "secretary-1",
                AgentRole.SECRETARY,
                None,
            ),
            (
                _foundation.AGENTS["ux-dev-1"].uuid,
                "ux-dev-1",
                AgentRole.DEVELOPER,
                Team.UX_UI,
            ),
            (
                _foundation.AGENTS["ux-dev-2"].uuid,
                "ux-dev-2",
                AgentRole.DEVELOPER,
                Team.UX_UI,
            ),
        ):
            if await session.get(AgentTable, agent_uuid) is not None:
                continue
            session.add(
                AgentTable(
                    id=agent_uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

    stack.run_db(_run)


def _seed_completed_authoring_task(stack: E2EStack, project_id: Any) -> UUID:
    """A completed ``source=video`` authoring task carrying a proposed
    composition — the render loop's scan basis. Mirrors the shape a real
    ``VideoEngine.open_video_task`` + ``propose_video`` call would leave
    behind, seeded directly (the harness's own convention for mid-flight
    setup — see ``arcs.seed_hierarchy``); the render/approve/gate wiring
    under test doesn't depend on how the authoring task got here.
    """
    from roboco.foundation import identity as _foundation
    from roboco.foundation.policy.content import markers as _markers
    from roboco.models import Team
    from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
    from roboco.services.task import VIDEO_SOURCE

    draft: dict[str, Any] = {
        "occasion": "e2e pipeline test",
        "script": "Here's what shipped",
        "brief": "Announce the e2e video pipeline",
        "composition_id": "Intro",
        "input_props": {"title": "hello"},
        "x_caption": "Check out our new release!",
        "tiktok_caption": "New release, check it out",
        "platforms": ["x", "tiktok"],
    }
    task_id: UUID = seed_task(
        stack,
        title="Video: e2e pipeline test",
        description="Announce the e2e video pipeline",
        acceptance_criteria=["Both 9:16 and 1:1 cuts render"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.LOW,
        team=Team.UX_UI,
        project_id=project_id,
        created_by=_foundation.AGENTS["system"].uuid,
        assigned_to=_foundation.AGENTS["ux-dev-1"].uuid,
        status=TaskStatus.COMPLETED,
        source=VIDEO_SOURCE,
        confirmed_by_human=True,
        orchestration_markers={_markers.VIDEO_DRAFT: draft},
    )
    return task_id


class _FakeRenderer:
    """Stands in for the remotion-renderer sidecar: returns a deterministic
    path per orientation, no tar/HTTP anywhere."""

    async def render(
        self,
        *,
        source_dir: str,
        composition_id: str,
        input_props: dict[str, Any],
        orientation: str,
        render_key: str,
    ) -> str:
        _ = (source_dir, input_props)
        return f"/fake-out/{render_key}-{composition_id}-{orientation}.mp4"


def _render_completed_task(stack: E2EStack, task_id: UUID) -> None:
    """Drive the REAL orchestrator render step against the completed
    authoring task — only the sidecar client + workspace read-clone are
    mocked (the render step's external-I/O boundary)."""
    from pathlib import Path as _Path

    from roboco.db.tables import TaskTable
    from roboco.runtime.orchestrator import AgentOrchestrator
    from sqlalchemy import select

    workspace = SimpleNamespace(
        ensure_read_clone=AsyncMock(return_value=_Path("/fake-clone"))
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    async def _run(session: AsyncSession) -> None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        with (
            patch(
                "roboco.services.video_renderer_client.get_video_renderer",
                _FakeRenderer,
            ),
            patch(
                "roboco.services.workspace.get_workspace_service",
                lambda _db: workspace,
            ),
        ):
            await orch._render_video_task(session, row)

    stack.run_db(_run)


def _task_dict(stack: E2EStack, task_id: UUID) -> dict[str, Any]:
    """The (source, confirmed_by_human) shape a dispatcher reads off a task
    — real committed values, not a hand-crafted stand-in."""
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        return {
            "source": row.source,
            "confirmed_by_human": row.confirmed_by_human,
            "status": str(row.status),
        }

    result: dict[str, Any] = stack.run_db(_run)
    return result


def _find_video_post_draft(stack: E2EStack, source_task_id: UUID) -> dict[str, Any]:
    """The held video_post draft the render step materialized for
    ``source_task_id`` — located via the marker's own back-reference
    (``_originate_video_post`` stamps ``source_task_id``), robust against any
    other video_post rows in this session-scoped shared test DB."""
    from roboco.foundation.policy.content import markers as _markers
    from roboco.services.task import get_task_service

    async def _run(session: AsyncSession) -> dict[str, Any]:
        drafts = await get_task_service(session).list_open_video_post_drafts()
        match = next(
            t
            for t in drafts
            if (_markers.get_video_draft(t) or {}).get("source_task_id")
            == str(source_task_id)
        )
        draft = _markers.get_video_draft(match) or {}
        return {
            "id": match.id,
            "source": match.source,
            "confirmed_by_human": match.confirmed_by_human,
            "status": str(match.status),
            "mp4_paths": dict(draft.get("mp4_paths") or {}),
        }

    result: dict[str, Any] = stack.run_db(_run)
    return result


class _FakeXPoster(XVideoPoster):
    @property
    def configured(self) -> bool:
        return True

    async def post_video(self, *, mp4_path: str, caption: str) -> XVideoPostResult:
        _ = (mp4_path, caption)
        return XVideoPostResult(posted=True, video_id="e2e-x-vid", detail="posted")


class _FakeTikTokPoster(TikTokPoster):
    @property
    def configured(self) -> bool:
        return True

    async def upload_to_inbox(
        self, *, mp4_path: str, caption: str
    ) -> TikTokUploadResult:
        _ = (mp4_path, caption)
        return TikTokUploadResult(
            uploaded=True, publish_id="e2e-tt-pub", detail="uploaded"
        )


# No real Redis in this harness (and the root conftest's autouse fixture
# points settings.redis_url at an unreachable port for every test regardless)
# — mocked the same way tests/integration/test_video_routes.py does.
_LOCKED = (
    patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value="e2e-lock-token")),
    patch.object(HeartbeatMutex, "release", AsyncMock(return_value=None)),
)


def _approve(stack: E2EStack, draft_id: UUID) -> dict[str, Any]:
    from roboco.services.video_post_service import get_video_post_service

    async def _run(session: AsyncSession) -> dict[str, Any]:
        svc = get_video_post_service(
            session, x_poster=_FakeXPoster(), tiktok_poster=_FakeTikTokPoster()
        )
        result = await svc.approve(draft_id)
        assert result is not None
        return {"status": result.status, "posted": dict(result.posted)}

    with _LOCKED[0], _LOCKED[1]:
        outcome: dict[str, Any] = stack.run_db(_run)
    return outcome


def test_video_pipeline_render_and_approve(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company: Company = seed_company(stack)
    _seed_video_agents(stack)
    project_id, _project_slug = seed_project(stack, company)

    task_id = _seed_completed_authoring_task(stack, project_id)

    # The exact bug class test_feature_spotlight.py guards against: a verb
    # granted (role_config) + implemented (ContentActions) + routed
    # (api/v1/do) but missing from do_server's _TOOLS/_REGISTERED_TOOLS is
    # silently uncallable over MCP no matter what the gateway layers say.
    dev = ScriptedAgent(stack, company.dev_id, "be-dev-1", "developer")
    do_module = dev._module("roboco.mcp.do_server")
    assert "propose_video" in do_module._TOOLS, (
        "propose_video missing from do_server._TOOLS — no role could ever "
        "call it over MCP"
    )
    assert "propose_video" in do_module._REGISTERED_TOOLS, (
        "propose_video is granted to developer in role_config but absent "
        "from this agent's _register_tools() output"
    )

    # propose_video is granted to every developer role (be/fe/ux-dev share
    # Role.DEVELOPER) — the runtime TEAM gate is the real enforcement, so a
    # be-dev's call must be rejected even though the tool is on their manifest.
    env = dev.do(
        "propose_video",
        composition_id="Intro",
        x_caption="Check it out",
        tiktok_caption="Check it out on TikTok",
        platforms=["x"],
    )
    expect_error(env, "not_authorized", "be-dev propose_video team gate")

    # Contrast case: the authoring task's own source is normal delivery work
    # (confirmed_by_human=True) — neither dispatcher treats it as held.
    before = _task_dict(stack, task_id)
    assert before["status"] == "completed", before
    assert _is_non_dev_dispatch_source(before) is False, before
    assert _is_held_ceo_source(before) is False, before

    # The render loop: only the sidecar client + workspace read-clone mocked.
    _render_completed_task(stack, task_id)

    draft = _find_video_post_draft(stack, task_id)
    assert draft["source"] == "video_post", draft
    assert draft["confirmed_by_human"] is False, draft
    assert draft["status"] == "pending", draft  # held, awaiting the CEO
    assert set(draft["mp4_paths"]) == {"vertical", "square"}, draft

    # The key wiring: video_post is skipped by BOTH dispatchers.
    held_shape = {
        "source": draft["source"],
        "confirmed_by_human": draft["confirmed_by_human"],
    }
    assert _is_non_dev_dispatch_source(held_shape) is True, held_shape
    assert _is_held_ceo_source(held_shape) is True, held_shape

    # VideoPostService.approve posts via mocked X-v2 + TikTok posters, then is
    # idempotent on a second call (no re-post, same ids returned).
    first = _approve(stack, draft["id"])
    assert first["status"] == "posted", first
    assert first["posted"] == {"x": "e2e-x-vid", "tiktok": "e2e-tt-pub"}, first

    second = _approve(stack, draft["id"])
    assert second["status"] == "already_posted", second
    assert second["posted"] == first["posted"], second
