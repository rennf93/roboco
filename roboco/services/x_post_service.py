"""XPostService — the CEO's approve/reject glue over held X posts/replies.

Mirrors ``ReleaseProposalService``: finds the open draft(s) the engine
prepared, and on approval posts to X via ``x_client`` under a Redis
single-flight lock (a plain SET NX — unlike the release mutex there is no long
heartbeat to run since a single tweet POST completes in well under the lock
TTL), then marks the task COMPLETED. A completed task is idempotent — a
second approve is a no-op that returns the already-posted result. Rejecting
records the reason and CANCELS the draft (unlike the release proposal there is
no revision workflow here — the CEO edits inline and re-approves, or a fresh
draft is originated on the next cycle/release).

A successfully-posted ``x_feature`` (spotlight) draft additionally fires
``_open_spotlight_video`` — the companion-video hook moved here from
authoring time (``propose_feature_spotlight``) so it only fires once the CEO
has actually approved the spotlight, mirroring the
``ReleaseProposalService.approve`` -> ``_draft_video`` seam.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import redis.asyncio as redis

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.task import X_FEATURE_SOURCE, X_SOURCES, get_task_service
from roboco.services.x_client import MAX_TWEET_CHARS, build_x_client
from roboco.services.x_credentials import get_x_credentials_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "roboco:x_post:"
_LOCK_TTL_SECONDS = 60  # a tweet POST completes in seconds; generous crash backstop
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class XPostBodyTooLongError(ValueError):
    """The CEO's edited body exceeds the 280-char tweet limit."""


class TaskAlreadyCompletedError(Exception):
    """The task is already COMPLETED (posted publicly) and can't be rejected."""


@dataclass(frozen=True)
class XPostExecuteResult:
    """The outcome of an approve call.

    `status` is one of: posted, already_posted, already_in_progress,
    no_credentials, post_failed, redis_unavailable.
    """

    status: str
    tweet_id: str | None
    detail: str


