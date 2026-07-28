"""BarflyEngine — Barfly (Board Program), org-scoped.

Mirrors ``roboco.services.x_engine.XEngine``'s mentions-poll shape, but
SEARCHES instead of listening: the Head of Marketing wants X conversations
where RoboCo is relevant but UNMENTIONED (spec §4 "Barfly") — keyword/topic
search, not the mentions timeline. The X API tier is ops config, not a code
concern (spec §8): this engine assumes ``search_recent`` is available; if the
account's tier doesn't have it, the tier gets upgraded, not the code.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.barfly.enabled`` key is
  the ONLY arming path (mirrors periscope/spackle/scales/sentinel/coroner —
  ``_legacy_enabled`` has no alias for a program not migrated from a pre-
  registry flag).
* **Credentials-gated**, same posture as ``XEngine.
  open_feature_spotlight_exploration``: drafting replies nobody can ever post
  is pointless, so a cycle never opens without stored X credentials.
* **One open cycle at a time.** Dedup by ``source=board_barfly`` non-terminal
  tasks.
* **Dedup ledger reuse.** Candidate tweet ids are checked against/recorded
  into the EXISTING ``x_seen_mentions`` table (``XSeenMentionTable``) rather
  than a new table: its shape (``mention_id`` primary key + ``seen_at``) is
  generic enough to mean "a tweet id already turned into a held item",
  regardless of whether it was discovered via the mentions poll or a search —
  a tweet id is a tweet id in one global namespace, so reuse also stops
  Barfly from re-surfacing something the mentions poll already drafted (and
  vice versa). No migration needed.
* **External text is screened.** Every candidate's text is untrusted,
  attacker-writable content — ``foundation.policy.injection_guard.
  screen_external_text`` neutralizes it (envelope + inline pattern flags,
  nothing dropped) before it's stored on the exploration task's marker, same
  posture as ``XEngine._originate_reply``'s mention screening.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Head of Marketing carrying the screened
  candidates; the Head of Marketing picks up to N worth replying to and
  calls ``propose_conversation_replies`` exactly once, which materializes
  each approved-shape reply as its own held draft (source=x_barfly) through
  ``XEngine._originate_post`` — the same chokepoint every X draft rides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.db.tables import XSeenMentionTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.injection_guard import screen_external_text
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import BARFLY_SOURCE, TaskCreateRequest, get_task_service
from roboco.services.x_client import build_x_client
from roboco.services.x_credentials import get_x_credentials_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService
    from roboco.services.x_client import XClient, XMention

_EXPLORATION_TITLE = "Barfly conversation-reply cycle"
_MIN_CANDIDATE_CHARS = 3


def _exploration_description(candidate_count: int) -> str:
    return (
        "Review the screened candidate X conversations already gathered for "
        "you on this task — search results where RoboCo is relevant but "
        "unmentioned. Pick up to "
        f"{candidate_count} worth engaging and draft commentary via "
        "propose_conversation_replies(). Only target a REAL candidate "
        "already on this task — never invent a tweet. Each draft posts as a "
        "STANDALONE post carrying a link to the conversation (X forbids "
        "programmatic replies into unmentioning threads), so write "
        "commentary that stands on its own with no @handles — assume the "
        "reader sees your post first and the linked thread second."
    )


class BarflyEngine(BaseService):
    """Search X, screen results, and originate ONE held Barfly cycle for the
    Head of Marketing."""

    service_name = "barfly_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, no X credentials are configured,
        a cycle is already open, the RoboCo project isn't resolvable, or the
        search turned up nothing worth carrying (no candidates survive
        dedup/screening). Never authors replies itself — the Head of
        Marketing does, via ``propose_conversation_replies`` once spawned by
        the board dispatcher.
        """
        if not await program_armed(self.session, "barfly"):
            return None
        client = await self._client()
        if not client.configured:
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_barfly_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning("barfly-engine: RoboCo project not resolvable; skipping")
            return None
        candidates = await self._gather_candidates(client)
        if not candidates:
            return None
        return await self._originate(task_svc, cast("UUID", project.id), candidates)

    async def _client(self) -> XClient:
        creds = await get_x_credentials_service(self.session).get_decrypted()
        return build_x_client(
            creds,
            account_user_id=settings.x_account_user_id,
            timeout=settings.x_request_timeout_seconds,
        )

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _gather_candidates(self, client: XClient) -> list[dict[str, Any]]:
        """Run every configured query, screening and deduping as candidates
        come in, up to the per-cycle cap. Stops early once the cap is hit —
        a later query never trims an earlier one's results."""
        cap = settings.barfly_max_candidates
        candidates: list[dict[str, Any]] = []
        for query in settings.barfly_queries:
            if len(candidates) >= cap:
                break
            results = await client.search_recent(query, max_results=cap)
            for mention in results:
                if len(candidates) >= cap:
                    break
                candidate = await self._screen_and_mark(mention)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    async def _screen_and_mark(self, mention: XMention) -> dict[str, Any] | None:
        """Filter + dedup + screen one search result into a candidate dict,
        or None to skip it (not-yet-seen check happens first so a below-
        floor/empty result never burns a seen-ledger row)."""
        if not mention.id or len(mention.text.strip()) < _MIN_CANDIDATE_CHARS:
            return None
        if await self._already_seen(mention.id):
            return None
        screened = screen_external_text(mention.text, source=f"x_search:{mention.id}")
        if screened.flagged:
            self.log.warning(
                "barfly-engine: injection pattern detected in candidate text",
                tweet_id=mention.id,
                hits=screened.hits,
            )
        await self._mark_seen(mention.id)
        engagement = mention.like_count + mention.reply_count + mention.retweet_count
        # Store the screened rendering UNCLAMPED (mirrors XEngine._originate_
        # reply's x_mention_ref.text) — a real tweet is already <=280 chars,
        # but screen_external_text's envelope/caution/flag wrapping is not,
        # and _clamp_tweet-ing THAT would truncate away the very content the
        # screen-and-flag posture exists to preserve, not drop.
        return {
            "id": mention.id,
            "author_handle": mention.author_id,
            "text": screened.rendered,
            "engagement_note": f"{engagement} combined likes/replies/retweets",
        }

    async def _already_seen(self, tweet_id: str) -> bool:
        # ponytail: reuse XSeenMentionTable (mention_id + seen_at) rather than
        # a dedicated table — a tweet id is a tweet id regardless of
        # discovery path, see the module docstring's dedup-ledger-reuse note.
        return await self.session.get(XSeenMentionTable, tweet_id) is not None

    async def _mark_seen(self, tweet_id: str) -> None:
        self.session.add(XSeenMentionTable(mention_id=tweet_id))
        await self.session.flush()

    async def _originate(
        self,
        task_svc: TaskService,
        project_id: UUID,
        candidates: list[dict[str, Any]],
    ) -> TaskTable:
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_exploration_description(len(candidates)),
                acceptance_criteria=[
                    "propose_conversation_replies() is called once with 1-"
                    f"{settings.barfly_max_candidates} drafted replies, each "
                    "targeting a real candidate tweet_id already on this task"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=BARFLY_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        markers.set_barfly_candidates(task, candidates)
        await self.session.flush()
        self.log.info(
            "barfly exploration cycle opened (Head of Marketing)",
            task_id=str(task.id),
            candidate_count=len(candidates),
        )
        return task


def get_barfly_engine(session: AsyncSession) -> BarflyEngine:
    """Build a BarflyEngine for ``session``."""
    return BarflyEngine(session)
