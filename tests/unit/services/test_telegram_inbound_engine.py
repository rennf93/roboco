"""TelegramInboundEngine coverage: chat-id rejection, offset advancement, and
one test per approve/reject-kind dispatching to a mocked service (asserting
CEO identity + reason threading). Every service factory the engine calls is
monkeypatched module-level — no DB, no network."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services import telegram_inbound as ti
from roboco.services.roadmap_service import RoadmapItemResult
from roboco.services.telegram_credentials import TelegramCredentialsData
from roboco.services.video_post_service import VideoPostExecuteResult
from roboco.services.x_post_service import XPostExecuteResult

CEO_UUID = ti._CEO_UUID


def _uuid_with_prefix(prefix: str) -> UUID:
    """A real UUID whose ``str(uuid)[:8] == prefix`` — the id8 convention."""
    return UUID(hex=prefix + uuid4().hex[len(prefix) :])


def _fake_task(id8: str = "a1b2c3d4", title: str = "Test task") -> SimpleNamespace:
    return SimpleNamespace(
        id=_uuid_with_prefix(id8),
        title=title,
        description="",
        pr_url=None,
        status=SimpleNamespace(value="pending"),
        team=None,
    )


def _fake_session() -> MagicMock:
    """``session.add`` is sync in real SQLAlchemy (a plain MagicMock call, no
    "never awaited" warning); only the awaited methods this engine actually
    uses get an AsyncMock."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


def _engine() -> ti.TelegramInboundEngine:
    """A bare engine over a mocked session — nothing in these tests touches
    real DB rows, only the monkeypatched service factories."""
    return ti.TelegramInboundEngine(_fake_session())


CREDS = TelegramCredentialsData(bot_token="123:ABC", chat_id="777")


# ---------------------------------------------------------------------------
# chat-id rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorized_chat_message_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    client = AsyncMock()

    await engine._handle_message(
        {"chat": {"id": 999}, "text": "/status"}, CREDS, client
    )

    dispatch.assert_not_called()
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_chat_message_dispatches_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    client = AsyncMock()

    await engine._handle_message(
        {"chat": {"id": 777}, "text": "/status"}, CREDS, client
    )

    dispatch.assert_awaited_once_with("status", "", client)


@pytest.mark.asyncio
async def test_unauthorized_chat_callback_answers_not_authorized() -> None:
    engine = _engine()
    client = AsyncMock()

    await engine._handle_callback(
        {
            "id": "cq1",
            "data": "apv:xpost:a1b2c3d4",
            "message": {"chat": {"id": 999}, "message_id": 5},
        },
        CREDS,
        client,
    )

    client.answer_callback_query.assert_awaited_once_with("cq1", "Not authorized")
    client.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# sender identity — defense-in-depth on top of chat-id authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_with_mismatched_sender_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The right chat, but a `from.id` that disagrees with it (would only
    happen if the "private" chat somehow carried a second poster) — dropped
    silently, same as an unauthorized chat."""
    engine = _engine()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    client = AsyncMock()

    await engine._handle_message(
        {"chat": {"id": 777}, "from": {"id": 999}, "text": "/status"}, CREDS, client
    )

    dispatch.assert_not_called()
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_message_with_matching_sender_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    client = AsyncMock()

    await engine._handle_message(
        {"chat": {"id": 777}, "from": {"id": 777}, "text": "/status"}, CREDS, client
    )

    dispatch.assert_awaited_once_with("status", "", client)


@pytest.mark.asyncio
async def test_message_without_from_keeps_prior_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `from` on the update at all — the pre-Fix-2 behavior (chat-id-only)
    is unchanged."""
    engine = _engine()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    client = AsyncMock()

    await engine._handle_message(
        {"chat": {"id": 777}, "text": "/status"}, CREDS, client
    )

    dispatch.assert_awaited_once_with("status", "", client)


