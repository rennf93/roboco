"""XEngine — draft X (Twitter) posts/replies, ALL held for CEO approval.

Mirrors the ReleaseManagerEngine "detect -> originate a CEO-gated artifact ->
hold" shape:

* **Default OFF.** ``x_engine_enabled`` is False, so neither the release-post
  hook nor the mentions loop originates anything.
* **Never posts.** Every draft is HELD (``confirmed_by_human=False``, owned by
  the Secretary, skipped by every dispatcher) — posting is acted on only by
  ``XPostService`` behind an explicit CEO approve.
* **No creds, no calls.** Without stored X credentials the client degrades to
  ``NullXClient`` (never raises, never egresses) exactly like an unconfigured
  research provider.
* **Local model only.** Drafting runs on the local LLM (MemoryDistiller
  posture) — never a cloud LLM in the hot path.

Two responsibilities: ``draft_release_post`` is the event-driven hook called
from ``ReleaseProposalService.approve()``'s publish success branch;
``run_cycle`` is the periodic mentions poll driven by the orchestrator loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import httpx
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import (
    AgentSpawnSessionTable,
    XSeenFeatureTable,
    XSeenMentionTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.company_goals import get_company_goals_service
from roboco.services.project import get_project_service
from roboco.services.task import (
    X_FEATURE_EXPLORATION_SOURCE,
    X_FEATURE_SOURCE,
    X_POST_SOURCE,
    X_REPLY_SOURCE,
    TaskCreateRequest,
    get_task_service,
)
from roboco.services.x_client import (
    MAX_TWEET_CHARS,
    XClient,
    XMention,
    build_x_client,
)
from roboco.services.x_credentials import get_x_credentials_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_CHAT_TIMEOUT_SECONDS = 60.0
_MIN_MENTION_CHARS = 3

_HOM_VOICE = (
    "You are RoboCo's Head of Marketing, posting on the company's X (Twitter) "
    "account. Confident and concise, no emoji spam, no hashtags unless truly "
    "apt. Speak as 'we'. Plain text only, no markdown, no thread — one post."
)


def _clamp_tweet(text: str) -> str:
    """Collapse whitespace and hard-enforce the 280-char limit."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_TWEET_CHARS:
        return collapsed
    return collapsed[: MAX_TWEET_CHARS - 1].rstrip() + "…"


def _fallback_release_body(version: str, highlights: list[str]) -> str:
    lead = highlights[0] if highlights else "assorted improvements"
    return f"RoboCo v{version} is out: {lead}"


def _release_prompt(version: str, highlights: list[str], voice: str) -> str:
    bullets = "\n".join(f"- {h}" for h in highlights[:5]) or "- routine improvements"
    return (
        f"{voice}\n\n"
        f"Draft ONE tweet (max 280 characters) announcing that RoboCo "
        f"v{version} just shipped. Lead with the most user-visible change.\n\n"
        f"Highlights:\n{bullets}\n"
    )


def _reply_prompt(mention: XMention, voice: str) -> str:
    return (
        f"{voice}\n\n"
        "Draft ONE reply tweet (max 280 characters) to this mention. Be "
        "helpful and on-brand; do not invent facts about RoboCo.\n\n"
        f'Mention: "{mention.text}"\n'
    )


async def _chat(prompt: str) -> str | None:
    """One local-LLM chat call (OpenAI-compatible); None on a non-success."""
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


def _is_meaningful(mention: XMention, min_engagement: int) -> bool:
    """Bounded "meaningful mention" heuristic — deliberately simple.

    Not-obviously-a-bot (real text, not a bare retweet) plus an engagement
    floor (likes + replies + retweets). Precision over recall: a mention that
    fails this is just never drafted, never mis-drafted.
    """
    text = mention.text.strip()
    if len(text) < _MIN_MENTION_CHARS:
        return False
    if text.upper().startswith("RT "):
        return False
    engagement = mention.like_count + mention.reply_count + mention.retweet_count
    return engagement >= min_engagement


