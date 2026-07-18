"""VideoEngine — open a UX/UI authoring task for a bespoke marketing video,
and materialize the rendered clip as a held CEO-approval draft.

Two task kinds, mirroring the XEngine/ReleaseManagerEngine "originate a
CEO-scoped artifact" shape, but split across the real delivery lifecycle:

* **Authoring task** (``source="video"``) — a normal, ASSIGNED delivery task
  (``confirmed_by_human=True``): the engine hands it straight to a ux-dev, who
  authors a HyperFrames composition on a real branch through the standard
  claim -> code -> PR -> merge path. NOT held and NOT in any dispatcher's
  skip bucket — it is a pre-assigned code task like any other.
* **Held draft** (``source="video_post"``) — materialized once a render pass
  has produced the MP4s: Secretary-owned, ``confirmed_by_human=False``,
  skipped by every dispatcher, acted on only by an explicit CEO approve.

Default OFF (``video_engine_enabled``); no authoring task is ever opened
while the flag is off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.company_goals import get_company_goals_service
from roboco.services.notification_delivery import get_notification_delivery_service
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
    "Composition follows motion/README.md's design bar and uses the "
    "panel-demo kit register where the occasion shows the product",
    "request_render preview frames verified — every scene in the brief "
    "appears fully and legibly in the rendered cut",
]
_POST_ACCEPTANCE_CRITERIA = ["CEO approves or rejects the draft"]

# Mirrors task_completeness._AC_MAX_ITEMS / _AC_MAX_ITEM_CHARS (mig 068) —
# duplicated locally per this file's no-cross-service-internals idiom (see
# vault_intake_engine.py / api/schemas/v1/flow.py for the same pattern).
_AC_MAX_ITEMS = 7
_AC_MAX_ITEM_CHARS = 200

# reauthor_from_rejection's fallback AC when the original brief named no
# enumerable features — the rejection reason is already verbatim in the brief.
_REAUTHOR_FEEDBACK_CRITERION = (
    "Every point in the CEO rejection feedback is visibly addressed in the rendered cut"
)

_CHAT_TIMEOUT_SECONDS = 60.0

# The whole CHANGELOG section for a release, not one bullet — capped so a
# pathological entry can't blow up the task description.
_CHANGELOG_BRIEF_CHARS = 4000

# Shared by every open_video_task caller (release/spotlight/on-demand) so the
# authoring dev always lands on the demo kit instead of a text card.
_MOTION_DESIGN_POINTER = (
    "Before authoring: read motion/README.md's design bar and motion/kit/"
    "README.md. Build in the panel-demo register on motion/kit/ — extend "
    "compositions/panel-demo/ rather than starting from scratch or shipping "
    "a text card. Before submitting: call request_render and read every "
    "returned frame to verify the RENDERED cut, not just the source."
)


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


def _changelog_highlights(changelog: str) -> list[str]:
    """Every bullet line in a CHANGELOG section, in order — the structured
    highlights list a composition's props.js can render directly."""
    highlights = []
    for line in changelog.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            text = stripped.lstrip("-*").strip()
            if text:
                highlights.append(text)
    return highlights


def _first_changelog_bullet(changelog: str) -> str:
    """First CHANGELOG bullet's text, or "" if none — the fallback template's
    headline when the local model is unavailable."""
    highlights = _changelog_highlights(changelog)
    return highlights[0] if highlights else ""


def _fallback_release_script(version: str, changelog: str, product_name: str) -> str:
    highlight = _first_changelog_bullet(changelog)
    lead = f": {highlight}" if highlight else ""
    return f"{product_name} v{version} just shipped{lead}."


def _release_video_prompt(version: str, changelog: str, product_name: str) -> str:
    return (
        f"You are {product_name}'s marketing team, writing a short voiceover "
        "script for a bespoke motion-graphics video announcing a release. "
        "Plain text, 2-3 short sentences, energetic but factual — no "
        "invented facts.\n\n"
        f"Write the script for {product_name} v{version}, based on this "
        f"CHANGELOG entry:\n{changelog[:1000]}\n"
    )


