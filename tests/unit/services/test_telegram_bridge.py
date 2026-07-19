"""Telegram ↔ live-chat bridge (P5): session lifecycle, free-text routing,
the stream consumer's turn/draft forwarding, idle sweep, and the engine's
/secretary /newtask /end + intake-callback wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services import telegram_bridge as bridge
from roboco.services import telegram_inbound as ti


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    bridge._SESSIONS.clear()
    bridge._PENDING_NEWTASK.clear()
    yield
    bridge._SESSIONS.clear()
    bridge._PENDING_NEWTASK.clear()


def _fake_session_db() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


def _engine() -> ti.TelegramInboundEngine:
    return ti.TelegramInboundEngine(_fake_session_db())


CREDS = SimpleNamespace(bot_token="123:ABC", chat_id="777")


class FakeRegistry:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = events or []
        self.delivered: list[tuple[str, str]] = []
        self.parked: list[tuple[str, str]] = []

    async def deliver(self, session_id: str, text: str) -> bool:
        self.delivered.append((session_id, text))
        return True

    def park(self, session_id: str, task_id: str) -> bool:
        self.parked.append((session_id, task_id))
        return True

    async def stream(self, _session_id: str):
        for event in self.events:
            yield event


def _bridge_session(
    kind: str = "secretary", *, parked: bool = False
) -> bridge.BridgeSession:
    sess = bridge.BridgeSession(kind=kind, session_id=uuid4().hex, client=AsyncMock())
    sess.parked = parked
    return sess


# ---------------------------------------------------------------------------
# bridge module — lifecycle + routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_text_routes_only_bridged_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry()
    monkeypatch.setattr(
        "roboco.services.prompter_live.get_live_registry", lambda: registry
    )

    assert await bridge.deliver_text("777", "hello") is None

    sess = _bridge_session()
    bridge._SESSIONS["777"] = sess
    assert await bridge.deliver_text("777", "hello") == ""
    assert registry.delivered == [(sess.session_id, "hello")]


@pytest.mark.asyncio
async def test_end_session_reaps_by_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = SimpleNamespace(
        reap_secretary_session=AsyncMock(), reap_intake_session=AsyncMock()
    )
    monkeypatch.setattr(bridge, "_orchestrator", lambda: orch)

    sess = _bridge_session("intake")
    bridge._SESSIONS["777"] = sess
    assert await bridge.end_session("777") == "Ended."
    orch.reap_intake_session.assert_awaited_once_with(sess.session_id)
    assert "777" not in bridge._SESSIONS

    assert await bridge.end_session("777") == "No active session."


@pytest.mark.asyncio
async def test_sweep_idle_skips_parked_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = SimpleNamespace(
        reap_secretary_session=AsyncMock(), reap_intake_session=AsyncMock()
    )
    monkeypatch.setattr(bridge, "_orchestrator", lambda: orch)
    monkeypatch.setattr(bridge.settings, "interactive_idle_reap_seconds", 100)

    idle = _bridge_session("secretary")
    idle.last_user_turn -= 1000
    parked = _bridge_session("intake", parked=True)
    parked.last_user_turn -= 1000
    bridge._SESSIONS["idle-chat"] = idle
    bridge._SESSIONS["parked-chat"] = parked

    await bridge.sweep_idle()

    assert "idle-chat" not in bridge._SESSIONS
    assert "parked-chat" in bridge._SESSIONS
    orch.reap_secretary_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_accumulates_turns_and_surfaces_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = {"title": "Fix <thing>", "team": "backend", "description": "d"}
    registry = FakeRegistry(
        [
            {"kind": "text", "text": "Hello "},
            {"kind": "text", "text": "CEO."},
            {"kind": "turn_end", "text": ""},
            {"kind": "draft", "text": "", "data": draft},
        ]
    )
    monkeypatch.setattr(
        "roboco.services.prompter_live.get_live_registry", lambda: registry
    )

    sess = _bridge_session("intake")
    bridge._SESSIONS["777"] = sess
    await bridge._consume("777", sess)

    calls = sess.client.send_message.await_args_list
    # Turn text, then the draft card, then the end-of-session note.
    assert calls[0].args[0] == "Hello CEO."
    assert "Fix &lt;thing&gt;" in calls[1].args[0]
    keyboard = calls[1].kwargs["reply_markup"]["inline_keyboard"][0]
    assert keyboard[0]["callback_data"].startswith("apv:intake:")
    assert keyboard[1]["callback_data"].startswith("rej:intake:")
    assert sess.pending_draft == draft
    assert calls[-1].args[0] == "Session ended."
    # Stream ended → session evicted, client closed.
    assert "777" not in bridge._SESSIONS
    sess.client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_secretary_opens_session_and_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = SimpleNamespace(start_secretary_session=AsyncMock())
    monkeypatch.setattr(bridge, "_orchestrator", lambda: orch)
    monkeypatch.setattr(bridge, "_build_client", lambda _c: AsyncMock())
    consume = AsyncMock()
    monkeypatch.setattr(bridge, "_consume", consume)

    message = await bridge.start_secretary("777", "plan my day", CREDS)

    assert "On it" in message
    sess = bridge._SESSIONS["777"]
    assert sess.kind == "secretary"
    orch.start_secretary_session.assert_awaited_once_with(
        sess.session_id, initial_message="plan my day"
    )
    await sess.consumer  # the mocked _consume task completes cleanly


# ---------------------------------------------------------------------------
# engine wiring — commands + callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secretary_command_starts_or_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = AsyncMock(return_value="🎩 On it…")
    monkeypatch.setattr(bridge, "start_secretary", start)
    client = AsyncMock()

    await _engine()._dispatch_command(
        "secretary", "plan", client, chat_id="777", creds=CREDS
    )
    start.assert_awaited_once_with("777", "plan", CREDS)

    bridge._SESSIONS["777"] = _bridge_session("intake")
    await _engine()._dispatch_command(
        "secretary", "plan", client, chat_id="777", creds=CREDS
    )
    assert "mid /newtask" in client.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_newtask_with_multiple_projects_asks_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = [
        SimpleNamespace(id=uuid4(), slug="roboco", name="RoboCo"),
        SimpleNamespace(id=uuid4(), slug="website", name="Website"),
    ]
    svc = MagicMock(list_all=AsyncMock(return_value=projects))
    monkeypatch.setattr(ti, "get_project_service", lambda _s: svc)
    client = AsyncMock()

    await _engine()._dispatch_command(
        "newtask", "ship a thing", client, chat_id="777", creds=CREDS
    )

    assert bridge._PENDING_NEWTASK["777"] == "ship a thing"
    keyboard = client.send_message.await_args.kwargs["reply_markup"]
    labels = [row[0]["text"] for row in keyboard["inline_keyboard"]]
    assert labels == ["RoboCo", "Website"]


@pytest.mark.asyncio
async def test_project_pick_callback_starts_intake_with_stored_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(id=uuid4(), slug="roboco", name="RoboCo")
    svc = MagicMock(list_all=AsyncMock(return_value=[project]))
    monkeypatch.setattr(ti, "get_project_service", lambda _s: svc)
    start = AsyncMock(return_value="📝 Intake on RoboCo")
    monkeypatch.setattr(bridge, "start_intake", start)
    bridge._PENDING_NEWTASK["777"] = "ship a thing"
    client = AsyncMock()

    parsed = ti.parse_callback(f"sel:proj:{str(project.id)[:8]}")
    assert parsed is not None
    await _engine()._handle_bridge_callback(parsed, "777", 5, CREDS, client)

    start.assert_awaited_once_with("777", "ship a thing", CREDS, project=project)


@pytest.mark.asyncio
async def test_intake_confirm_routes_board_and_parks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    prompter = MagicMock(confirm_live_draft=AsyncMock(return_value=task_id))
    monkeypatch.setattr(
        "roboco.services.prompter.get_prompter_service", lambda _s: prompter
    )
    registry = FakeRegistry()
    monkeypatch.setattr(
        "roboco.services.prompter_live.get_live_registry", lambda: registry
    )

    project_id = uuid4()
    sess = _bridge_session("intake")
    sess.project_id = str(project_id)
    sess.pending_draft = {"title": "T"}
    bridge._SESSIONS["777"] = sess

    ok, text = await _engine()._confirm_intake_draft("777")

    assert ok is True
    assert "Board review" in text
    prompter.confirm_live_draft.assert_awaited_once_with(
        {"title": "T"}, ti._CEO_UUID, project_id=project_id, route="board"
    )
    assert registry.parked == [(sess.session_id, str(task_id))]
    assert sess.parked is True
    assert sess.pending_draft is None


@pytest.mark.asyncio
async def test_intake_discard_keeps_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = _bridge_session("intake")
    sess.pending_draft = {"title": "T"}
    bridge._SESSIONS["777"] = sess
    engine = _engine()
    finish = AsyncMock()
    monkeypatch.setattr(engine, "_finish_action", finish)
    client = AsyncMock()

    parsed = ti.parse_callback(f"rej:intake:{sess.session_id[:8]}")
    assert parsed is not None
    await engine._handle_bridge_callback(parsed, "777", 5, CREDS, client)

    assert sess.pending_draft is None
    assert "777" in bridge._SESSIONS
    assert finish.await_args.args[2] is True