@pytest.mark.asyncio
async def test_callback_with_mismatched_sender_answers_not_authorized() -> None:
    engine = _engine()
    client = AsyncMock()

    await engine._handle_callback(
        {
            "id": "cq1",
            "data": "apv:xpost:a1b2c3d4",
            "from": {"id": 999},
            "message": {"chat": {"id": 777}, "message_id": 5},
        },
        CREDS,
        client,
    )

    client.answer_callback_query.assert_awaited_once_with("cq1", "Not authorized")
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_callback_with_matching_sender_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    client = AsyncMock()
    dispatch = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(engine, "_dispatch_approve", dispatch)

    await engine._handle_callback(
        {
            "id": "cq1",
            "data": "apv:xpost:a1b2c3d4",
            "from": {"id": 777},
            "message": {"chat": {"id": 777}, "message_id": 5},
        },
        CREDS,
        client,
    )

    dispatch.assert_awaited_once()
    client.answer_callback_query.assert_any_await("cq1", "Working...")


# ---------------------------------------------------------------------------
# expired force-reply prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_reply_prompt_sends_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A popped-but-expired pending prompt must tell the CEO instead of
    silently doing nothing (the CEO otherwise has no idea why their reply had
    no effect)."""
    engine = _engine()
    client = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(engine, "_dispatch_command", dispatch)
    ti._PENDING_REPLIES[("777", 42)] = ti._PendingAction(
        kind="xpost",
        id8="a1b2c3d4",
        extra="",
        action="reject",
        origin_message_id=10,
        expires_at=time.monotonic() - 1,  # already expired
    )

    await engine._handle_message(
        {
            "chat": {"id": 777},
            "text": "some reason",
            "reply_to_message": {"message_id": 42},
        },
        CREDS,
        client,
    )

    client.send_message.assert_awaited_once_with(
        "That prompt expired — tap the button again.", parse_mode="HTML"
    )
    dispatch.assert_not_called()
    assert ("777", 42) not in ti._PENDING_REPLIES


# ---------------------------------------------------------------------------
# offset advancement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_noop_when_flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ti.settings, "telegram_enabled", False)
    monkeypatch.setattr(ti.settings, "telegram_inbound_enabled", True)
    engine = _engine()
    creds_svc = AsyncMock()
    monkeypatch.setattr(
        ti, "get_telegram_credentials_service", lambda _session: creds_svc
    )

    await engine.run_cycle()

    creds_svc.get_decrypted.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_noop_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti.settings, "telegram_enabled", True)
    monkeypatch.setattr(ti.settings, "telegram_inbound_enabled", True)
    engine = _engine()
    creds_svc = AsyncMock()
    creds_svc.get_decrypted = AsyncMock(return_value=None)
    monkeypatch.setattr(
        ti, "get_telegram_credentials_service", lambda _session: creds_svc
    )

    await engine.run_cycle()

    creds_svc.get_decrypted.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_cycle_advances_offset_past_highest_update_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti.settings, "telegram_enabled", True)
    monkeypatch.setattr(ti.settings, "telegram_inbound_enabled", True)
    engine = _engine()

    creds_svc = AsyncMock()
    creds_svc.get_decrypted = AsyncMock(return_value=CREDS)
    monkeypatch.setattr(
        ti, "get_telegram_credentials_service", lambda _session: creds_svc
    )

    client = AsyncMock()
    client.configured = True
    # Two updates the engine ignores (no message/callback_query key) — only
    # the offset bookkeeping is under test here.
    client.get_updates = AsyncMock(
        return_value=[{"update_id": 100}, {"update_id": 105}]
    )
    monkeypatch.setattr(engine, "_client", AsyncMock(return_value=client))

    settings_svc = AsyncMock()
    settings_svc.get_int = AsyncMock(return_value=0)
    monkeypatch.setattr(ti, "get_settings_service", lambda _session: settings_svc)

    await engine.run_cycle()

    client.get_updates.assert_awaited_once_with(offset=None, timeout=25, limit=50)
    settings_svc.set.assert_awaited_once_with("telegram_last_update_id", "106")


@pytest.mark.asyncio
async def test_run_cycle_requests_stored_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti.settings, "telegram_enabled", True)
    monkeypatch.setattr(ti.settings, "telegram_inbound_enabled", True)
    engine = _engine()

    creds_svc = AsyncMock()
    creds_svc.get_decrypted = AsyncMock(return_value=CREDS)
    monkeypatch.setattr(
        ti, "get_telegram_credentials_service", lambda _session: creds_svc
    )

    client = AsyncMock()
    client.configured = True
    client.get_updates = AsyncMock(return_value=[])
    monkeypatch.setattr(engine, "_client", AsyncMock(return_value=client))

    settings_svc = AsyncMock()
    settings_svc.get_int = AsyncMock(return_value=42)
    monkeypatch.setattr(ti, "get_settings_service", lambda _session: settings_svc)

    await engine.run_cycle()

    client.get_updates.assert_awaited_once_with(offset=42, timeout=25, limit=50)
    # No updates seen -> offset must not regress/rewrite.
    settings_svc.set.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_task — exact id-prefix match, ambiguity handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_task_exact_prefix_match(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    task = _fake_task("a1b2c3d4")
    task_svc = AsyncMock()
    task_svc.search_tasks = AsyncMock(return_value=[task])
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    resolved = await engine._resolve_task("a1b2c3d4")

    # Widened from 10 -> 50: a real id-prefix hit can otherwise be pushed out
    # of a small window by title/description ILIKE hits on newer rows.
    task_svc.search_tasks.assert_awaited_once_with("a1b2c3d4", limit=50)
    assert resolved is task


@pytest.mark.asyncio
async def test_resolve_task_ambiguous_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    # Both genuinely start with the same prefix -> ambiguous.
    t1 = _fake_task("a1b2c3d4")
    t2 = SimpleNamespace(id=UUID(hex="a1b2c3d4" + "0" * 24), title="dup")
    task_svc = AsyncMock()
    task_svc.search_tasks = AsyncMock(return_value=[t1, t2])
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    assert await engine._resolve_task("a1b2c3d4") is None


@pytest.mark.asyncio
async def test_resolve_task_filters_out_title_only_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_tasks also OR-matches title/description substrings; a hit whose
    id does NOT start with the query must be filtered back out."""
    engine = _engine()
    real = _fake_task("a1b2c3d4")
    title_hit = _fake_task("ffffffff", title="mentions a1b2c3d4 in the title")
    task_svc = AsyncMock()
    task_svc.search_tasks = AsyncMock(return_value=[real, title_hit])
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    assert await engine._resolve_task("a1b2c3d4") is real