def _release_video_brief(
    version: str, changelog: str, highlights: list[str], product_name: str
) -> str:
    """The structured release brief: the (capped) CHANGELOG section for this
    version plus its highlights list — replaces the old one-liner-as-
    description. The LLM script stays a separate ``script`` prop suggestion,
    never the whole brief."""
    section = changelog[:_CHANGELOG_BRIEF_CHARS].strip() or "(no changelog entry)"
    parts = [f"{product_name} v{version} release notes:", section]
    if highlights:
        bullets = "\n".join(f"- {h}" for h in highlights)
        parts.append(f"Highlights:\n{bullets}")
    return "\n\n".join(parts)


def _scene_criterion(features: list[str]) -> str | None:
    """Turn an enumerable brief feature list into its own gate-checkable AC.

    The live failure this closes: a brief named N features, the task's ACs
    stayed generic, a dev shipped fewer scenes, and every gate passed because
    "N features" existed only in prose. None for an empty/absent list. The
    joined list is bounded to the AC per-item char cap — truncated with an
    "… (+N more)" tail rather than overrunning it.
    """
    trimmed = [f.strip() for f in features if f and f.strip()]
    if not trimmed:
        return None
    prefix = "Every brief-named feature appears as its own fully readable scene: "
    budget = _AC_MAX_ITEM_CHARS - len(prefix)
    kept = list(trimmed)
    while kept:
        omitted = len(trimmed) - len(kept)
        tail = f"… (+{omitted} more)" if omitted else ""
        body = "; ".join(kept) + tail
        if len(body) <= budget:
            return prefix + body
        kept.pop()
    return prefix + "…"  # pathological: even one (huge) feature name overruns


def _authoring_criteria(
    suggested_input_props: dict[str, Any] | None,
    fallback_acceptance_criterion: str | None,
) -> list[str]:
    """The base authoring ACs plus one derived/fallback criterion: the scene
    criterion when ``suggested_input_props["highlights"]`` is a real list,
    else ``fallback_acceptance_criterion`` (when given), else nothing."""
    criteria = list(_AUTHORING_ACCEPTANCE_CRITERIA)
    features = (suggested_input_props or {}).get("highlights")
    criterion = _scene_criterion(features if isinstance(features, list) else [])
    criterion = criterion or fallback_acceptance_criterion
    if criterion is not None and len(criteria) < _AC_MAX_ITEMS:
        criteria.append(criterion)
    return criteria


def _reauthor_brief(reason: str, draft: dict[str, Any]) -> str:
    """The revision brief for a CEO-rejected cut: the verbatim rejection
    feedback, a revise-in-place pointer at the existing composition (when
    known), then the original brief/script for context."""
    parts = [
        "REVISION of a CEO-rejected cut. CEO rejection feedback "
        f"(address every point): {reason}"
    ]
    composition_id = draft.get("composition_id")
    if composition_id:
        parts.append(
            f"Revise the EXISTING composition motion/compositions/{composition_id}/ "
            "in place — do not start a new composition."
        )
    original = draft.get("brief") or draft.get("script") or ""
    if original:
        parts.append(original)
    return "\n\n".join(parts)


