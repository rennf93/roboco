"""expires_at is now stamped at creation (NotificationService) and actually
matched by NotificationDeliveryService.sweep_expired_notifications' SQL
WHERE clause — before the fix the column was never written, so this query
always matched zero rows regardless of how stale a notification was.

Integration tests against the migrated Postgres DB: `sweep_expired_notifications`
issues a real `expires_at < now()` query, so a mocked session (as
`tests/unit/services/test_notification_delivery.py` uses) can't exercise it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.config import settings
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import Team
from roboco.models.notification import CreateNotificationParams
from roboco.services.notification import NotificationService
from roboco.services.notification_delivery import (
    NotificationDeliveryService,
    defer_after_commit,
    get_notification_delivery_service,
)
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(autouse=True)
async def _committed_notification_senders(
    _test_database_url: str,
) -> AsyncIterator[list[UUID]]:
    """Cleans up NotificationTable rows a test durably committed.

    The sweep's per-row commit (#Correction 2) means a test that reaches
    `sweep_expired_notifications` leaves its notification row(s) durable in
    this session-scoped shared test DB — NOT teardown-rolled-back like
    `db_session`'s own uncommitted work — which then pollutes a LATER test
    file's system-wide notification listing (`test_notification_system_list`).

    A test that commits appends its seeded sender agent id(s) to the yielded
    list. A re-escalation row always inherits the ORIGINAL notification's
    `from_agent` (see `_re_escalate_recipient`), so deleting every
    NotificationTable row `from_agent`-matched to a test's sender(s) catches
    both the original row and anything it spawned — the most precise handle
    available, and it needs no separate tracking of recipient/target ids or
    the re-escalation rows' own ids.

    Runs on a wholly separate engine/connection (independent of `db_session`'s
    own transaction state) so it always reaches Postgres regardless of what
    `db_session` itself still has open when this fixture tears down.
    """
    sender_ids: list[UUID] = []
    yield sender_ids
    if not sender_ids:
        return
    engine = create_async_engine(_test_database_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                delete(NotificationTable).where(
                    NotificationTable.from_agent.in_(sender_ids)
                )
            )
    finally:
        await engine.dispose()


async def _await_drain(session: AsyncSession) -> None:
    """Await any `defer_after_commit` drain tasks stashed on the session
    (mirrors `test_notification_delivery_phantom.py`'s helper) — the real
    drain is fire-and-forget via `asyncio.create_task`, so a test must await
    it explicitly rather than racing the event loop."""
    tasks = list(session.info.get("_roboco_drain_tasks", []))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _stale_unacked_count(session: AsyncSession) -> int:
    """Mirrors `sweep_expired_notifications`'s own stale-unacked predicate.

    The sweep's per-row commit (already true before this file's fixes —
    see the `>= 1` assertions above) makes prior tests' rows durable in this
    session-scoped shared test DB rather than teardown-rolled-back, so a new
    test can't assert an exact sweep `count` — it must diff against this
    baseline instead.
    """
    result = await session.execute(
        select(NotificationTable).where(
            and_(
                NotificationTable.expires_at.is_not(None),
                NotificationTable.expires_at < datetime.now(UTC),
                NotificationTable.requires_ack.is_(True),
            )
        )
    )
    return sum(
        1
        for n in result.scalars().all()
        if not NotificationDeliveryService._notification_is_fully_acked(n)
    )


async def _seed_agent(db: AsyncSession, *, role: AgentRole, slug: str) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt=slug,
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    return cast("UUID", agent.id)


@pytest.mark.asyncio
async def test_created_notification_expires_at_is_stamped_and_matched_by_sweep(
    db_session: AsyncSession,
    _committed_notification_senders: list[UUID],
) -> None:
    """End-to-end: NotificationService._create_notification stamps expires_at
    for an ack-required row, and once that deadline is in the past,
    sweep_expired_notifications' real Postgres query finds it (count 1) —
    the exact round trip that was a dead no-op before this fix, since
    expires_at was always NULL and `expires_at < now()` never matched."""
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"sndr-{unique}"
    )
    recipient = await _seed_agent(
        db_session, role=AgentRole.CELL_PM, slug=f"pm-{unique}"
    )
    _committed_notification_senders.append(sender)

    svc = NotificationService()
    await svc._create_notification(
        CreateNotificationParams(
            notification_type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=str(sender),
            to_agents=[str(recipient)],
            subject="blocked",
            body="external dependency",
        ),
        db_session=db_session,
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
                NotificationTable.from_agent == sender,
            )
        )
    ).scalar_one()
    assert row.expires_at is not None
    assert row.requires_ack is True

    # Backdate it past the deadline (no real clock wait) and confirm the
    # sweep's `expires_at < now()` predicate now actually matches.
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    assert count >= 1


@pytest.mark.asyncio
async def test_directly_stamped_expired_row_is_matched_by_sweep_query(
    db_session: AsyncSession,
    _committed_notification_senders: list[UUID],
) -> None:
    """Isolates the sweep query mechanics from creation: a hand-built
    ack-required, unacked row with expires_at in the past must be counted."""
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"s2-{unique}"
    )
    recipient = await _seed_agent(db_session, role=AgentRole.QA, slug=f"r2-{unique}")
    _committed_notification_senders.append(sender)

    notification = NotificationTable(
        type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[recipient],
        subject="stale alert",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(notification)
    await db_session.flush()

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    assert count >= 1


@pytest.mark.asyncio
async def test_zero_ttl_disables_expires_at_stamping_end_to_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notification_ack_ttl_hours=0 leaves expires_at NULL even for an
    ack-required notification created through the real service."""
    monkeypatch.setattr(settings, "notification_ack_ttl_hours", 0)
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"s3-{unique}"
    )
    recipient = await _seed_agent(
        db_session, role=AgentRole.CELL_PM, slug=f"pm3-{unique}"
    )

    svc = NotificationService()
    await svc._create_notification(
        CreateNotificationParams(
            notification_type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=str(sender),
            to_agents=[str(recipient)],
            subject="blocked",
            body="external dependency",
        ),
        db_session=db_session,
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
                NotificationTable.from_agent == sender,
            )
        )
    ).scalar_one()
    assert row.expires_at is None


