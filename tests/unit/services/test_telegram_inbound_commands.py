"""V4 bot-command tier: the BOT_COMMANDS registry drives /help and the
once-per-process setMyCommands sync; /agents, /usage, and /blocked render
from the same services the panel reads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.services import telegram_inbound as ti

COMMAND_COUNT = len(ti.BOT_COMMANDS)


def _fake_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


def _engine() -> ti.TelegramInboundEngine:
    return ti.TelegramInboundEngine(_fake_session())


def _uuid_with_prefix(prefix: str) -> UUID:
    return UUID(hex=prefix + uuid4().hex[len(prefix) :])


def _task(id8: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(id=_uuid_with_prefix(id8), title=title)


# ---------------------------------------------------------------------------
# registry ↔ help ↔ dispatch coherence
# ---------------------------------------------------------------------------


def test_help_text_derives_from_the_registry() -> None:
    for entry in ti.BOT_COMMANDS:
        assert f"/{entry['command']} — {entry['description']}" in ti._HELP_TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["agents", "usage", "blocked"])
async def test_registry_commands_dispatch_to_a_renderer(
    cmd: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine()
    renderer = AsyncMock(return_value="rendered")
    monkeypatch.setattr(engine, f"_render_{cmd}", renderer)
    client = AsyncMock()

    await engine._dispatch_command(cmd, "", client)

    renderer.assert_awaited_once()
    assert client.send_message.await_args.args[0] == "rendered"


# ---------------------------------------------------------------------------
# setMyCommands sync — once per process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_syncs_commands_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti.TelegramInboundEngine, "_commands_synced", False)
    monkeypatch.setattr(ti.settings, "telegram_enabled", True)
    monkeypatch.setattr(ti.settings, "telegram_inbound_enabled", True)

    creds = SimpleNamespace(bot_token="123:ABC", chat_id="777")
    creds_svc = MagicMock(get_decrypted=AsyncMock(return_value=creds))
    monkeypatch.setattr(ti, "get_telegram_credentials_service", lambda _s: creds_svc)
    settings_svc = MagicMock(get_int=AsyncMock(return_value=0), set=AsyncMock())
    monkeypatch.setattr(ti, "get_settings_service", lambda _s: settings_svc)

    client = AsyncMock()
    client.configured = True
    client.get_updates = AsyncMock(return_value=[])

    engine = ti.TelegramInboundEngine(_fake_session(), client=client)
    await engine.run_cycle()
    await engine.run_cycle()

    client.set_my_commands.assert_awaited_once_with(list(ti.BOT_COMMANDS))


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_status_shares_fleet_derivation_with_render_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /status fleet line must come from the SAME by_status breakdown
    /agents and the Today brief use (TgCockpitService.fleet) — a second,
    independent agent-status query previously could (and did) disagree."""
    fleet: dict[str, Any] = {
        "total": 27,
        "by_status": {"active": 3, "idle": 20, "offline": 4},
        "working": [],
    }
    cockpit = MagicMock(fleet=AsyncMock(return_value=fleet))
    monkeypatch.setattr(ti, "get_tg_cockpit_service", lambda _s: cockpit)
    tasks = MagicMock(
        count_by_status=AsyncMock(return_value={"in_progress": 5, "pending": 2})
    )
    monkeypatch.setattr(ti, "get_task_service", lambda _s: tasks)

    text = await _engine()._render_status()

    assert "3</b> active" in text
    assert "20</b> idle" in text
    assert "4</b> offline" in text
    assert "in_progress" in text


@pytest.mark.asyncio
async def test_render_agents_lists_working_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fleet: dict[str, Any] = {
        "total": 26,
        "by_status": {"active": 4, "idle": 22},
        "working": [
            {"name": "be-dev-1", "task_title": "GitProvider seam"},
            {"name": "fe-qa", "task_title": None},
        ],
    }
    cockpit = MagicMock(fleet=AsyncMock(return_value=fleet))
    monkeypatch.setattr(ti, "get_tg_cockpit_service", lambda _s: cockpit)

    text = await _engine()._render_agents()

    assert "26 total" in text
    assert "4 active" in text
    assert "be-dev-1" in text
    assert "GitProvider seam" in text
    assert "fe-qa" in text


@pytest.mark.asyncio
async def test_render_usage_formats_today_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cockpit = MagicMock(
        today_spend=AsyncMock(
            return_value={
                "tokens_today": 2_400_000,
                "cost_today_usd": 18.7,
                "subscription_billed": False,
            }
        )
    )
    monkeypatch.setattr(ti, "get_tg_cockpit_service", lambda _s: cockpit)

    text = await _engine()._render_usage()

    assert "$18.70" in text
    assert "2,400,000 tokens" in text


@pytest.mark.asyncio
async def test_render_usage_labels_subscription_billed_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An untracked-subscription spend day (Ollama Cloud, no grounded rate)
    must never render as a bare, misleading '$0.00'."""
    cockpit = MagicMock(
        today_spend=AsyncMock(
            return_value={
                "tokens_today": 456_221,
                "cost_today_usd": 0.0,
                "subscription_billed": True,
            }
        )
    )
    monkeypatch.setattr(ti, "get_tg_cockpit_service", lambda _s: cockpit)

    text = await _engine()._render_usage()

    assert "subscription (untracked)" in text
    assert "456,221 tokens" in text
    assert "$0.00" not in text


@pytest.mark.asyncio
async def test_render_blocked_sections_and_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = MagicMock(
        list_awaiting_ceo_approval=AsyncMock(
            return_value=[_task("aaaa1111", "Root PR ready")]
        ),
        list_blocked=AsyncMock(return_value=[_task("bbbb2222", "Infra <wedge>")]),
    )
    monkeypatch.setattr(ti, "get_task_service", lambda _s: tasks)
    monkeypatch.setattr(ti.settings, "panel_base_url", "https://nas.example")

    text = await _engine()._render_blocked()

    assert "Awaiting you" in text
    assert "Blocked" in text
    assert '<a href="https://nas.example/tasks/aaaa1111">Root PR ready</a>' in text
    assert "aaaa1111" in text
    # HTML-escaped title, never raw.
    assert "Infra &lt;wedge&gt;" in text


@pytest.mark.asyncio
async def test_render_blocked_all_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = MagicMock(
        list_awaiting_ceo_approval=AsyncMock(return_value=[]),
        list_blocked=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(ti, "get_task_service", lambda _s: tasks)

    text = await _engine()._render_blocked()

    assert "Nothing is blocked" in text