# ---------------------------------------------------------------------------
# dispatch: one per approve-kind
# ---------------------------------------------------------------------------


def _stub_resolve(
    monkeypatch: pytest.MonkeyPatch, engine: ti.TelegramInboundEngine, task: Any
) -> None:
    monkeypatch.setattr(engine, "_resolve_task", AsyncMock(return_value=task))


@pytest.mark.asyncio
async def test_dispatch_approve_task_calls_ceo_approve_with_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    task_svc = AsyncMock()
    task_svc.ceo_approve = AsyncMock(return_value=task)
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    notes = "Looks solid, shipping it now."
    ok, _text = await engine._dispatch_approve("task", "a1b2c3d4", "", notes=notes)

    task_svc.ceo_approve.assert_awaited_once_with(task.id, notes)
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_approve_task_refuses_short_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    task_svc = AsyncMock()
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    ok, text = await engine._dispatch_approve("task", "a1b2c3d4", "", notes="too short")

    task_svc.ceo_approve.assert_not_called()
    assert ok is False
    assert "20" in text


@pytest.mark.asyncio
async def test_dispatch_reject_task_calls_ceo_reject_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    task_svc = AsyncMock()
    task_svc.ceo_reject = AsyncMock(return_value=task)
    monkeypatch.setattr(ti, "get_task_service", lambda _session: task_svc)

    ok, _text = await engine._dispatch_reject("task", "a1b2c3d4", "", "not good enough")

    task_svc.ceo_reject.assert_awaited_once_with(task.id, "not good enough")
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_approve_release_dispatches_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    dispatch_mock = MagicMock()
    monkeypatch.setattr(ti, "dispatch_approve", dispatch_mock)
    factory = object()
    monkeypatch.setattr(ti, "get_session_factory", lambda: factory)

    ok, text = await engine._dispatch_approve("release", "a1b2c3d4", "", notes=None)

    dispatch_mock.assert_called_once_with(task.id, factory)
    assert ok is True
    assert "background" in text


