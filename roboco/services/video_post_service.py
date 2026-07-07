"""VideoPostService — the CEO's approve/reject glue over held video-post drafts.

Mirrors ``XPostService``: finds the held ``video_post`` draft the render loop
prepared, and on approval posts the rendered clip to each of the draft's
platforms, then marks the task COMPLETED. A completed task is idempotent — a
second approve is a no-op that returns the already-posted ids. Rejecting
records the reason and CANCELS the draft.

Unlike ``XPostService`` (a single tweet POST that completes in seconds, so a
flat 60s `SET NX` is plenty), a video upload + server-side transcode/poll can
run well past a minute, so the critical section here runs under the
heartbeat-renewed mutex (``heartbeat_mutex.py``) instead — the same fencing +
renew-loop shape ``ReleaseProposalService`` uses for its ~40min release
execute.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.heartbeat_mutex import HeartbeatLockUnavailable, HeartbeatMutex
from roboco.services.task import VIDEO_POST_SOURCE, get_task_service
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

logger = logging.getLogger(__name__)

# A tweet-length caption reuses X's own limit; TikTok's caption/description
# field allows up to 2200 chars (mirrors _MAX_TIKTOK_CAPTION_CHARS, the
# propose_video author-time check in gateway/content_actions.py — duplicated
# here rather than imported so this service doesn't reach into the gateway
# layer's internals).
MAX_TIKTOK_CAPTION_CHARS = 2200

_LOCK_PREFIX = "roboco:video_post:"
# A video upload + transcode/poll can run well past a minute (X media
# processing alone can take ~140s, TikTok chunked uploads longer); the TTL is
# a crash backstop and the heartbeat keeps it alive for the real duration.
_LOCK_TTL_SECONDS = 1800
_LOCK_HEARTBEAT_SECONDS = 30.0

_DEFAULT_CUTS: dict[str, str] = {"x": "square", "tiktok": "vertical"}


class VideoCaptionTooLongError(ValueError):
    """The CEO's edited caption exceeds the platform's character limit."""


class TaskAlreadyCompletedError(Exception):
    """The task is already COMPLETED (posted publicly) and can't be rejected."""


@dataclass(frozen=True)
class XVideoPostResult:
    """The outcome of an `XVideoPoster.post_video` call."""

    posted: bool
    video_id: str | None
    detail: str


@dataclass(frozen=True)
class TikTokUploadResult:
    """The outcome of a `TikTokPoster.upload_to_inbox` call."""

    uploaded: bool
    publish_id: str | None
    detail: str


class XVideoPoster(ABC):
    """Posts a rendered clip to X as native video.

    The real v2 media-upload implementation (initialize/append/finalize/
    poll, then `/2/tweets` with `media_ids`) lands next phase;
    `NullXVideoPoster` is the inert default injected until then.
    """

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def post_video(self, *, mp4_path: str, caption: str) -> XVideoPostResult: ...


class NullXVideoPoster(XVideoPoster):
    """No X video poster wired — every call is a no-op, never raises."""

    @property
    def configured(self) -> bool:
        return False

    async def post_video(self, *, mp4_path: str, caption: str) -> XVideoPostResult:
        _ = (mp4_path, caption)
        return XVideoPostResult(
            posted=False, video_id=None, detail="no X video poster configured"
        )


class TikTokPoster(ABC):
    """Uploads a rendered clip to the creator's TikTok drafts inbox.

    The real OAuth2 inbox-upload implementation (init/chunked-PUT/status-
    fetch) lands next phase; `NullTikTokPoster` is the inert default injected
    until then.
    """

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def upload_to_inbox(
        self, *, mp4_path: str, caption: str
    ) -> TikTokUploadResult: ...


class NullTikTokPoster(TikTokPoster):
    """No TikTok poster wired — every call is a no-op, never raises."""

    @property
    def configured(self) -> bool:
        return False

    async def upload_to_inbox(
        self, *, mp4_path: str, caption: str
    ) -> TikTokUploadResult:
        _ = (mp4_path, caption)
        return TikTokUploadResult(
            uploaded=False, publish_id=None, detail="no TikTok poster configured"
        )


@dataclass(frozen=True)
class VideoPostExecuteResult:
    """The outcome of an approve call.

    `status` is one of: posted, posted_partial, post_failed, no_platforms,
    already_posted, already_in_progress, redis_unavailable, lock_lost.
    `posted` maps platform -> the id the poster returned, for platforms that
    succeeded (persisted, so a retry after a partial failure never re-posts
    an already-succeeded platform).
    """

    status: str
    posted: dict[str, str]
    detail: str


