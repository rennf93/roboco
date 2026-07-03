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

from typing import TYPE_CHECKING, cast

import httpx

from roboco.config import settings
from roboco.db.tables import XSeenMentionTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import (
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


def _release_prompt(version: str, highlights: list[str]) -> str:
    bullets = "\n".join(f"- {h}" for h in highlights[:5]) or "- routine improvements"
    return (
        f"{_HOM_VOICE}\n\n"
        f"Draft ONE tweet (max 280 characters) announcing that RoboCo "
        f"v{version} just shipped. Lead with the most user-visible change.\n\n"
        f"Highlights:\n{bullets}\n"
    )


def _reply_prompt(mention: XMention) -> str:
    return (
        f"{_HOM_VOICE}\n\n"
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
        try:
            draft = await _chat(_release_prompt(version, highlights))
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
        list when the flag is off or no credentials are configured."""
        if not settings.x_engine_enabled:
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
        try:
            draft = await _chat(_reply_prompt(mention))
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


def get_x_engine(session: AsyncSession, client: XClient | None = None) -> XEngine:
    """Build an XEngine for ``session`` (optional injected client for tests)."""
    return XEngine(session, client=client)