# =============================================================================
# defer_after_commit vs. begin_nested() savepoints
#
# `_re_escalate_recipient` wraps `_persist_and_deliver` (which calls
# `deliver()` -> `defer_bus_publish` -> `defer_after_commit`) in a
# `begin_nested()` savepoint, per row, inside this very sweep. SQLAlchemy
# dispatches `after_commit`/`after_rollback` on a SAVEPOINT release/rollback
# too (verified live against this Postgres), so without the fix the pending
# work would drain right there — before the sweep's own real per-row
# `session.commit()` — reintroducing the phantom-notification bug the
# outbox exists to prevent.
# =============================================================================


@pytest.mark.asyncio
async def test_defer_after_commit_does_not_drain_at_savepoint_release(
    db_session: AsyncSession,
) -> None:
    """A `begin_nested()` release must NOT drain pending work — only the
    real root commit may."""
    ran: list[str] = []

    async def _work() -> None:
        ran.append("ran")

    defer_after_commit(db_session, _work)

    async with db_session.begin_nested():
        pass  # savepoint opens and releases; the root transaction stays open

    await _await_drain(db_session)
    assert ran == []  # not drained at the savepoint boundary

    await db_session.commit()
    await _await_drain(db_session)
    assert ran == ["ran"]  # drained at the real root commit