@pytest.mark.asyncio
async def test_dispatch_approve_release_refuses_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatch_approve fires the release execute in the background with no
    result to inspect, so a stale Approve on an already-rejected (CANCELLED)
    proposal must be caught HERE, before dispatch — else the CEO sees a false
    "dispatched" success while the service-level guard silently no-ops."""
    engine = _engine()
    task = _fake_task()
    task.status = TaskStatus.CANCELLED
    _stub_resolve(monkeypatch, engine, task)
    dispatch_mock = MagicMock()
    monkeypatch.setattr(ti, "dispatch_approve", dispatch_mock)

    ok, text = await engine._dispatch_approve("release", "a1b2c3d4", "", notes=None)

    dispatch_mock.assert_not_called()
    assert ok is False
    assert "already rejected" in text.lower()


@pytest.mark.asyncio
async def test_dispatch_reject_release_surfaces_already_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reject-after-approve mirror guard: the service raises when the
    proposal already published; the handler must map that to (False, ...)
    instead of an uncaught exception (which `run_cycle`'s broad except would
    swallow, leaving the CEO with no response at all)."""
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    release_svc = AsyncMock()
    release_svc.reject = AsyncMock(
        side_effect=ti._ReleaseDone(
            "release proposal already published (COMPLETED); cannot be rejected"
        )
    )
    monkeypatch.setattr(
        ti, "get_release_proposal_service", lambda _session: release_svc
    )

    ok, text = await engine._dispatch_reject(
        "release", "a1b2c3d4", "", "needs another migration check"
    )

    assert ok is False
    assert "already published" in text


