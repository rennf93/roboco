"""Bounded re-fire guard for loop-prone notification types.

TASK_ASSIGNMENT / REVIEW_REQUEST / DOCUMENTATION_REQUEST / BROADCAST can be
re-fired in a loop (a PM re-notifying the same recipient about the same task
every tick while it sits in a state), flooding inboxes. A short Redis SET-NX
window per (type, sender, recipient, task) suppresses the re-fire. Fail-open:
Redis unavailable → never suppresses. KNOWLEDGE_SHARE / MENTION / A2A_REQUEST
always pass through (one-shot by nature, no dedup key).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models import NotificationType
from roboco.services.notification_dedup import all_recipients_recently_notified

_FAKE_URL = "redis://localhost:6379/0"
_DEDUP_TTL = 60  # mirrors _DEDUP_TTL_SECONDS in the helper
_TWO_RECIPIENTS = 2


def _conn(set_returns: list[object]) -> MagicMock:
    """A fake redis conn whose `.set` returns successive values then None."""
    c = MagicMock()
    c.set = AsyncMock(side_effect=[*set_returns, None])
    c.aclose = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_first_fire_not_suppressed() -> None:
    # First fire for a single recipient: SET NX acquires (True) → not a re-fire.
    conn = _conn([True])
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn
        a = uuid4()
        suppressed = await all_recipients_recently_notified(
            ntype=NotificationType.TASK_ASSIGNMENT,
            from_agent=uuid4(),
            recipients=[a],
            related_task_id=uuid4(),
        )
    assert suppressed is False
    conn.set.assert_awaited_once()
    assert conn.set.call_args.kwargs.get("nx") is True
    assert conn.set.call_args.kwargs.get("ex") == _DEDUP_TTL


@pytest.mark.asyncio
async def test_all_recipients_dup_suppresses() -> None:
    # Two recipients, both already held (SET NX returns None for each) → re-fire.
    conn = _conn([None, None])
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn
        suppressed = await all_recipients_recently_notified(
            ntype=NotificationType.REVIEW_REQUEST,
            from_agent=uuid4(),
            recipients=[uuid4(), uuid4()],
            related_task_id=uuid4(),
        )
    assert suppressed is True
    assert conn.set.await_count == _TWO_RECIPIENTS


@pytest.mark.asyncio
async def test_mixed_recipients_not_suppressed() -> None:
    # One fresh (acquired) + one dup → persist (not suppress). The fresh one is
    # acquired (marked) so the next fire converges toward full suppression.
    conn = _conn([True, None])
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn
        suppressed = await all_recipients_recently_notified(
            ntype=NotificationType.DOCUMENTATION_REQUEST,
            from_agent=uuid4(),
            recipients=[uuid4(), uuid4()],
            related_task_id=uuid4(),
        )
    assert suppressed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ntype",
    [
        NotificationType.KNOWLEDGE_SHARE,
        NotificationType.MENTION,
        NotificationType.A2A_REQUEST,
    ],
)
async def test_excluded_types_never_suppressed(ntype: NotificationType) -> None:
    # One-shot types bypass the guard entirely — even if Redis would say dup.
    conn = _conn([None])
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn
        suppressed = await all_recipients_recently_notified(
            ntype=ntype,
            from_agent=uuid4(),
            recipients=[uuid4()],
            related_task_id=uuid4(),
        )
    assert suppressed is False
    conn.set.assert_not_awaited()  # guard short-circuited before touching Redis


@pytest.mark.asyncio
async def test_redis_unavailable_fail_open() -> None:
    # Redis down / from_url raising → never suppress (a notification is never
    # dropped because of the dedup infra).
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.side_effect = RuntimeError("redis down")
        suppressed = await all_recipients_recently_notified(
            ntype=NotificationType.BROADCAST,
            from_agent=uuid4(),
            recipients=[uuid4()],
            related_task_id=None,
        )
    assert suppressed is False


@pytest.mark.asyncio
async def test_empty_recipients_or_no_sender_short_circuits() -> None:
    # Nothing to dedup against → not suppressed, no Redis call.
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = _conn([])
        assert (
            await all_recipients_recently_notified(
                ntype=NotificationType.TASK_ASSIGNMENT,
                from_agent=uuid4(),
                recipients=[],
                related_task_id=uuid4(),
            )
            is False
        )
        assert (
            await all_recipients_recently_notified(
                ntype=NotificationType.TASK_ASSIGNMENT,
                from_agent=None,
                recipients=[uuid4()],
                related_task_id=uuid4(),
            )
            is False
        )
    redis_mod.from_url.assert_not_called()


@pytest.mark.asyncio
async def test_key_carries_type_sender_recipient_and_task() -> None:
    # The dedup identity is (type, sender, recipient, task) — rewording or a
    # different subject must NOT defeat the guard, and 'none' stands in for a
    # taskless broadcast so two broadcasts about nothing still dedup.
    conn = _conn([True])
    sender = uuid4()
    recip = uuid4()
    task = uuid4()
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn
        await all_recipients_recently_notified(
            ntype=NotificationType.TASK_ASSIGNMENT,
            from_agent=sender,
            recipients=[recip],
            related_task_id=task,
        )
    key = conn.set.call_args.args[0]
    assert key == f"roboco:notif_dedup:task_assignment:{sender}:{recip}:{task}"

    conn2 = _conn([True])
    with (
        patch("roboco.services.notification_dedup.settings") as settings,
        patch("roboco.services.notification_dedup.redis") as redis_mod,
    ):
        settings.redis_url = _FAKE_URL
        redis_mod.from_url.return_value = conn2
        await all_recipients_recently_notified(
            ntype=NotificationType.BROADCAST,
            from_agent=sender,
            recipients=[recip],
            related_task_id=None,
        )
    assert (
        conn2.set.call_args.args[0]
        == f"roboco:notif_dedup:broadcast:{sender}:{recip}:none"
    )
