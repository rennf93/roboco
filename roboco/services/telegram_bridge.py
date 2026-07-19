"""Telegram ↔ live-chat bridge (Mini App V4 P5).

``/secretary`` and ``/newtask`` bridge the CEO's Telegram chat into the same
in-process live-chat runtimes the panel drives: the persistent Secretary
container and the scoped Intake (Prompter) interview. There is no
synchronous send→reply seam — replies land on the session's single-consumer
asyncio queue (``PrompterLiveRegistry.stream``) — so each bridged session
runs one long-lived consumer task that accumulates streamed text and pushes
one Telegram message per completed turn (``turn_end``). A ``draft`` event
becomes a confirm/discard keyboard; confirm routes through the normal
board-review path and PARKS the session, so board feedback later streams
straight back into the same Telegram thread.

State is per-process in-memory (the ``_PENDING_REPLIES`` posture in
``telegram_inbound``): an orchestrator restart drops the map and the CEO
starts a fresh session. At most one bridged session per chat; the intake
and secretary containers are process-wide singletons shared with the panel,
so starting a bridged session preempts a live panel session of the same
kind by construction.

The consumer holds the relay stream open, which arms the registry's 60s
keepalive — so the registry's own idle reap never fires for bridged
sessions. ``sweep_idle`` is therefore the bridge's own TTL (same setting),
skipping parked sessions exactly like the registry does.
"""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from roboco.config import settings
from roboco.services.telegram_client import build_telegram_client

if TYPE_CHECKING:
    from roboco.services.telegram_client import TelegramClient
    from roboco.services.telegram_credentials import TelegramCredentialsData

logger = structlog.get_logger()

_DRAFT_PREVIEW_CHARS = 400
_REPLY_CHAR_LIMIT = 4000  # under Telegram's 4096 cap, leaves tag headroom


@dataclass
class BridgeSession:
    kind: str  # "secretary" | "intake"
    session_id: str
    client: TelegramClient
    consumer: asyncio.Task[None] | None = None
    project_id: str | None = None  # intake confirm scope (uuid str)
    pending_draft: dict[str, Any] | None = None
    parked: bool = False
    last_user_turn: float = field(default_factory=time.monotonic)


# chat_id → live bridged session; chat_id → /newtask text awaiting a
# project pick. Per-process, best-effort by design.
_SESSIONS: dict[str, BridgeSession] = {}
_PENDING_NEWTASK: dict[str, str] = {}


def active_session(chat_id: str) -> BridgeSession | None:
    return _SESSIONS.get(chat_id)


def remember_newtask_text(chat_id: str, text: str) -> None:
    _PENDING_NEWTASK[chat_id] = text


def pop_newtask_text(chat_id: str) -> str:
    return _PENDING_NEWTASK.pop(chat_id, "")


def discard_draft(chat_id: str) -> None:
    sess = _SESSIONS.get(chat_id)
    if sess is not None:
        sess.pending_draft = None


def mark_parked(chat_id: str, task_id: str) -> None:
    """Confirm succeeded: park the relay session against the task (the
    panel's own board-route behavior) so the agent survives board review
    and the redraft brief streams back into this thread."""
    from roboco.services.prompter_live import get_live_registry

    sess = _SESSIONS.get(chat_id)
    if sess is None:
        return
    get_live_registry().park(sess.session_id, task_id)
    sess.parked = True
    sess.pending_draft = None


def _orchestrator() -> Any | None:
    # Deferred api.deps import — the established service-layer pattern for
    # reaching the process-wide orchestrator handle (see TaskService).
    from roboco.api.deps import get_orchestrator_or_none

    return get_orchestrator_or_none()


def _build_client(creds: TelegramCredentialsData) -> TelegramClient:
    return build_telegram_client(creds, timeout=settings.telegram_timeout_seconds)


async def start_secretary(
    chat_id: str, initial_text: str, creds: TelegramCredentialsData
) -> str:
    orch = _orchestrator()
    if orch is None:
        return "Orchestrator isn't ready — try again in a minute."
    session_id = uuid4().hex
    sess = BridgeSession(
        kind="secretary", session_id=session_id, client=_build_client(creds)
    )
    _SESSIONS[chat_id] = sess
    await orch.start_secretary_session(session_id, initial_message=initial_text or None)
    sess.consumer = asyncio.create_task(_consume(chat_id, sess))
    return "🎩 On it…" if initial_text else "🎩 Secretary here — what do you need?"


async def start_intake(
    chat_id: str,
    initial_text: str,
    creds: TelegramCredentialsData,
    *,
    project: Any,
) -> str:
    """``project`` is any object with ``id``/``slug``/``name`` — in practice
    the ProjectTable row the engine already resolved."""
    orch = _orchestrator()
    if orch is None:
        return "Orchestrator isn't ready — try again in a minute."
    session_id = uuid4().hex
    sess = BridgeSession(
        kind="intake",
        session_id=session_id,
        client=_build_client(creds),
        project_id=str(project.id),
    )
    _SESSIONS[chat_id] = sess
    await orch.start_intake_session(
        session_id,
        project_slug=project.slug,
        initial_message=initial_text or None,
    )
    sess.consumer = asyncio.create_task(_consume(chat_id, sess))
    return (
        f"📝 Intake on <b>{html.escape(str(project.name))}</b> — interviewing "
        "now, replies land here. /end to stop."
    )