_FEATURE_EXPLORATION_TITLE = "X feature-spotlight exploration"
_FEATURE_EXPLORATION_DESCRIPTION = (
    "Investigate RoboCo's own shipped capabilities — CHANGELOG.md, the "
    "feature-flags ledger, docs/map, the company charter, and the knowledge "
    "base — and pick ONE under-publicized feature not already covered (see "
    "the seen-features list on this task). Draft ONE marketing post via "
    "propose_feature_spotlight()."
)


class XEngine(BaseService):
    """Draft release posts (event hook) + mention replies (poll), both held."""

    service_name = "x_engine"

    def __init__(self, session: AsyncSession, client: XClient | None = None) -> None:
        super().__init__(session)
        self._injected_client = client

    async def _client(self) -> XClient:
        if self._injected_client is not None:
            return self._injected_client
        creds = await get_x_credentials_service(self.session).get_decrypted()
        return build_x_client(
            creds,
            account_user_id=settings.x_account_user_id,
            timeout=settings.x_request_timeout_seconds,
        )

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _voice_guide(self) -> str:
        """Baseline house style plus the CEO's brand-voice sample, when set.

        The CEO-supplied sample lives in the company charter (``company_goals.
        brand_voice``, panel-editable) — a live DB read, never hardcoded, so an
        edit takes effect on the next draft with no deploy. Falls back to the
        baseline constant alone when unset, so this is additive, not required.
        """
        charter = await get_company_goals_service(self.session).get()
        brand_voice = (charter.get("brand_voice") or "").strip()
        if not brand_voice:
            return _HOM_VOICE
        return (
            f"{_HOM_VOICE}\n\n"
            f"Additional brand-voice direction from the CEO:\n{brand_voice}"
        )

    # ---- release posts (event-driven hook) --------------------------------

    async def draft_release_post(
        self, *, version: str, highlights: list[str]
    ) -> TaskTable | None:
        """Originate ONE held release-announcement draft, or None (no-op).

        No-ops when the flag is off, no credentials are configured, a draft
        for this version already exists (retry-safe), or the open-post cap is
        reached. Called from ``ReleaseProposalService.approve()``'s publish
        success branch — never invoked by the loop itself.
        """
        if not settings.x_engine_enabled:
            return None
        client = await self._client()
        if not client.configured:
            return None
        task_svc = get_task_service(self.session)
        open_posts = await task_svc.list_open_x_posts()
        if any(
            task.source == X_POST_SOURCE
            and markers.get_x_release_version(task) == version
            for task in open_posts
        ):
            return None  # already drafted for this version
        if len(open_posts) >= settings.x_max_open_posts:
            self.log.warning(
                "x-engine: open-post cap reached; not drafting release post",
                version=version,
            )
            return None
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "x-engine: RoboCo project not resolvable; skipping release post",
                version=version,
            )
            return None
        body = await self._draft_release_body(version, highlights)
        task = await self._originate_post(
            title=f"X post: release v{version}",
            body=body,
            source=X_POST_SOURCE,
            project_id=cast("UUID", project.id),
        )
        markers.set_x_release_version(task, version)
        await self.session.flush()
        self.log.info("x-engine: release post drafted (held for CEO)", version=version)
        return task

    async def _draft_release_body(self, version: str, highlights: list[str]) -> str:
        voice = await self._voice_guide()
        try:
            draft = await _chat(_release_prompt(version, highlights, voice))
        except Exception as exc:
            self.log.warning(
                "x-engine: local-model draft failed (fallback template)",
                error=str(exc),
            )
            draft = None
        body = (draft or "").strip() or _fallback_release_body(version, highlights)
        return _clamp_tweet(body)

    # ---- mentions (periodic poll) ------------------------------------------

    async def run_cycle(self) -> list[TaskTable]:
        """One mentions-poll pass: fetch, filter, dedup, draft, hold. No-op
        list unless the engine AND the mention-reply sub-switch are on and
        credentials are configured (release posting doesn't use this path)."""
        if not (settings.x_engine_enabled and settings.x_replies_enabled):
            return []
        client = await self._client()
        if not client.configured:
            return []
        task_svc = get_task_service(self.session)
        open_count = len(await task_svc.list_open_x_posts())
        if open_count >= settings.x_max_open_posts:
            self.log.info("x-engine: open-post cap reached; skipping mentions cycle")
            return []
        mentions = await client.fetch_mentions(since_id=None, max_results=50)
        project = await self._roboco_project()
        return await self._process_mentions(mentions, project, open_count)

    async def _process_mentions(
        self, mentions: list[XMention], project: ProjectTable | None, open_count: int
    ) -> list[TaskTable]:
        """Filter/dedup each mention and originate held reply drafts, honoring
        the per-cycle and open-post caps."""
        originated: list[TaskTable] = []
        for mention in mentions:
            if len(originated) >= settings.x_mentions_max_per_cycle:
                break
            if open_count + len(originated) >= settings.x_max_open_posts:
                break
            if not mention.id or await self._already_seen(mention.id):
                continue
            await self._mark_seen(mention.id)
            if not _is_meaningful(mention, settings.x_mentions_min_engagement):
                continue
            if project is None or project.id is None:
                self.log.warning(
                    "x-engine: RoboCo project not resolvable; skipping mentions cycle"
                )
                break
            originated.append(
                await self._originate_reply(mention, cast("UUID", project.id))
            )
        return originated

    async def _originate_reply(self, mention: XMention, project_id: UUID) -> TaskTable:
        body = await self._draft_reply_body(mention)
        task = await self._originate_post(
            title=f"X reply: mention {mention.id}",
            body=body,
            source=X_REPLY_SOURCE,
            project_id=project_id,
        )
        markers.set_x_mention_ref(
            task,
            {"id": mention.id, "author_id": mention.author_id, "text": mention.text},
        )
        await self.session.flush()
        self.log.info("x-engine: reply drafted (held for CEO)", mention_id=mention.id)
        return task

    async def _draft_reply_body(self, mention: XMention) -> str:
        voice = await self._voice_guide()
        try:
            draft = await _chat(_reply_prompt(mention, voice))
        except Exception as exc:
            self.log.warning(
                "x-engine: local-model reply draft failed (fallback template)",
                error=str(exc),
            )
            draft = None
        body = (draft or "").strip() or "Thanks for the mention!"
        return _clamp_tweet(body)

    async def _already_seen(self, mention_id: str) -> bool:
        return await self.session.get(XSeenMentionTable, mention_id) is not None

    async def _mark_seen(self, mention_id: str) -> None:
        self.session.add(XSeenMentionTable(mention_id=mention_id))
        await self.session.flush()

    # ---- feature spotlight (periodic HoM investigation) --------------------

    async def open_feature_spotlight_exploration(self) -> TaskTable | None:
        """Originate ONE held exploration task for the Head of Marketing, or None.

        No-ops when the flags are off, no X credentials are configured (drafting
        content nobody can ever post is pointless — mirrors the release/mentions
        guard), a cycle is already open, the shared open-post cap is reached, or
        the RoboCo project isn't resolvable. Never authors content itself — HoM
        does, via propose_feature_spotlight() once the dispatcher spawns it.
        """
        if not (settings.x_engine_enabled and settings.x_feature_spotlight_enabled):
            return None
        client = await self._client()
        if not client.configured:
            return None
        task_svc = get_task_service(self.session)
        # Cancel a stale + spawnless exploration so the engine can re-arm; a
        # fresh cycle or one with a live HoM spawn blocks (one open at a time).
        if not await self._cancel_stale_exploration(task_svc):
            return None
        if len(await task_svc.list_open_x_posts()) >= settings.x_max_open_posts:
            return None  # respect the shared held-draft cap
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "x-engine: RoboCo project not resolvable; skipping feature spotlight"
            )
            return None
        return await self._originate_feature_exploration(
            task_svc, cast("UUID", project.id)
        )

    async def _cancel_stale_exploration(self, task_svc: TaskService) -> bool:
        """Return False to block re-arm (fresh cycle or live HoM spawn); return
        True to proceed — either no open exploration, or a stale+spawnless one
        was just cancelled and a fresh one should be originated.
        """
        open_explorations = await task_svc.list_open_feature_explorations()
        if not open_explorations:
            return True  # nothing open; fall through to originate
        stale = open_explorations[0]  # oldest-first
        stale_age = datetime.now(UTC) - stale.created_at
        if stale_age < timedelta(
            seconds=2 * settings.x_feature_spotlight_interval_seconds
        ):
            return False  # one open cycle at a time; not yet stale
        if await self._has_live_hom_spawn(cast("UUID", stale.id)):
            return False  # HoM still working it; respawn breaker tripped but alive
        # Stale + spawnless: the HoM spawn died without completing. Cancel and
        # re-arm so a dead exploration can't gate the engine silent forever.
        stale.status = TaskStatus.CANCELLED
        await self.session.flush()
        return True

    async def _has_live_hom_spawn(self, task_id: UUID) -> bool:
        """True if an agent spawn for ``task_id`` is still active (ended_at NULL).

        The respawn breaker can trip while HoM is genuinely working — a live
        spawn row means the agent is still running, so re-arm must block.
        """
        result = await self.session.execute(
            select(AgentSpawnSessionTable)
            .where(
                AgentSpawnSessionTable.task_id == str(task_id),
                AgentSpawnSessionTable.ended_at.is_(None),
            )
            .limit(1)
        )
        return result.scalars().first() is not None

    async def _originate_feature_exploration(
        self, task_svc: TaskService, project_id: UUID
    ) -> TaskTable:
        seen = await self._seen_feature_slugs()
        task = await task_svc.create(
            TaskCreateRequest(
                title=_FEATURE_EXPLORATION_TITLE,
                description=_FEATURE_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_feature_spotlight() is called once with an "
                    "under-publicized, not-yet-covered feature"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=X_FEATURE_EXPLORATION_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        markers.set_x_seen_features(task, seen)
        await self.session.flush()
        self.log.info(
            "feature-spotlight exploration opened (Head of Marketing)",
            task_id=str(task.id),
        )
        return task

    async def _seen_feature_slugs(self) -> list[str]:
        result = await self.session.execute(select(XSeenFeatureTable.feature_slug))
        return list(result.scalars().all())

    async def is_feature_seen(self, feature_slug: str) -> bool:
        return await self.session.get(XSeenFeatureTable, feature_slug) is not None

    # ---- shared origination -------------------------------------------------

    async def _originate_post(
        self, *, title: str, body: str, source: str, project_id: UUID
    ) -> TaskTable:
        """Open ONE PENDING, HELD X draft owned by the Secretary."""
        task_svc = get_task_service(self.session)
        task = await task_svc.create(
            TaskCreateRequest(
                title=title,
                description=body,
                acceptance_criteria=["CEO approves or rejects the draft"],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["secretary-1"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=source,
                confirmed_by_human=False,  # HELD for the CEO; never dispatched
            )
        )
        markers.set_x_draft_body(task, body)
        await self.session.flush()
        return task

    async def materialize_feature_spotlight(
        self,
        *,
        exploration_task: TaskTable,
        feature_slug: str,
        feature_title: str,
        body: str,
    ) -> TaskTable:
        """Complete a HoM-authored spotlight: mark the feature seen, create the
        held draft (identical shape to _originate_post), complete the exploration
        task. Called only from the propose_feature_spotlight content verb."""
        self.session.add(XSeenFeatureTable(feature_slug=feature_slug))
        task = await self._originate_post(
            title=f"X post: feature spotlight — {feature_title}",
            body=_clamp_tweet(body),
            source=X_FEATURE_SOURCE,
            project_id=cast("UUID", exploration_task.project_id),
        )
        markers.set_x_feature_ref(task, {"slug": feature_slug, "title": feature_title})
        exploration_task.status = TaskStatus.COMPLETED
        await self.session.flush()
        self.log.info(
            "x-engine: feature spotlight drafted (held for CEO)",
            feature_slug=feature_slug,
        )
        return task


def get_x_engine(session: AsyncSession, client: XClient | None = None) -> XEngine:
    """Build an XEngine for ``session`` (optional injected client for tests)."""
    return XEngine(session, client=client)