@pytest.mark.asyncio
async def test_defer_after_commit_discarded_on_root_rollback(
    db_session: AsyncSession,
) -> None:
    """A real root rollback discards pending work — no phantom event for
    work whose enclosing transaction never became durable."""
    await db_session.execute(select(1))  # force a real root txn to open

    ran: list[str] = []

    async def _work() -> None:
        ran.append("ran")

    defer_after_commit(db_session, _work)
    await db_session.rollback()
    await _await_drain(db_session)

    assert ran == []


# =============================================================================
# Sweep per-row isolation (#Correction 1/2): one row's failure must not
# corrupt or block another row's processing in the same tick, and a row
# that does succeed must be durably committed regardless.
# =============================================================================


@pytest.mark.asyncio
async def test_sweep_delivers_via_resolvable_chain_and_commit_is_durable(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    _test_database_url: str,
    _committed_notification_senders: list[UUID],
) -> None:
    """The 3 tests above never seed a resolvable escalation chain, so
    `_re_escalate_recipient` always short-circuits at `get_escalation_target`
    returning None before ever reaching `_persist_and_deliver` — a due row's
    real delivery path stays unexercised. Seed one row with a resolvable
    chain plus a second row whose processing is forced to raise, and confirm
    the first row really delivers (a new escalation NotificationTable row
    addressed to the resolved target) and its `reescalation_count` bump is
    durably committed — visible from a wholly separate connection, not just
    this session's own (rollback-able) view — regardless of the other row's
    failure.
    """
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"snd-{unique}"
    )
    recipient = await _seed_agent(
        db_session, role=AgentRole.CELL_PM, slug=f"rcp-{unique}"
    )
    target = await _seed_agent(db_session, role=AgentRole.MAIN_PM, slug=f"tgt-{unique}")
    _committed_notification_senders.append(sender)
    baseline = await _stale_unacked_count(db_session)

    good = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[recipient],
        subject=f"good-{unique}",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    bad = NotificationTable(
        type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[recipient],
        subject=f"bad-{unique}",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add_all([good, bad])
    await db_session.flush()
    good_id = cast("UUID", good.id)
    bad_id = cast("UUID", bad.id)

    # This test only needs the escalation chain to resolve; who it resolves
    # to doesn't depend on the recipient slug (real `ESCALATION_CHAIN` keys
    # are fixed strings we can't safely reuse across tests without colliding
    # on `agents.slug`'s uniqueness once the fix's per-row commit makes these
    # rows durable rather than teardown-rolled-back).
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_escalation_target",
        lambda _slug: f"tgt-{unique}",
    )

    # Fault-inject in `_re_escalate_unacked`, not `_maybe_reescalate` itself:
    # the sweep has no ORDER BY, so whichever row it reaches first must not
    # matter. `_maybe_reescalate`'s own CAS-claim commit runs BEFORE this
    # call, so by the time "bad" raises, that commit has already flushed
    # whatever was pending — including the initial insert of BOTH rows,
    # which were only `flush()`-ed (not committed) before this sweep call.
    # Raising any earlier (inside `_maybe_reescalate` itself, before its own
    # commit) would let "bad" processed first roll back "good"'s still-
    # uncommitted insert too — an order dependency, not a real assertion.
    orig_re_escalate_unacked = NotificationDeliveryService._re_escalate_unacked

    async def _re_escalate_unacked_one_fails(
        self: NotificationDeliveryService, n: NotificationTable
    ) -> int:
        if n.id == bad_id:
            raise RuntimeError("simulated processing failure")
        return await orig_re_escalate_unacked(self, n)

    monkeypatch.setattr(
        NotificationDeliveryService,
        "_re_escalate_unacked",
        _re_escalate_unacked_one_fails,
    )

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    # Both new rows still counted as stale + unacked this tick, on top of
    # whatever earlier tests in this session-scoped DB already committed.
    assert count == baseline + 2

    # Verify durability from a SEPARATE connection bound to the same test
    # DB — proves the good row's commit really reached Postgres, not just
    # this session's own still-mutable view.
    verify_engine = create_async_engine(_test_database_url, future=True)
    try:
        verify_factory = async_sessionmaker(bind=verify_engine, class_=AsyncSession)
        async with verify_factory() as verify_session:
            good_row = (
                await verify_session.execute(
                    select(NotificationTable).where(NotificationTable.id == good_id)
                )
            ).scalar_one()
            assert good_row.reescalation_count == 1
            assert good_row.reescalation_delivered_count == 1

            escalated = (
                (
                    await verify_session.execute(
                        select(NotificationTable).where(
                            NotificationTable.to_agents.contains([target])
                        )
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await verify_engine.dispose()

    assert any(f"good-{unique}" in (e.subject or "") for e in escalated)


@pytest.mark.asyncio
async def test_sweep_one_row_failure_does_not_block_the_other_rows_processing(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    _test_database_url: str,
    _committed_notification_senders: list[UUID],
) -> None:
    """Before the fix, a root `rollback()` following one row's exception
    expired every object still held from the earlier SELECT, so whichever
    row `unacked` iterated to next raised `MissingGreenlet` on its own
    attribute access (an async lazy-refresh attempted in a sync/greenlet
    context) — aborting the whole tick instead of just skipping the bad row.
    Snapshotting ids and re-fetching each row via `session.get` (async-safe
    even post-expiry) makes every row's processing independent of any
    earlier row's failure."""
    unique = uuid4().hex[:8]
    sender = await _seed_agent(db_session, role=AgentRole.DEVELOPER, slug=f"s-{unique}")
    good_recipient = await _seed_agent(
        db_session, role=AgentRole.QA, slug=f"gr-{unique}"
    )
    bad_recipient = await _seed_agent(
        db_session, role=AgentRole.QA, slug=f"br-{unique}"
    )
    _committed_notification_senders.append(sender)
    baseline = await _stale_unacked_count(db_session)

    good = NotificationTable(
        type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[good_recipient],
        subject=f"good-{unique}",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    bad = NotificationTable(
        type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[bad_recipient],
        subject=f"bad-{unique}",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add_all([good, bad])
    await db_session.flush()
    good_id = cast("UUID", good.id)
    bad_id = cast("UUID", bad.id)

    # No resolvable chain for either row — isolates this test to the
    # row-isolation property alone (delivery-path coverage is the other test).
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_escalation_target",
        lambda _slug: None,
    )

    orig_re_escalate_unacked = NotificationDeliveryService._re_escalate_unacked

    async def _re_escalate_unacked_one_fails(
        self: NotificationDeliveryService, n: NotificationTable
    ) -> int:
        if n.id == bad_id:
            raise RuntimeError("simulated delivery failure")
        return await orig_re_escalate_unacked(self, n)

    monkeypatch.setattr(
        NotificationDeliveryService,
        "_re_escalate_unacked",
        _re_escalate_unacked_one_fails,
    )

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    assert count == baseline + 2  # both new rows still stale + unacked

    verify_engine = create_async_engine(_test_database_url, future=True)
    try:
        verify_factory = async_sessionmaker(bind=verify_engine, class_=AsyncSession)
        async with verify_factory() as verify_session:
            good_row = (
                await verify_session.execute(
                    select(NotificationTable).where(NotificationTable.id == good_id)
                )
            ).scalar_one()
            bad_row = (
                await verify_session.execute(
                    select(NotificationTable).where(NotificationTable.id == bad_id)
                )
            ).scalar_one()
    finally:
        await verify_engine.dispose()

    # The good row was fully processed (attempt slot claimed + committed)
    # independent of whatever happened to the bad row.
    assert good_row.reescalation_count == 1
    # The bad row's CAS-claim commit (before the raise) is also durable —
    # the raise happens in `_re_escalate_unacked`, strictly after that commit.
    assert bad_row.reescalation_count == 1
    assert bad_row.reescalation_delivered_count == 0
