"""Reusable heartbeat-renewed Redis mutex — SET NX EX with a fencing token, a
background renew loop, and a compare-and-del release.

Mirrors (without touching) ``ReleaseProposalService``'s release-execute mutex
(``release_proposal.py:40-307``): the same fencing-token + heartbeat + Lua
release shape, extracted here so any OTHER operation too long for a flat
``SET NX`` (e.g. a video upload + server-side transcode, which can easily
exceed ``XPostService``'s flat 60s lock) gets the same crash-safe, race-safe
lock without re-implementing the asyncio orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import redis.asyncio as redis

from roboco.config import settings

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

# Only delete/extend the lock when its value still equals the caller's
# fencing token, so a late release/heartbeat can't clobber a usurper's lock.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
_HEARTBEAT_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class HeartbeatLockUnavailable(Exception):
    """Redis is unreachable so the mutex can't be acquired.

    Distinct from "the lock is held" (a concurrent operation owns it — retry
    later): this is an infra failure. Both stay fail-closed — guarded work
    never runs without the mutex.
    """


@dataclass(frozen=True)
class GuardedResult[T]:
    """The outcome of `HeartbeatMutex.run_guarded`.

    `lock_lost=True` means the guarded coroutine was cancelled fail-closed
    after a >TTL Redis outage let a usurper acquire the lock mid-operation;
    `value` is only meaningful when `lock_lost` is False.
    """

    value: T | None
    lock_lost: bool


class HeartbeatMutex:
    """A `SET NX EX` Redis mutex with a fencing token + background renew loop.

    `acquire`/`release`/`heartbeat_once` are the raw primitives (a fresh
    connection per call, matching this project's existing lock helpers);
    `run_guarded` composes them with the renew loop and the cancel-on-loss
    dance so callers don't reimplement the asyncio orchestration for every
    long critical section (a plain flat `SET NX`, like `XPostService`'s, is
    fine for a sub-minute op; anything that can run longer than one TTL
    needs this instead).
    """

    def __init__(self, key: str, *, ttl_seconds: int, heartbeat_seconds: float) -> None:
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def acquire(self) -> str | None:
        """`SET NX EX` with a fresh fencing token.

        None means the lock is already held (a concurrent operation owns
        it). Raises `HeartbeatLockUnavailable` when Redis itself is
        unreachable.
        """
        token = uuid4().hex
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                acquired = await conn.set(
                    self._key, token, nx=True, ex=self._ttl_seconds
                )
                return token if acquired else None
            finally:
                await conn.aclose()
        except Exception as exc:
            raise HeartbeatLockUnavailable(str(exc)) from exc

    async def release(self, token: str) -> None:
        """Compare-and-del: only clears the key if it still holds `token`, so
        a late release can never delete a usurper's lock (best-effort — a
        release failure only leaves the TTL to backstop eventual cleanup)."""
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                await conn.eval(_RELEASE_SCRIPT, 1, self._key, token)
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("heartbeat mutex release failed (redis): %s", exc)

    async def heartbeat_once(self, token: str) -> bool:
        """Compare-and-expire. True if `token` still owns the lock (TTL renewed)."""
        conn = redis.from_url(settings.redis_url)
        try:
            res = await conn.eval(
                _HEARTBEAT_SCRIPT, 1, self._key, token, self._ttl_seconds
            )
            return bool(res)
        finally:
            await conn.aclose()

    async def _heartbeat_loop(
        self, token: str, guarded: asyncio.Task[Any], lock_lost: asyncio.Event
    ) -> None:
        """Refresh the TTL until `guarded` finishes or ownership is lost.

        Refreshes before the first sleep so a fast operation still extends
        the TTL. Fail-CLOSED: a falsy result (compare-and-expire says the
        stored token isn't ours) cancels `guarded` immediately, no grace. A
        *raised* renew error alone used to be logged-and-continued forever,
        so a holder whose Redis renews kept erroring never learned its key
        had expired server-side while a usurper acquired it — now a raise
        only tolerates a transient blip: once `_ttl_seconds -
        _heartbeat_seconds` has elapsed since the last SUCCESSFUL renew (the
        key has expired or is about to), it's treated the same as an
        explicit lock-loss.
        """
        last_success = time.monotonic()
        grace = self._ttl_seconds - 2 * self._heartbeat_seconds
        while True:
            try:
                renewed = await self.heartbeat_once(token)
            except Exception as exc:
                elapsed = time.monotonic() - last_success
                if elapsed >= grace:
                    logger.critical(
                        "heartbeat mutex renew has been failing for ~the "
                        "whole TTL (elapsed=%.1fs); treating the lock as "
                        "lost fail-closed (key=%s): %s",
                        elapsed,
                        self._key,
                        exc,
                    )
                    lock_lost.set()
                    guarded.cancel()
                    return
                logger.warning("heartbeat mutex refresh failed (redis): %s", exc)
            else:
                if not renewed:
                    logger.critical(
                        "heartbeat mutex lock lost mid-operation; cancelling "
                        "fail-closed (key=%s)",
                        self._key,
                    )
                    lock_lost.set()
                    guarded.cancel()
                    return
                last_success = time.monotonic()
            await asyncio.sleep(self._heartbeat_seconds)

    async def run_guarded[T](
        self, coro: Coroutine[Any, Any, T], token: str
    ) -> GuardedResult[T]:
        """Run `coro` to completion while a background heartbeat holds
        `token`'s lock alive; cancels it fail-closed if the lock is ever lost.

        A caller-side cancellation of `run_guarded` itself (unrelated to a
        lost lock) propagates rather than being swallowed as `lock_lost`.
        """
        lock_lost = asyncio.Event()
        guarded_task: asyncio.Task[T] = asyncio.create_task(coro)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(token, guarded_task, lock_lost)
        )
        try:
            try:
                value = await guarded_task
            except asyncio.CancelledError:
                if not lock_lost.is_set():
                    raise
                return GuardedResult(value=None, lock_lost=True)
            return GuardedResult(value=value, lock_lost=False)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if not guarded_task.done():
                guarded_task.cancel()
                await asyncio.gather(guarded_task, return_exceptions=True)