class VideoEngine(BaseService):
    """Open video-authoring tasks (event hooks + on-demand), both gated."""

    service_name = "video_engine"

    async def _roboco_project(self) -> ProjectTable | None:
        """The fixed RoboCo project the release/spotlight hooks author
        against (``self_heal_project_slug``). The on-demand caller instead
        supplies its own ``project_id`` — see ``resolve_authoring_project``."""
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def resolve_authoring_project(
        self, *, project_id: UUID | None, occasion: str
    ) -> ProjectTable | None:
        """The project to author this video against, or None when
        unresolvable or not opted into the video engine.

        ``project_id`` (the on-demand ``/video/request`` caller, and the
        render loop's own per-task resolution) resolves by id; omitted (the
        release/spotlight hooks), it falls back to the fixed RoboCo project.
        Both paths share the same two skip reasons, both logged: unresolvable
        project (warning — a config/data gap) vs. project not opted in (info
        — the operator hasn't flipped the per-project ``video_engine_enabled``
        toggle). The global flag arms the subsystem; the project's flag opts
        that repo into authoring against its own ``motion/`` dir (mirrors
        ``ci_watch_enabled``).
        """
        project = (
            await get_project_service(self.session).get(project_id)
            if project_id is not None
            else await self._roboco_project()
        )
        if project is None or project.id is None:
            self.log.warning(
                "video-engine: project not resolvable; skipping video task",
                occasion=occasion,
                project_id=str(project_id) if project_id else None,
            )
            return None
        if not getattr(project, "video_engine_enabled", False):
            self.log.info(
                "video-engine: project not opted into video; skipping video task",
                occasion=occasion,
                project_slug=str(getattr(project, "slug", "")),
            )
            return None
        return project

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

    async def _brand_voice_note(self) -> str:
        """CEO-supplied brand-voice sample from the company charter, or ""
        when unset. Same ``company_goals.brand_voice`` read x_engine's
        ``_voice_guide`` uses, duplicated locally per this service's
        no-cross-service-internals policy."""
        charter = await get_company_goals_service(self.session).get()
        return (charter.get("brand_voice") or "").strip()

    async def _enrich_brief(self, brief: str) -> str:
        """Append the CEO's brand-voice sample (when set) and the motion
        design-bar pointer to any occasion's brief.

        The one seam shared by the release, spotlight, and on-demand callers
        of ``open_video_task`` — each supplies only its own content, and all
        three inherit the same enrichment here rather than duplicating it
        per caller (on-demand's caller, the ``/video/request`` route, passes
        its brief through verbatim, so this is the only place it can land).
        """
        parts = [brief]
        brand_voice = await self._brand_voice_note()
        if brand_voice:
            parts.append(f"Brand voice (from the CEO's charter):\n{brand_voice}")
        parts.append(_MOTION_DESIGN_POINTER)
        return "\n\n".join(parts)

    async def open_video_task(
        self,
        *,
        occasion: str,
        script: str,
        platforms: list[str],
        brief: str,
        suggested_input_props: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        fallback_acceptance_criterion: str | None = None,
    ) -> TaskTable | None:
        """Originate ONE UX/UI authoring task for a bespoke video, or None.

        No-ops when the global flag is off, the target project hasn't opted
        in (``video_engine_enabled``), a task for this occasion is already
        open (authoring or held draft), the open cap is reached, or the
        target project isn't resolvable. The opened task is a normal,
        ASSIGNED delivery task (``source=VIDEO_SOURCE``,
        ``confirmed_by_human=True``) — NOT held — so it dispatches straight to
        the assigned ux-dev like any other pre-assigned code task.

        ``project_id`` scopes authoring to a specific project (the on-demand
        ``/video/request`` caller); omitted, the release/spotlight hooks
        default to the fixed RoboCo project — see
        ``resolve_authoring_project``.

        ``brief`` is enriched (brand-voice + motion design-bar pointer
        appended) before becoming the task description and the marker's
        ``brief`` field. ``suggested_input_props`` (e.g. ``{"version": ...,
        "highlights": [...]}`` from the release caller) is seeded onto the
        marker as-is so the dev copies real structured data into
        ``propose_video``'s ``input_props`` instead of hand-typing facts.

        When ``suggested_input_props`` carries a ``highlights`` list, it
        becomes its OWN acceptance criterion (``_scene_criterion``) — a
        prose-only feature list used to pass gates even when a dev shipped
        fewer scenes than named. Absent highlights,
        ``fallback_acceptance_criterion`` (when supplied) is appended instead
        — ``reauthor_from_rejection`` uses this for its
        feedback-must-be-addressed criterion.
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
        project = await self.resolve_authoring_project(
            project_id=project_id, occasion=occasion
        )
        if project is None:
            return None
        from sqlalchemy.exc import SQLAlchemyError

        assignee = self._select_ux_dev(open_tasks)
        enriched_brief = await self._enrich_brief(brief)
        acceptance_criteria = _authoring_criteria(
            suggested_input_props, fallback_acceptance_criterion
        )
        # Savepoint-isolate the insert: a DBAPI error here (FK, deadlock,
        # dropped connection) must roll back ONLY this insert, never poison the
        # shared session — whose next commit is the caller's release-publish
        # finalize / request boundary, which must not inherit an error state.
        try:
            async with self.session.begin_nested():
                task = await task_svc.create(
                    TaskCreateRequest(
                        title=f"Video: {occasion}",
                        description=enriched_brief,
                        acceptance_criteria=acceptance_criteria,
                        team=Team.UX_UI,
                        assigned_to=assignee,
                        created_by=_foundation.AGENTS["system"].uuid,
                        task_type=TaskType.CODE,
                        nature=TaskNature.TECHNICAL,
                        # LOW keeps it atomic: a single composition needs no
                        # cell-PM decomposition, and a medium/high root task
                        # assigned to a dev is auto-blocked for subtasks it
                        # will never have.
                        estimated_complexity=Complexity.LOW,
                        project_id=cast("UUID", project.id),
                        status=TaskStatus.PENDING,
                        source=VIDEO_SOURCE,
                        confirmed_by_human=True,  # normal delivery, not CEO-held
                    )
                )
                markers.set_video_draft(
                    task,
                    {
                        "occasion": occasion,
                        "script": script,
                        "platforms": platforms,
                        "brief": enriched_brief,
                        "suggested_input_props": dict(suggested_input_props or {}),
                    },
                )
                await self.session.flush()
            self.log.info(
                "video-engine: authoring task opened",
                occasion=occasion,
                assignee=str(assignee),
            )
            return task
        except SQLAlchemyError as exc:
            self.log.warning(
                "video-engine: authoring task insert failed",
                occasion=occasion,
                error=str(exc),
            )
            return None

    # ---- release trigger (event-driven hook) -------------------------------

    async def draft_release_video(
        self, *, version: str, changelog: str, project_id: UUID | None = None
    ) -> TaskTable | None:
        """Originate ONE UX/UI video-authoring task for a release announcement,
        or None (no-op).

        No-ops when the flag or the release sub-switch is off; the shared
        dedup/open-cap/project checks in ``open_video_task`` cover the rest.
        Called from ``ReleaseProposalService.approve()``'s publish success
        branch, right beside the X-post draft hook — never invoked by a loop
        itself.

        The brief is the structured changelog block (built independent of
        the local model, so it stands even when the model is down); the
        LLM-drafted (or template-fallback) one-liner is only the ``script``
        prop suggestion, never the whole brief.
        """
        if not (settings.video_engine_enabled and settings.video_on_release):
            return None
        project = (
            await get_project_service(self.session).get(project_id)
            if project_id is not None
            else None
        )
        product_name = await get_company_goals_service(
            self.session
        ).resolve_product_name(project)
        script = await self._draft_release_script(version, changelog, product_name)
        highlights = _changelog_highlights(changelog)
        brief = _release_video_brief(version, changelog, highlights, product_name)
        return await self.open_video_task(
            occasion=f"release {version}",
            script=script,
            platforms=["x", "tiktok"],
            brief=brief,
            suggested_input_props={"version": version, "highlights": highlights},
            project_id=project_id,
        )

    async def _draft_release_script(
        self, version: str, changelog: str, product_name: str
    ) -> str:
        try:
            draft = await _chat(_release_video_prompt(version, changelog, product_name))
        except Exception as exc:
            self.log.warning(
                "video-engine: local-model script draft failed (fallback template)",
                error=str(exc),
            )
            draft = None
        return (draft or "").strip() or _fallback_release_script(
            version, changelog, product_name
        )

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
        # Savepoint-isolate the insert: in the render loop's N-tasks-per-cycle
        # commit, a DBAPI error on one held draft must roll back only that
        # insert (the loop then marks the task failed) — not the cycle's work.
        async with self.session.begin_nested():
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
                    "source_task_id": str(source_task.id),
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
        try:
            await get_notification_delivery_service(
                self.session
            ).notify_ceo_of_queue_item(
                kind="video", id8=str(task.id)[:8], title=occasion
            )
        except Exception as exc:
            self.log.warning(
                "video-engine: telegram notify failed (best-effort)", error=str(exc)
            )
        return task

    # ---- reject -> re-author (CEO feedback loop) ---------------------------

    async def _resolve_reauthor_project(
        self, post_task: TaskTable, draft: dict[str, Any]
    ) -> UUID | None:
        """The project to re-author against: the rejected post's own
        ``project_id`` (the normal case), else its source authoring task's
        (via the draft's ``source_task_id``) — a defensive fallback for a
        draft that somehow landed without one."""
        if post_task.project_id is not None:
            return cast("UUID", post_task.project_id)
        source_task_id = draft.get("source_task_id")
        if not source_task_id:
            return None
        source_task = await get_task_service(self.session).get(
            cast("UUID", source_task_id)
        )
        return cast("UUID", source_task.project_id) if source_task else None

    async def reauthor_from_rejection(
        self, post_task: TaskTable, reason: str
    ) -> TaskTable | None:
        """Route a CEO's rejection reason into a fresh authoring task that
        revises the SAME composition in place, instead of the feedback going
        nowhere.

        Reads the rejected ``video_post`` draft's carried-forward
        ``video_draft`` marker (occasion, brief/script, composition_id,
        platforms, input_props, source_task_id) and re-opens via
        ``open_video_task`` under the SAME occasion — that call's own dedup
        only scans OPEN drafts, so the just-cancelled post never blocks it (a
        second reject while a revision is already open correctly dedups
        against it instead of stacking a third).

        Best-effort: never raises. A missing draft marker, an unresolvable
        project, or any other failure just logs a warning and returns None —
        the caller's reject must succeed regardless of this seam.
        """
        try:
            draft = markers.get_video_draft(post_task)
            if draft is None:
                self.log.warning(
                    "video-engine: reauthor skipped, no video_draft marker",
                    task_id=str(post_task.id),
                )
                return None
            project_id = await self._resolve_reauthor_project(post_task, draft)
            if project_id is None:
                self.log.warning(
                    "video-engine: reauthor skipped, no project resolvable",
                    task_id=str(post_task.id),
                )
                return None
            return await self.open_video_task(
                occasion=str(draft.get("occasion") or post_task.title),
                script=str(draft.get("script") or ""),
                platforms=list(draft.get("platforms") or []),
                brief=_reauthor_brief(reason, draft),
                suggested_input_props=(
                    draft.get("input_props") or draft.get("suggested_input_props")
                ),
                project_id=project_id,
                # Original had highlights -> the scene criterion regenerates
                # from them; no highlights -> this fallback names the reason
                # (already verbatim in the brief) as the checkable outcome.
                fallback_acceptance_criterion=_REAUTHOR_FEEDBACK_CRITERION,
            )
        except Exception as exc:
            self.log.warning(
                "video-engine: reauthor from rejection failed",
                task_id=str(post_task.id),
                error=str(exc),
            )
            return None

    # ---- re-render (CEO-triggered retry) -----------------------------------

    async def rerender(self, task_id: UUID) -> TaskTable | None:
        """Clear ``render_status``/``render_attempts``/``render_error`` off a
        completed video-authoring task's ``video_draft`` marker, so the next
        render cycle's scan (``render_status`` unset) re-picks it up and
        re-renders it — e.g. after the CEO fixes something and wants a fresh
        pass, or wants to retry past a terminal ``failed`` state.

        None (a 404 to the route) when there is no such completed authoring
        task, or the dev hasn't called ``propose_video`` yet (no
        ``composition_id`` — nothing to render).
        """
        task = await get_task_service(self.session).get(task_id)
        if (
            task is None
            or task.source != VIDEO_SOURCE
            or task.status != TaskStatus.COMPLETED
        ):
            return None
        draft = markers.get_video_draft(task) or {}
        if not draft.get("composition_id"):
            return None
        cleared = {
            k: v
            for k, v in draft.items()
            if k not in ("render_status", "render_attempts", "render_error")
        }
        markers.set_video_draft(task, cleared)
        await self.session.flush()
        self.log.info("video-engine: re-render requested", task_id=str(task.id))
        return task


def get_video_engine(session: AsyncSession) -> VideoEngine:
    """Build a VideoEngine for ``session``."""
    return VideoEngine(session)