class XPostService(BaseService):
    """List / approve / reject held X post + reply drafts."""

    service_name = "x_post_service"

    async def list_open_posts(self) -> list[TaskTable]:
        """Every held X draft (both sources) awaiting the CEO."""
        return await get_task_service(self.session).list_open_x_posts()

    async def list_post_history(self, *, limit: int = 50) -> list[TaskTable]:
        """Acted-on X drafts (posted or rejected), newest-acted-first —
        the panel history basis."""
        return await get_task_service(self.session).list_x_post_history(limit=limit)

    async def approve(
        self, task_id: UUID, edited_body: str | None = None
    ) -> XPostExecuteResult | None:
        """Post the draft to X (optionally with the CEO's edited body).

        Returns None when ``task_id`` is not an open X draft. Idempotent: a
        task already COMPLETED returns ``already_posted`` with the stored
        tweet id, without calling the client again.
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source not in X_SOURCES:
            return None

        trimmed: str | None = None
        if edited_body is not None:
            trimmed = edited_body.strip()
            if len(trimmed) > MAX_TWEET_CHARS:
                raise XPostBodyTooLongError(
                    f"edited body is {len(trimmed)} chars, over the "
                    f"{MAX_TWEET_CHARS}-char tweet limit"
                )
            if not trimmed:
                trimmed = None

        if task.status == TaskStatus.COMPLETED:
            return XPostExecuteResult(
                status="already_posted",
                tweet_id=markers.get_x_posted_tweet_id(task),
                detail="this draft was already posted",
            )

        lock_key = f"{_LOCK_PREFIX}{task_id}"
        try:
            token = await self._acquire_lock(lock_key)
        except _LockUnavailable as exc:
            logger.error("x-post lock unavailable (redis down): %s", exc)
            return XPostExecuteResult(
                status="redis_unavailable",
                tweet_id=None,
                detail="Redis is unavailable so the post mutex can't be acquired.",
            )
        if token is None:
            return XPostExecuteResult(
                status="already_in_progress",
                tweet_id=None,
                detail="A post is already in progress for this draft.",
            )
        try:
            return await self._approve_locked(task_id, task, trimmed)
        finally:
            await self._release_lock(lock_key, token)

    async def _approve_locked(
        self, task_id: UUID, task: TaskTable, trimmed_body: str | None
    ) -> XPostExecuteResult | None:
        """The critical section under the held post lock.

        Re-reads the committed state (expire forces a fresh SELECT): a
        concurrent approve that won the lock first may have posted + committed
        COMPLETED after the pre-lock read, so acting on the stale in-memory
        row would double-post. The CEO's edited body is applied to the
        re-read locked row AFTER the COMPLETED check — a concurrent approve
        that already posted can't have this edit land on the just-posted task
        (stored body ≠ posted tweet).
        """
        self.session.expire(task)
        locked = await get_task_service(self.session).get(task_id)
        if locked is None:
            return None
        if locked.status == TaskStatus.COMPLETED:
            return XPostExecuteResult(
                status="already_posted",
                tweet_id=markers.get_x_posted_tweet_id(locked),
                detail="this draft was already posted",
            )
        if trimmed_body:
            markers.set_x_draft_body(locked, trimmed_body)
            await self.session.flush()
        return await self._post(locked)

    async def _post(self, task: TaskTable) -> XPostExecuteResult:
        body = markers.get_x_draft_body(task) or task.description or ""
        creds = await get_x_credentials_service(self.session).get_decrypted()
        client = build_x_client(
            creds,
            account_user_id=settings.x_account_user_id,
            timeout=settings.x_request_timeout_seconds,
        )
        if not client.configured:
            return XPostExecuteResult(
                status="no_credentials",
                tweet_id=None,
                detail="No X credentials are configured.",
            )
        result = await client.post_tweet(body)
        if not result.posted:
            return XPostExecuteResult(
                status="post_failed", tweet_id=None, detail=result.detail
            )
        markers.set_x_posted_tweet_id(task, result.tweet_id or "")
        task.status = TaskStatus.COMPLETED
        # Commit while still holding the lock so COMPLETED is durable before
        # release — otherwise a racing approve could acquire the lock the
        # instant we drop it and double-post before the route-level commit.
        await self.session.commit()
        if task.source == X_FEATURE_SOURCE:
            await self._open_spotlight_video(task, body)
        return XPostExecuteResult(
            status="posted", tweet_id=result.tweet_id, detail=result.detail
        )

    async def _open_spotlight_video(self, task: TaskTable, posted_body: str) -> None:
        """Mirrors ``ReleaseProposalService._draft_video``: a best-effort side
        effect after the post has already succeeded, never allowed to affect
        the result above. Moved here from authoring time
        (``propose_feature_spotlight``) so a ux-dev never burns a delivery
        cycle on a spotlight video for a draft the CEO ends up rejecting —
        this only fires once the tweet is actually live.

        Fires only when the draft's ``x_feature_ref`` marker carries
        ``wants_video`` (stamped by ``propose_feature_spotlight``) and both
        ``video_engine_enabled`` and ``video_on_spotlight`` are on.
        ``open_video_task``'s own occasion-dedup covers a hypothetical repeat
        call; the COMPLETED short-circuit in ``approve()`` already prevents
        ``_post`` (and so this) from running twice for the same draft.
        """
        if not (settings.video_engine_enabled and settings.video_on_spotlight):
            return
        ref = markers.get_x_feature_ref(task) or {}
        if not ref.get("wants_video"):
            return
        feature_slug = str(ref.get("slug") or "")
        feature_title = str(ref.get("title") or "")
        video_script = str(ref.get("video_script") or "")
        try:
            from roboco.services.video_engine import get_video_engine

            feature_brief = f"{feature_title}: {posted_body}"
            await get_video_engine(self.session).open_video_task(
                occasion=f"spotlight {feature_slug}",
                script=video_script.strip() or feature_brief,
                platforms=["x", "tiktok"],
                brief=feature_brief,
            )
        except Exception as exc:
            logger.warning("spotlight video draft failed (best-effort): %s", exc)

    async def reject(self, task_id: UUID, reason: str) -> TaskTable | None:
        """Record the CEO's reason and cancel the draft (never posted)."""
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source not in X_SOURCES:
            return None
        if task.status == TaskStatus.COMPLETED:
            raise TaskAlreadyCompletedError(
                f"X draft {task_id} already posted (COMPLETED); cannot be rejected"
            )
        markers.set_x_reject_reason(task, reason)
        task.status = TaskStatus.CANCELLED
        await self.session.flush()
        return task

    # ---- Redis single-flight lock (plain SET NX — no heartbeat needed) -----

    async def _acquire_lock(self, lock_key: str) -> str | None:
        token = uuid4().hex
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                acquired = await conn.set(
                    lock_key, token, nx=True, ex=_LOCK_TTL_SECONDS
                )
                return token if acquired else None
            finally:
                await conn.aclose()
        except Exception as exc:
            raise _LockUnavailable(str(exc)) from exc

    async def _release_lock(self, lock_key: str, token: str) -> None:
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                await conn.eval(_RELEASE_SCRIPT, 1, lock_key, token)
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("x-post lock release failed (redis): %s", exc)


class _LockUnavailable(Exception):
    """Redis is unreachable — distinct from "the lock is held" (fail-closed)."""


def get_x_post_service(session: AsyncSession) -> XPostService:
    """Construct an XPostService bound to ``session``."""
    return XPostService(session)