class VideoPostService(BaseService):
    """List / approve / reject held video-post drafts."""

    service_name = "video_post_service"

    def __init__(
        self,
        session: AsyncSession,
        *,
        x_poster: XVideoPoster,
        tiktok_poster: TikTokPoster,
    ) -> None:
        super().__init__(session)
        self._x_poster = x_poster
        self._tiktok_poster = tiktok_poster

    async def list_held_video_posts(self) -> list[TaskTable]:
        """Every held video_post draft awaiting the CEO (panel queue basis)."""
        return await get_task_service(self.session).list_open_video_post_drafts()

    async def approve(
        self,
        task_id: UUID,
        *,
        x_caption: str | None = None,
        tiktok_caption: str | None = None,
    ) -> VideoPostExecuteResult | None:
        """Post the draft's platforms (optionally with the CEO's edited captions).

        Returns None when `task_id` is not an open video_post draft.
        Idempotent: a task already COMPLETED returns `already_posted` with
        the stored ids, without calling any poster again. The critical
        section runs under the heartbeat-renewed mutex (an upload can
        outlast a flat lock TTL), not a plain SET NX.

        Caption edits are only VALIDATED here (pure, no session write) —
        they're applied to the draft under the lock, on the fresh re-read
        row. A pre-lock write used to flush a stale whole-column draft
        (missing whatever a concurrent approve had already posted); once
        this session later commits under the lock, that stale write wins
        and erases the concurrent approve's committed posted-id, causing a
        double-post on the next retry.
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != VIDEO_POST_SOURCE:
            return None

        validated_captions = self._validate_caption_edits(x_caption, tiktok_caption)

        if task.status == TaskStatus.COMPLETED:
            draft = dict(markers.get_video_draft(task) or {})
            return self._already_posted_result(draft)

        mutex = HeartbeatMutex(
            f"{_LOCK_PREFIX}{task_id}",
            ttl_seconds=_LOCK_TTL_SECONDS,
            heartbeat_seconds=_LOCK_HEARTBEAT_SECONDS,
        )
        try:
            token = await mutex.acquire()
        except HeartbeatLockUnavailable as exc:
            logger.error("video-post lock unavailable (redis down): %s", exc)
            return VideoPostExecuteResult(
                status="redis_unavailable",
                posted={},
                detail="Redis is unavailable so the post mutex can't be acquired.",
            )
        if token is None:
            return VideoPostExecuteResult(
                status="already_in_progress",
                posted={},
                detail="A post is already in progress for this draft.",
            )
        try:
            return await self._approve_locked(
                task_id, task, mutex, token, validated_captions
            )
        finally:
            await mutex.release(token)

    async def _approve_locked(
        self,
        task_id: UUID,
        task: TaskTable,
        mutex: HeartbeatMutex,
        token: str,
        validated_captions: dict[str, str],
    ) -> VideoPostExecuteResult | None:
        """The critical section under the held post lock.

        Re-reads the committed state (expire forces a fresh SELECT): a
        concurrent approve that won the lock first may have posted +
        committed COMPLETED after the pre-lock read, so acting on the stale
        in-memory row would double-post. On a lock-loss the session is
        rolled back before returning — every commit here goes through
        `_commit_shielded`, which both survives a mid-commit cancellation
        AND waits out that shielded commit before returning, so by the time
        we get here it has already settled; without that wait, this
        rollback would race a still-in-flight commit on the same session
        and SQLAlchemy raises IllegalStateChangeError. The rollback itself
        is still needed — it's what guarantees the session is clean for
        reuse instead of poisoned for the caller's next statement.
        """
        self.session.expire(task)
        locked = await get_task_service(self.session).get(task_id)
        if locked is None:
            return None
        # Copy — see the identical note in approve() above.
        draft = dict(markers.get_video_draft(locked) or {})
        if locked.status == TaskStatus.COMPLETED:
            return self._already_posted_result(draft)
        guarded = await mutex.run_guarded(
            self._post_all_platforms(locked, draft, validated_captions), token
        )
        if guarded.lock_lost:
            await self.session.rollback()
            return VideoPostExecuteResult(
                status="lock_lost",
                posted={},
                detail=(
                    "The post lock was lost mid-upload (an extended Redis "
                    "outage let the mutex expire); aborted fail-closed. "
                    "Retry the approve."
                ),
            )
        return guarded.value

    async def _post_all_platforms(
        self,
        task: TaskTable,
        draft: dict[str, Any],
        validated_captions: dict[str, str],
    ) -> VideoPostExecuteResult:
        """Post every platform in the draft that hasn't already succeeded.

        Applies the CEO's validated caption edits onto `draft` first — this
        is the fresh, re-read-under-lock draft (never the pre-lock one), so
        the edit rides the very first per-platform commit below instead of
        a separate stale-column write a concurrent approve could clobber.

        Each success is committed durably right away — before the next
        platform is attempted — so a mid-loop raise, a lock-loss
        cancellation, or a crash can never lose the record of what already
        posted; a retry re-reads the committed draft and skips it via the
        `already_posted` check below. Only commits COMPLETED once every
        platform has posted (`_finalize_post`).

        Each commit runs through `_commit_shielded` — a lock-loss
        cancellation firing while it's in flight must not interrupt it (see
        that method for why "shielded" alone isn't sufficient).
        """
        draft.update(validated_captions)
        platforms = draft.get("platforms") or []
        if not platforms:
            return VideoPostExecuteResult(
                status="no_platforms", posted={}, detail="draft has no target platforms"
            )
        posted: dict[str, str] = {}
        failures: dict[str, str] = {}
        for platform in platforms:
            already_posted = draft.get(f"{platform}_posted_id")
            if already_posted:
                posted[platform] = str(already_posted)
                continue
            posted_id, detail = await self._attempt_platform_post(platform, draft)
            if posted_id is None:
                failures[platform] = detail
                continue
            posted[platform] = posted_id
            draft[f"{platform}_posted_id"] = posted_id
            # Commit now, durable before the next platform is attempted. Copy
            # (not the shared `draft` object) — set_video_draft reassigns the
            # column, and re-passing the same mutated object on the NEXT
            # iteration would compare equal to the value it just set, so
            # SQLAlchemy would see "no change" and silently drop it.
            #
            # Residual: a crash between a poster returning and this commit
            # landing could still double-post that platform on retry —
            # acceptable for a CEO-gated, low-frequency approve; a
            # platform-native idempotency key is a future follow-up.
            markers.set_video_draft(task, dict(draft))
            await self._commit_shielded()
        return await self._finalize_post(task, posted, failures)

    async def _commit_shielded(self) -> None:
        """Commit via asyncio.shield so a lock-loss cancellation firing
        mid-commit can't interrupt it — an interrupted commit both loses
        the posted-id it was about to make durable (a retry re-posts that
        platform) and can leave the session unusable for whatever runs
        next.

        On cancellation, waits out the still-in-flight shielded commit
        before re-raising — the caller's `_approve_locked` rolls back on
        lock_lost, and racing that rollback against a commit still
        touching the same session raises SQLAlchemy's own
        IllegalStateChangeError (two operations on one session at once).
        """
        commit_task = asyncio.ensure_future(self.session.commit())
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            await asyncio.gather(commit_task, return_exceptions=True)
            raise

    async def _attempt_platform_post(
        self, platform: str, draft: dict[str, Any]
    ) -> tuple[str | None, str]:
        """Resolve `platform`'s cut/mp4 and dispatch its poster.

        Returns (id, detail); id is None on any failure — no rendered mp4
        for the cut, an unknown platform, a poster-reported rejection, or a
        raising poster (caught here so it fails only THIS platform, not the
        whole post cycle).
        """
        mp4_paths = draft.get("mp4_paths") or {}
        cut = str(draft.get(f"{platform}_cut") or _DEFAULT_CUTS.get(platform, ""))
        mp4_path = mp4_paths.get(cut)
        if not mp4_path:
            return None, f"no rendered mp4 for cut {cut!r}"
        caption = str(draft.get(f"{platform}_caption") or "")
        try:
            return await self._post_platform(platform, mp4_path, caption)
        except Exception as exc:
            return None, str(exc)

    async def _post_platform(
        self, platform: str, mp4_path: str, caption: str
    ) -> tuple[str | None, str]:
        """Dispatch one platform's poster. Returns (id, detail); id is None
        on failure/unknown-platform, with `detail` explaining why."""
        if platform == "x":
            return await self._post_x(mp4_path, caption)
        if platform == "tiktok":
            return await self._post_tiktok(mp4_path, caption)
        return None, f"unknown platform {platform!r}"

    async def _post_x(self, mp4_path: str, caption: str) -> tuple[str | None, str]:
        if not self._x_poster.configured:
            return None, "no X credentials configured"
        result = await self._x_poster.post_video(mp4_path=mp4_path, caption=caption)
        if not result.posted:
            return None, result.detail
        return result.video_id, result.detail

    async def _post_tiktok(self, mp4_path: str, caption: str) -> tuple[str | None, str]:
        if not self._tiktok_poster.configured:
            return None, "no TikTok credentials configured"
        result = await self._tiktok_poster.upload_to_inbox(
            mp4_path=mp4_path, caption=caption
        )
        if not result.uploaded:
            return None, result.detail
        return result.publish_id, result.detail

    async def _finalize_post(
        self, task: TaskTable, posted: dict[str, str], failures: dict[str, str]
    ) -> VideoPostExecuteResult:
        if not failures:
            task.status = TaskStatus.COMPLETED
            # Commit while still holding the lock so COMPLETED is durable
            # before release — otherwise a racing approve could acquire the
            # lock the instant we drop it and double-post before a
            # route-level commit. Shielded — see _commit_shielded.
            await self._commit_shielded()
            return VideoPostExecuteResult(
                status="posted", posted=dict(posted), detail="posted to all platforms"
            )
        # Every successful platform's posted-id was already committed in the
        # loop above (see _post_all_platforms) — nothing left to persist.
        status = "posted_partial" if posted else "post_failed"
        detail = "; ".join(f"{p}: {d}" for p, d in failures.items())
        return VideoPostExecuteResult(status=status, posted=dict(posted), detail=detail)

    def _validate_caption_edits(
        self, x_caption: str | None, tiktok_caption: str | None
    ) -> dict[str, str]:
        """Validate + trim the CEO's edited captions. Pure — no session
        write. Raises `VideoCaptionTooLongError` over a platform's limit. A
        blank (post-strip) edit is dropped (mirrors XPostService.approve).
        Returns only the fields that changed; the caller applies the result
        onto the freshly re-read draft under the lock, never here.
        """
        edits: dict[str, str] = {}
        if x_caption is not None:
            trimmed = self._clamp_or_raise(
                x_caption, field="x_caption", max_chars=MAX_TWEET_CHARS
            )
            if trimmed:
                edits["x_caption"] = trimmed
        if tiktok_caption is not None:
            trimmed = self._clamp_or_raise(
                tiktok_caption,
                field="tiktok_caption",
                max_chars=MAX_TIKTOK_CAPTION_CHARS,
            )
            if trimmed:
                edits["tiktok_caption"] = trimmed
        return edits

    @staticmethod
    def _clamp_or_raise(value: str, *, field: str, max_chars: int) -> str:
        trimmed = value.strip()
        if len(trimmed) > max_chars:
            raise VideoCaptionTooLongError(
                f"{field} is {len(trimmed)} chars, over the {max_chars}-char limit"
            )
        return trimmed

    @staticmethod
    def _already_posted_result(draft: dict[str, Any]) -> VideoPostExecuteResult:
        posted: dict[str, str] = {}
        for platform in _DEFAULT_CUTS:
            posted_id = draft.get(f"{platform}_posted_id")
            if posted_id:
                posted[platform] = str(posted_id)
        return VideoPostExecuteResult(
            status="already_posted",
            posted=posted,
            detail="this draft was already posted",
        )

    async def reject(self, task_id: UUID, reason: str) -> TaskTable | None:
        """Record the CEO's reason and cancel the draft (never posted)."""
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != VIDEO_POST_SOURCE:
            return None
        if task.status == TaskStatus.COMPLETED:
            raise TaskAlreadyCompletedError(
                f"video post {task_id} already posted (COMPLETED); cannot be rejected"
            )
        markers.set_video_reject_reason(task, reason)
        task.status = TaskStatus.CANCELLED
        await self.session.flush()
        return task


def get_video_post_service(
    session: AsyncSession,
    *,
    x_poster: XVideoPoster | None = None,
    tiktok_poster: TikTokPoster | None = None,
) -> VideoPostService:
    """Construct a VideoPostService bound to `session`.

    Defaults to the inert Null posters — the real X-v2 / TikTok
    implementations are injected here once built (next phase).
    """
    return VideoPostService(
        session,
        x_poster=x_poster or NullXVideoPoster(),
        tiktok_poster=tiktok_poster or NullTikTokPoster(),
    )
