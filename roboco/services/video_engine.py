"""VideoEngine — open a UX/UI authoring task for a bespoke marketing video,
and materialize the rendered clip as a held CEO-approval draft.

Two task kinds, mirroring the XEngine/ReleaseManagerEngine "originate a
CEO-scoped artifact" shape, but split across the real delivery lifecycle:

* **Authoring task** (``source="video"``) — a normal, ASSIGNED delivery task
  (``confirmed_by_human=True``): the engine hands it straight to a ux-dev, who
  authors a Remotion composition on a real branch through the standard
  claim -> code -> PR -> merge path. NOT held and NOT in any dispatcher's
  skip bucket — it is a pre-assigned code task like any other.
* **Held draft** (``source="video_post"``) — materialized once a render pass
  has produced the MP4s: Secretary-owned, ``confirmed_by_human=False``,
  skipped by every dispatcher, acted on only by an explicit CEO approve.

Default OFF (``video_engine_enabled``); no authoring task is ever opened
while the flag is off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import httpx

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import (
    VIDEO_POST_SOURCE,
    VIDEO_SOURCE,
    TaskCreateRequest,
    get_task_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable

_AUTHORING_ACCEPTANCE_CRITERIA = [
    "Both 9:16 and 1:1 cuts render",
    "Captions within platform limits",
]
_POST_ACCEPTANCE_CRITERIA = ["CEO approves or rejects the draft"]

_CHAT_TIMEOUT_SECONDS = 60.0


async def _chat(prompt: str) -> str | None:
    """One local-LLM chat call (OpenAI-compatible); None on a non-success.

    Mirrors XEngine's identical helper, duplicated locally (rather than
    imported) so video_engine doesn't reach into another service's private
    internals."""
    async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{settings.local_llm_base_url}/chat/completions",
            json={
                "model": settings.local_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
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


def _first_changelog_bullet(changelog: str) -> str:
    """First CHANGELOG bullet's text, or "" if none — the fallback template's
    headline when the local model is unavailable."""
    for line in changelog.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            text = stripped.lstrip("-*").strip()
            if text:
                return text
    return ""


def _fallback_release_script(version: str, changelog: str) -> str:
    highlight = _first_changelog_bullet(changelog)
    lead = f": {highlight}" if highlight else ""
    return f"RoboCo v{version} just shipped{lead}."


def _release_video_prompt(version: str, changelog: str) -> str:
    return (
        "You are RoboCo's marketing team, writing a short voiceover script "
        "for a bespoke motion-graphics video announcing a release. Plain "
        "text, 2-3 short sentences, energetic but factual — no invented "
        "facts.\n\n"
        f"Write the script for RoboCo v{version}, based on this CHANGELOG "
        f"entry:\n{changelog[:1000]}\n"
    )


class VideoEngine(BaseService):
    """Open video-authoring tasks (event hooks + on-demand), both gated."""

    service_name = "video_engine"

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    @staticmethod
    def _select_ux_dev(open_tasks: list[TaskTable]) -> UUID:
        """Deterministically balance authoring assignment across the two ux-devs.

        Counts currently-open authoring tasks (``source=video``) per dev from
        the already-fetched open list (no extra query) — the dev with fewer
        open tasks gets the next one. A tie (including zero/zero) goes to
        ux-dev-1, so the choice never depends on dict/set ordering.
        """
        dev1 = _foundation.AGENTS["ux-dev-1"].uuid
        dev2 = _foundation.AGENTS["ux-dev-2"].uuid
        dev1_count = sum(
            1 for t in open_tasks if t.source == VIDEO_SOURCE and t.assigned_to == dev1
        )
        dev2_count = sum(
            1 for t in open_tasks if t.source == VIDEO_SOURCE and t.assigned_to == dev2
        )
        return dev2 if dev2_count < dev1_count else dev1

    # ---- authoring task (event hooks + on-demand) --------------------------

    async def open_video_task(
        self, *, occasion: str, script: str, platforms: list[str], brief: str
    ) -> TaskTable | None:
        """Originate ONE UX/UI authoring task for a bespoke video, or None.

        No-ops when the flag is off, a task for this occasion is already open
        (authoring or held draft), the open cap is reached, or the RoboCo
        project isn't resolvable. The opened task is a normal, ASSIGNED
        delivery task (``source=VIDEO_SOURCE``, ``confirmed_by_human=True``)
        — NOT held — so it dispatches straight to the assigned ux-dev like any
        other pre-assigned code task.
        """
        if not settings.video_engine_enabled:
            return None
        task_svc = get_task_service(self.session)
        open_tasks = await task_svc.list_open_video_posts()
        for existing in open_tasks:
            draft = markers.get_video_draft(existing)
            if draft is not None and draft.get("occasion") == occasion:
                return None  # already drafted for this occasion
        if len(open_tasks) >= settings.video_max_open_posts:
            self.log.warning(
                "video-engine: open cap reached; not opening authoring task",
                occasion=occasion,
            )
            return None
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "video-engine: RoboCo project not resolvable; skipping video task",
                occasion=occasion,
            )
            return None
        assignee = self._select_ux_dev(open_tasks)
        task = await task_svc.create(
            TaskCreateRequest(
                title=f"Video: {occasion}",
                description=brief,
                acceptance_criteria=list(_AUTHORING_ACCEPTANCE_CRITERIA),
                team=Team.UX_UI,
                assigned_to=assignee,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                # LOW keeps it atomic: a single composition needs no cell-PM
                # decomposition, and a medium/high root task assigned to a dev
                # is auto-blocked for subtasks it will never have.
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", project.id),
                status=TaskStatus.PENDING,
                source=VIDEO_SOURCE,
                confirmed_by_human=True,  # normal delivery task, not CEO-held
            )
        )
        markers.set_video_draft(
            task,
            {
                "occasion": occasion,
                "script": script,
                "platforms": platforms,
                "brief": brief,
            },
        )
        await self.session.flush()
        self.log.info(
            "video-engine: authoring task opened",
            occasion=occasion,
            assignee=str(assignee),
        )
        return task

    # ---- release trigger (event-driven hook) -------------------------------

    async def draft_release_video(
        self, *, version: str, changelog: str
    ) -> TaskTable | None:
        """Originate ONE UX/UI video-authoring task for a release announcement,
        or None (no-op).

        No-ops when the flag or the release sub-switch is off; the shared
        dedup/open-cap/project checks in ``open_video_task`` cover the rest.
        Called from ``ReleaseProposalService.approve()``'s publish success
        branch, right beside the X-post draft hook — never invoked by a loop
        itself.
        """
        if not (settings.video_engine_enabled and settings.video_on_release):
            return None
        script = await self._draft_release_script(version, changelog)
        return await self.open_video_task(
            occasion=f"release {version}",
            script=script,
            platforms=["x", "tiktok"],
            brief=script,
        )

    async def _draft_release_script(self, version: str, changelog: str) -> str:
        try:
            draft = await _chat(_release_video_prompt(version, changelog))
        except Exception as exc:
            self.log.warning(
                "video-engine: local-model script draft failed (fallback template)",
                error=str(exc),
            )
            draft = None
        return (draft or "").strip() or _fallback_release_script(version, changelog)

    # ---- held draft (materialized once a render pass produces MP4s) -------

    async def _originate_video_post(
        self,
        *,
        source_task: TaskTable,
        mp4_paths: dict[str, str],
        captions: dict[str, str],
        platforms: list[str],
    ) -> TaskTable:
        """Open ONE PENDING, HELD video-post draft owned by the Secretary.

        Carries forward the authoring task's ``video_draft`` marker (occasion,
        script, composition_id, input_props) plus the freshly rendered mp4
        paths and per-platform captions — the payload a render pass
        materializes once the composition has merged and rendered.
        """
        task_svc = get_task_service(self.session)
        source_draft = markers.get_video_draft(source_task) or {}
        occasion = source_draft.get("occasion") or source_task.title
        task = await task_svc.create(
            TaskCreateRequest(
                title=f"Video post: {occasion}",
                description=source_draft.get("script") or source_task.description,
                acceptance_criteria=list(_POST_ACCEPTANCE_CRITERIA),
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["secretary-1"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", source_task.project_id),
                status=TaskStatus.PENDING,
                source=VIDEO_POST_SOURCE,
                confirmed_by_human=False,  # HELD for the CEO; never dispatched
            )
        )
        markers.set_video_draft(
            task,
            {
                **source_draft,
                "mp4_paths": mp4_paths,
                "x_caption": captions.get("x", ""),
                "tiktok_caption": captions.get("tiktok", ""),
                "platforms": platforms,
                "render_status": "rendered",
            },
        )
        await self.session.flush()
        self.log.info(
            "video-engine: video post drafted (held for CEO)",
            source_task_id=str(source_task.id),
        )
        return task


def get_video_engine(session: AsyncSession) -> VideoEngine:
    """Build a VideoEngine for ``session``."""
    return VideoEngine(session)