async def deliver_text(chat_id: str, text: str) -> str | None:
    """Route free text into the chat's bridged session. None = no session
    (caller ignores the text, the pre-bridge behavior); "" = delivered;
    anything else is a user-facing error to send back."""
    from roboco.services.prompter_live import get_live_registry

    sess = _SESSIONS.get(chat_id)
    if sess is None:
        return None
    sess.last_user_turn = time.monotonic()
    delivered = await get_live_registry().deliver(sess.session_id, text)
    if delivered:
        return ""
    return "Couldn't reach the agent — it may still be starting. Try again shortly."


async def end_session(chat_id: str) -> str:
    sess = _SESSIONS.pop(chat_id, None)
    if sess is None:
        return "No active session."
    _PENDING_NEWTASK.pop(chat_id, None)
    orch = _orchestrator()
    if orch is not None:
        if sess.kind == "secretary":
            await orch.reap_secretary_session(sess.session_id)
        else:
            await orch.reap_intake_session(sess.session_id)
    return "Ended."


async def sweep_idle() -> None:
    """The bridge's own idle TTL (the consumer's open stream arms the
    registry keepalive, so the registry never reaps these itself). Parked
    sessions are exempt — they're awaiting board review."""
    ttl = settings.interactive_idle_reap_seconds
    now = time.monotonic()
    for chat_id, sess in list(_SESSIONS.items()):
        if not sess.parked and now - sess.last_user_turn > ttl:
            logger.info(
                "telegram bridge session idle-reaped",
                chat_id=chat_id,
                kind=sess.kind,
            )
            await end_session(chat_id)


def _clip(text: str) -> str:
    if len(text) <= _REPLY_CHAR_LIMIT:
        return text
    return text[:_REPLY_CHAR_LIMIT] + "…"


def _render_draft(draft: dict[str, Any]) -> str:
    title = html.escape(str(draft.get("title") or "Untitled"))
    lines = [f"<b>📝 Draft — {title}</b>"]
    team = draft.get("team")
    if team:
        lines.append(f"Team: {html.escape(str(team))}")
    description = str(draft.get("description") or "")
    if description:
        lines.append(html.escape(description[:_DRAFT_PREVIEW_CHARS]))
    criteria = draft.get("acceptance_criteria") or []
    if criteria:
        lines.append(f"{len(criteria)} acceptance criteria")
    lines.append("")
    lines.append("Send it to Board review?")
    return "\n".join(lines)


def _draft_keyboard(session_id: str) -> dict[str, Any]:
    # Mirrors telegram_inbound's action:kind:id8 callback codec.
    id8 = session_id[:8]
    return {
        "inline_keyboard": [
            [
                {"text": "Send to Board", "callback_data": f"apv:intake:{id8}"},
                {"text": "Discard", "callback_data": f"rej:intake:{id8}"},
            ]
        ]
    }


async def _forward_event(
    sess: BridgeSession, ev: dict[str, Any], buf: list[str]
) -> None:
    kind = ev.get("kind")
    if kind == "text":
        buf.append(str(ev.get("text") or ""))
    elif kind == "turn_end":
        text = "".join(buf).strip()
        buf.clear()
        if text:
            await sess.client.send_message(_clip(html.escape(text)), parse_mode="HTML")
    elif kind == "draft":
        sess.pending_draft = dict(ev.get("data") or {})
        await sess.client.send_message(
            _render_draft(sess.pending_draft),
            parse_mode="HTML",
            reply_markup=_draft_keyboard(sess.session_id),
        )
    elif kind == "batch":
        await sess.client.send_message(
            "This became a MegaTask batch — those can't be confirmed from "
            "the phone yet. Finish it in the panel's intake chat."
        )
    elif kind == "error":
        await sess.client.send_message(
            f"⚠️ {html.escape(str(ev.get('text') or 'agent error'))}",
            parse_mode="HTML",
        )


async def _consume(chat_id: str, sess: BridgeSession) -> None:
    """Sole consumer of the session's relay stream — accumulates streamed
    text into one Telegram message per turn and surfaces draft/batch/error
    events. Ends when the relay closes (reap, /end, idle sweep)."""
    from roboco.services.prompter_live import get_live_registry

    buf: list[str] = []
    try:
        async for ev in get_live_registry().stream(sess.session_id):
            await _forward_event(sess, ev, buf)
    except Exception:
        logger.exception("telegram bridge consumer crashed", chat_id=chat_id)
    finally:
        if _SESSIONS.get(chat_id) is sess:
            _SESSIONS.pop(chat_id, None)
        try:
            await sess.client.send_message("Session ended.")
        finally:
            await sess.client.close()