@pytest.mark.asyncio
async def test_dispatch_reject_release_calls_service_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    release_svc = AsyncMock()
    release_svc.reject = AsyncMock(return_value=task)
    monkeypatch.setattr(
        ti, "get_release_proposal_service", lambda _session: release_svc
    )

    ok, _text = await engine._dispatch_reject(
        "release", "a1b2c3d4", "", "needs another migration check"
    )

    release_svc.reject.assert_awaited_once_with(
        task.id, "needs another migration check"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_reject_release_enforces_ten_char_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    release_svc = AsyncMock()
    monkeypatch.setattr(
        ti, "get_release_proposal_service", lambda _session: release_svc
    )

    ok, text = await engine._dispatch_reject("release", "a1b2c3d4", "", "short")

    release_svc.reject.assert_not_called()
    assert ok is False
    assert "not recorded" in text


@pytest.mark.asyncio
async def test_dispatch_approve_xpost_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    x_svc = AsyncMock()
    x_svc.approve = AsyncMock(
        return_value=XPostExecuteResult(status="posted", tweet_id="1", detail="ok")
    )
    monkeypatch.setattr(ti, "get_x_post_service", lambda _session: x_svc)

    ok, _text = await engine._dispatch_approve("xpost", "a1b2c3d4", "", notes=None)

    x_svc.approve.assert_awaited_once_with(task.id)
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_reject_xpost_calls_service_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    x_svc = AsyncMock()
    x_svc.reject = AsyncMock(return_value=task)
    monkeypatch.setattr(ti, "get_x_post_service", lambda _session: x_svc)

    ok, _text = await engine._dispatch_reject("xpost", "a1b2c3d4", "", "off-brand tone")

    x_svc.reject.assert_awaited_once_with(task.id, "off-brand tone")
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_approve_video_calls_real_video_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    video_svc = AsyncMock()
    video_svc.approve = AsyncMock(
        return_value=VideoPostExecuteResult(
            status="posted", posted={"x": "1"}, detail="ok"
        )
    )
    monkeypatch.setattr(
        engine, "_real_video_post_service", AsyncMock(return_value=video_svc)
    )

    ok, _text = await engine._dispatch_approve("video", "a1b2c3d4", "", notes=None)

    video_svc.approve.assert_awaited_once_with(task.id)
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_reject_video_calls_service_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    video_svc = AsyncMock()
    video_svc.reject = AsyncMock(return_value=task)
    monkeypatch.setattr(ti, "get_video_post_service", lambda _session: video_svc)

    ok, _text = await engine._dispatch_reject("video", "a1b2c3d4", "", "wrong caption")

    video_svc.reject.assert_awaited_once_with(task.id, "wrong caption")
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_approve_roadmap_calls_service_with_ceo_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    roadmap_svc = AsyncMock()
    roadmap_svc.approve_item = AsyncMock(
        return_value=RoadmapItemResult(
            status="approved", item_id="item-2", materialized_task_id="x", detail="ok"
        )
    )
    monkeypatch.setattr(ti, "get_roadmap_service", lambda _session: roadmap_svc)

    ok, _text = await engine._dispatch_approve(
        "roadmap", "a1b2c3d4", "item-2", notes=None
    )

    roadmap_svc.approve_item.assert_awaited_once_with(
        task.id, "item-2", created_by=CEO_UUID
    )
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_reject_roadmap_calls_service_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    task = _fake_task()
    _stub_resolve(monkeypatch, engine, task)
    roadmap_svc = AsyncMock()
    roadmap_svc.reject_item = AsyncMock(
        return_value=RoadmapItemResult(
            status="rejected", item_id="item-2", materialized_task_id=None, detail="ok"
        )
    )
    monkeypatch.setattr(ti, "get_roadmap_service", lambda _session: roadmap_svc)

    ok, _text = await engine._dispatch_reject(
        "roadmap", "a1b2c3d4", "item-2", "not aligned with strategy"
    )

    roadmap_svc.reject_item.assert_awaited_once_with(
        task.id, "item-2", "not aligned with strategy"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_approve_unresolved_task_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    _stub_resolve(monkeypatch, engine, None)

    ok, text = await engine._dispatch_approve("xpost", "ffffffff", "", notes=None)

    assert ok is False
    assert "No such" in text


# ---------------------------------------------------------------------------
# audit marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_audit_adds_via_telegram_row() -> None:
    engine = _engine()
    task_id = uuid4()

    engine._mark_audit("xpost", task_id, "approve", item_id="")

    added = cast("MagicMock", engine.session.add).call_args.args[0]
    assert added.event_type == "telegram.xpost.approve"
    assert added.agent_id == CEO_UUID
    assert added.target_id == task_id
    assert added.details["via"] == "telegram"


# ---------------------------------------------------------------------------
# /queue rendering — pluralization + HTML formatting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_queue_empty_says_nothing_awaiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(engine, "_collect_queue_items", AsyncMock(return_value=[]))
    client = AsyncMock()

    await engine._send_queue(client)

    client.send_message.assert_awaited_once_with(
        "✅ Nothing awaiting your approval.", parse_mode="HTML"
    )


@pytest.mark.asyncio
async def test_send_queue_singular_item_pluralization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(
        engine,
        "_collect_queue_items",
        AsyncMock(return_value=[("task", "a1b2c3d4", "", "Ship it")]),
    )
    client = AsyncMock()

    await engine._send_queue(client)

    header = client.send_message.await_args_list[0].args[0]
    assert header == "<b>🔔 Awaiting your approval</b> — 1 item"


@pytest.mark.asyncio
async def test_send_queue_plural_items_pluralization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(
        engine,
        "_collect_queue_items",
        AsyncMock(
            return_value=[
                ("task", "a1b2c3d4", "", "Ship it"),
                ("release", "deadbeef", "", "v1.0.0 ready"),
            ]
        ),
    )
    client = AsyncMock()

    await engine._send_queue(client)

    header = client.send_message.await_args_list[0].args[0]
    assert header == "<b>🔔 Awaiting your approval</b> — 2 items"


@pytest.mark.asyncio
async def test_send_queue_item_line_escapes_title_and_carries_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injection regression: a malicious task title must arrive HTML-escaped
    — never as live markup — in the /queue item line's sent payload."""
    engine = _engine()
    monkeypatch.setattr(
        engine,
        "_collect_queue_items",
        AsyncMock(return_value=[("task", "a1b2c3d4", "", "<b>bold&joke</b>")]),
    )
    client = AsyncMock()

    await engine._send_queue(client)

    item_call = client.send_message.await_args_list[1]
    text = item_call.args[0]
    assert "&lt;b&gt;bold&amp;joke&lt;/b&gt;" in text
    assert "<b>bold&joke</b>" not in text
    assert text.startswith("📋 <b>Task</b> — ")
    assert item_call.kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in item_call.kwargs


# ---------------------------------------------------------------------------
# /task — link preview disabled, title/status/team escaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_command_task_disables_link_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(engine, "_render_task", AsyncMock(return_value="detail"))
    client = AsyncMock()

    await engine._dispatch_command("task", "a1b2c3d4", client)

    client.send_message.assert_awaited_once_with(
        "detail", parse_mode="HTML", disable_link_preview=True
    )


def test_format_task_detail_escapes_html_in_title() -> None:
    """Injection regression: a task titled ``<b>bold&joke</b>`` must render
    HTML-escaped, not as live markup, in /task's detail view."""
    engine = _engine()
    task = _fake_task(title="<b>bold&joke</b>")

    rendered = engine._format_task_detail(task)

    assert "&lt;b&gt;bold&amp;joke&lt;/b&gt;" in rendered
    assert "<b>bold&joke</b>" not in rendered


def test_format_task_detail_pr_url_is_a_named_link() -> None:
    engine = _engine()
    task = _fake_task()
    task.pr_url = "https://github.com/example/repo/pull/1"

    rendered = engine._format_task_detail(task)

    assert '<a href="https://github.com/example/repo/pull/1">View PR</a>' in rendered


# ---------------------------------------------------------------------------
# outcome confirmations (_finish_action / _consume_reply) — escaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_action_escapes_text_and_edits_origin() -> None:
    engine = _engine()
    client = AsyncMock()

    await engine._finish_action(client, 42, True, "Rejected: <script>xss</script>")

    client.edit_message_reply_markup.assert_awaited_once_with(42, None)
    call = client.edit_message_text.await_args
    assert call.args == (42, "✅ Rejected: &lt;script&gt;xss&lt;/script&gt;")
    assert call.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_finish_action_escapes_text_without_origin() -> None:
    engine = _engine()
    client = AsyncMock()

    await engine._finish_action(client, None, False, "<script>alert(1)</script>")

    call = client.send_message.await_args
    assert call.args == ("❌ &lt;script&gt;alert(1)&lt;/script&gt;",)
    assert call.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_consume_reply_reject_outcome_arrives_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end regression: a reject reason that reaches the CEO through
    whatever text a dispatch handler returns must never arrive as live HTML —
    the funnel (_finish_action) escapes it regardless of the handler."""
    engine = _engine()
    client = AsyncMock()
    dispatch = AsyncMock(return_value=(True, "Rejected: <script>xss</script>"))
    monkeypatch.setattr(engine, "_dispatch_reject", dispatch)
    pending = ti._PendingAction(
        kind="xpost",
        id8="a1b2c3d4",
        extra="",
        action="reject",
        origin_message_id=None,
        expires_at=time.monotonic() + 60,
    )

    await engine._consume_reply(pending, "<script>xss</script>", client)

    dispatch.assert_awaited_once_with("xpost", "a1b2c3d4", "", "<script>xss</script>")
    sent_text = client.send_message.await_args.args[0]
    assert "&lt;script&gt;xss&lt;/script&gt;" in sent_text
    assert "<script>xss</script>" not in sent_text
