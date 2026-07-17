"""Telegram inbound V2 — commands (/status, /queue, /task) + actionable
approve/reject callback buttons.

Gated by ``telegram_inbound_enabled`` on top of V1's ``telegram_enabled`` —
both, plus stored credentials, are required before the poll loop does
anything (``run_cycle`` is otherwise a fast no-op). Every action a callback or
a command triggers calls the SAME service methods the CEO-gated HTTP routes
call (``TaskService.ceo_approve``/``ceo_reject``, ``ReleaseProposalService``,
``XPostService``, ``VideoPostService``, ``RoadmapService``) — never a
shortcut around their locks/idempotency, since chat-id authorization here IS
the CEO-identity check those routes' ``require_ceo_role`` performs.

Callback data is a compact ``apv|rej:<kind>:<id8>[:<extra>]`` string (Telegram
caps this at 64 bytes). A reject (any kind) and a task approve (mirrors the
HTTP route's >=20-char CEO note requirement) both need free text the button
alone can't carry, so those force_reply-prompt the CEO and hold a small
in-memory ``_PENDING_REPLIES`` map keyed by ``(chat_id, prompt_message_id)``
until the next matching reply consumes it (bounded TTL, per-process — an
orchestrator restart just drops a stale prompt, the CEO taps the button
again).

The getUpdates offset cursor persists in the existing ``system_settings`` KV
store (``telegram_last_update_id``) rather than a new table, so a restart
doesn't replay already-processed updates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import func, select

from roboco.config import settings
from roboco.db.base import get_session_factory
from roboco.db.tables import AgentTable, AuditLogTable
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.content.validators import reject_trivial
from roboco.models.base import AgentStatus, TaskStatus
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import BaseService, ValidationError
from roboco.services.release_proposal import TaskAlreadyCompletedError as _ReleaseDone
from roboco.services.release_proposal import (
    dispatch_approve,
    get_release_proposal_service,
)
from roboco.services.roadmap_service import get_roadmap_service
from roboco.services.settings import get_settings_service
from roboco.services.task import get_task_service
from roboco.services.telegram_client import TelegramClient, build_telegram_client
from roboco.services.telegram_credentials import get_telegram_credentials_service
from roboco.services.tiktok_client import build_tiktok_poster
from roboco.services.tiktok_credentials import get_tiktok_credentials_service
from roboco.services.video_post_service import TaskAlreadyCompletedError as _VideoDone
from roboco.services.video_post_service import (
    VideoCaptionTooLongError,
    VideoPostService,
    get_video_post_service,
)
from roboco.services.x_credentials import get_x_credentials_service
from roboco.services.x_post_service import TaskAlreadyCompletedError as _XPostDone
from roboco.services.x_post_service import XPostBodyTooLongError, get_x_post_service
from roboco.services.x_video_client import build_x_video_poster

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.telegram_credentials import TelegramCredentialsData

# Reuses the system_settings KV store instead of a dedicated table (see
# `roboco.services.settings._VALIDATORS` for the write-side int validator).
_OFFSET_KEY = "telegram_last_update_id"
_CEO_UUID = UUID(AGENT_UUIDS["ceo"])

_QUEUE_ITEM_CAP = 10
_MESSAGE_CHAR_LIMIT = 4096  # Telegram's own sendMessage text cap

_VALID_KINDS = ("task", "release", "xpost", "video", "roadmap")
# Per-kind reject-reason floor, mirroring each HTTP route's request schema
# (ReleaseRejectRequest min_length=10, XPostRejectRequest/VideoPostRejectRequest/
# RoadmapRejectRequest min_length=4; ceo_reject has no route-level floor beyond
# TaskService.ceo_reject's own reject_trivial default).
_REJECT_MIN_CHARS = {"task": 1, "release": 10, "xpost": 4, "video": 4, "roadmap": 4}
_DEFAULT_REJECT_MIN_CHARS = (
    4  # defense-in-depth fallback; every valid kind is listed above
)
# Mirrors ceo_approve_task's own `_MIN_NOTES_CHARS` — the HTTP route's audit-
# trail requirement for a CEO approval note.
_TASK_APPROVE_MIN_CHARS = 20

_HELP_TEXT = (
    "Commands:\n"
    "/status - fleet snapshot\n"
    "/queue - everything awaiting your approval, with buttons\n"
    "/task <id8-or-title> - task detail\n"
    "/help - this message"
)


def _truncate(text: str, limit: int = _MESSAGE_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_command(text: str) -> tuple[str, str]:
    """Split a message into ``(command, args)``; ``("", "")`` for non-command
    text. Strips a ``@botname`` suffix (the group-chat command syntax)."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return "", ""
    head, _, rest = stripped.partition(" ")
    cmd = head[1:].split("@", 1)[0].lower()
    return cmd, rest.strip()


@dataclass(frozen=True)
class ParsedCallback:
    """One ``apv:<kind>:<id8>[:<extra>]`` / ``rej:...`` callback_data, parsed."""

    action: str  # "apv" | "rej"
    kind: str
    id8: str
    extra: str = ""


_CALLBACK_DATA_MAX_BYTES = 64  # Telegram's own callback_data cap
_CALLBACK_PARTS_NO_EXTRA = 3  # action:kind:id8
_CALLBACK_PARTS_WITH_EXTRA = 4  # action:kind:id8:extra (roadmap's item id)


def build_callback(action: str, kind: str, id8: str, extra: str = "") -> str:
    """Compact callback_data. Raises if it would exceed Telegram's 64-byte cap
    (roadmap's ``item-N`` extras stay well under it in practice)."""
    parts = [action, kind, id8]
    if extra:
        parts.append(extra)
    data = ":".join(parts)
    if len(data.encode()) > _CALLBACK_DATA_MAX_BYTES:
        raise ValueError(
            f"callback_data exceeds {_CALLBACK_DATA_MAX_BYTES} bytes: {data!r}"
        )
    return data


def parse_callback(data: str) -> ParsedCallback | None:
    """None on anything malformed/unrecognized — the caller just answers the
    callback_query with a generic rejection rather than guessing intent."""
    parts = (data or "").split(":")
    if len(parts) not in (_CALLBACK_PARTS_NO_EXTRA, _CALLBACK_PARTS_WITH_EXTRA):
        return None
    action, kind, id8 = parts[0], parts[1], parts[2]
    if action not in ("apv", "rej") or kind not in _VALID_KINDS or not id8:
        return None
    extra = parts[3] if len(parts) == _CALLBACK_PARTS_WITH_EXTRA else ""
    return ParsedCallback(action=action, kind=kind, id8=id8, extra=extra)


# Deep-link target per kind — mirrors where each queue actually lives in the
# panel (release proposal + roadmap both surface on the Overview command
# center; X/video drafts on the Social queue).
_DEEP_LINK_PATH = {
    "task": "/tasks/{id8}",
    "release": "/overview",
    "xpost": "/social",
    "video": "/social",
    "roadmap": "/overview",
}


def _deep_link(kind: str, id8: str) -> str:
    base = settings.panel_base_url.rstrip("/")
    if not base:
        return ""
    return f"{base}{_DEEP_LINK_PATH[kind].format(id8=id8)}"


def build_action_keyboard(kind: str, id8: str, extra: str = "") -> dict[str, Any]:
    """Approve / Reject / (Open, when panel_base_url is set) inline row."""
    row: list[dict[str, str]] = [
        {"text": "Approve", "callback_data": build_callback("apv", kind, id8, extra)},
        {"text": "Reject", "callback_data": build_callback("rej", kind, id8, extra)},
    ]
    link = _deep_link(kind, id8)
    if link:
        row.append({"text": "Open", "url": link})
    return {"inline_keyboard": [row]}


def _authorized_chat(chat_id: str, creds_chat_id: str) -> bool:
    """The CEO's own stored chat id IS the identity check here — no
    agent/session token exists on a Telegram update, so this is the equivalent
    of every CEO-gated route's ``require_ceo_role``."""
    return bool(chat_id) and str(chat_id) == str(creds_chat_id)


def _authorized_sender(sender: dict[str, Any] | None, chat_id: str) -> bool:
    """Defense-in-depth on top of ``_authorized_chat``: when the update
    carries a ``from`` user, its id must ALSO equal the (already
    chat-id-verified) credentialed chat id — the supported deployment is a
    private 1:1 chat where ``from.id == chat.id == the CEO``. A present but
    mismatched ``from.id`` (e.g. another member somehow posting into what's
    assumed to be a private chat) is refused. Absent ``from`` keeps the prior
    chat-id-only behavior."""
    if not sender:
        return True
    sender_id = sender.get("id")
    if sender_id is None:
        return True
    return str(sender_id) == str(chat_id)


@dataclass
class _PendingAction:
    """A force_reply prompt awaiting the CEO's free-text reply."""

    kind: str
    id8: str
    extra: str
    action: str  # "approve" | "reject"
    origin_message_id: int | None  # the buttoned message to clean up after
    expires_at: float  # time.monotonic()-based


# Per-process, best-effort — mirrors XEngine's in-memory `_INFLIGHT_APPROVES`
# registry. Not durable by design: a restart drops any in-flight prompt, the
# CEO just taps the button again (cheap enough to not warrant a table).
_PENDING_REPLIES: dict[tuple[str, int], _PendingAction] = {}


def _sweep_expired_replies() -> None:
    now = time.monotonic()
    for key in [k for k, v in _PENDING_REPLIES.items() if v.expires_at <= now]:
        _PENDING_REPLIES.pop(key, None)


class TelegramInboundEngine(BaseService):
    """Poll Telegram for commands/callbacks and act on them."""

    service_name = "telegram_inbound"

    def __init__(
        self, session: AsyncSession, client: TelegramClient | None = None
    ) -> None:
        super().__init__(session)
        self._injected_client = client

    async def _client(self, creds: TelegramCredentialsData) -> TelegramClient:
        if self._injected_client is not None:
            return self._injected_client
        return build_telegram_client(creds, timeout=settings.telegram_timeout_seconds)

    # ---- poll cycle ----------------------------------------------------

    async def run_cycle(self) -> None:
        """One poll pass: fetch updates since the stored offset, dispatch
        each, advance the offset. Never raises — every step is best-effort;
        a single bad update is logged and skipped rather than wedging the
        whole cycle (and the offset still advances past it)."""
        if not (settings.telegram_enabled and settings.telegram_inbound_enabled):
            return
        creds = await get_telegram_credentials_service(self.session).get_decrypted()
        if creds is None:
            return
        client = await self._client(creds)
        if not client.configured:
            return
        offset = await get_settings_service(self.session).get_int(_OFFSET_KEY, 0)
        cap = settings.telegram_max_updates_per_cycle
        updates = await client.get_updates(
            offset=offset or None,
            timeout=settings.telegram_poll_timeout_seconds,
            limit=cap,
        )
        max_seen = offset - 1
        for update in updates[:cap]:
            update_id = int(update.get("update_id") or 0)
            max_seen = max(max_seen, update_id)
            try:
                await self._process_update(update, creds, client)
            except Exception:
                self.log.exception(
                    "telegram update processing failed", update_id=update_id
                )
        if max_seen >= offset:
            await get_settings_service(self.session).set(_OFFSET_KEY, str(max_seen + 1))
            await self.session.commit()

    async def _process_update(
        self,
        update: dict[str, Any],
        creds: TelegramCredentialsData,
        client: TelegramClient,
    ) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"], creds, client)
        elif "message" in update:
            await self._handle_message(update["message"], creds, client)
        # else: edited_message / channel_post / other update kinds — ignored.

    # ---- commands --------------------------------------------------------

    async def _handle_message(
        self,
        message: dict[str, Any],
        creds: TelegramCredentialsData,
        client: TelegramClient,
    ) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not _authorized_chat(chat_id, creds.chat_id):
            self.log.debug(
                "telegram message from unauthorized chat dropped", chat_id=chat_id
            )
            return
        if not _authorized_sender(message.get("from"), chat_id):
            self.log.debug(
                "telegram message from mismatched sender dropped", chat_id=chat_id
            )
            return
        text = str(message.get("text") or "")
        reply_to = message.get("reply_to_message") or {}
        reply_to_id = reply_to.get("message_id")
        if reply_to_id is not None:
            pending = _PENDING_REPLIES.pop((chat_id, int(reply_to_id)), None)
            if pending is not None:
                if pending.expires_at > time.monotonic():
                    await self._consume_reply(pending, text, client)
                    return
                await client.send_message("That prompt expired — tap the button again.")
                return
        cmd, args = parse_command(text)
        if not cmd:
            return
        await self._dispatch_command(cmd, args, client)

    async def _dispatch_command(
        self, cmd: str, args: str, client: TelegramClient
    ) -> None:
        if cmd in ("start", "help"):
            await client.send_message(_HELP_TEXT)
        elif cmd == "status":
            await client.send_message(await self._render_status())
        elif cmd == "queue":
            await self._send_queue(client)
        elif cmd == "task":
            await client.send_message(await self._render_task(args))
        else:
            await client.send_message(f"Unknown command /{cmd}.\n\n{_HELP_TEXT}")

    async def _render_status(self) -> str:
        """Cheap snapshot: active-agent count + task counts by status — no
        spend/strategy/pitch queries (that's the heavier cockpit summary)."""
        counts = await get_task_service(self.session).count_by_status()
        active_result = await self.session.execute(
            select(func.count(AgentTable.id)).where(
                AgentTable.status == AgentStatus.ACTIVE
            )
        )
        active = active_result.scalar_one()
        lines = [f"Active agents: {active}", "", "Tasks by status:"]
        lines += [f"  {k}: {v}" for k, v in sorted(counts.items())]
        return _truncate("\n".join(lines))

    async def _collect_queue_items(self) -> list[tuple[str, str, str, str]]:
        """``(kind, id8, extra, title)`` for everything awaiting the CEO —
        reuses the exact list/open methods the panel's own queues call. One
        small per-kind gatherer each, rather than one long branchy loop."""
        items: list[tuple[str, str, str, str]] = []
        items += await self._queue_items_for_tasks()
        items += await self._queue_items_for_release()
        items += await self._queue_items_for_xposts()
        items += await self._queue_items_for_videos()
        items += await self._queue_items_for_roadmap()
        return items

    async def _queue_items_for_tasks(self) -> list[tuple[str, str, str, str]]:
        tasks = await get_task_service(self.session).list_awaiting_ceo_approval()
        return [
            ("task", str(t.id)[:8], "", f"[Task] {t.title or 'Untitled'}")
            for t in tasks
        ]

    async def _queue_items_for_release(self) -> list[tuple[str, str, str, str]]:
        proposal = await get_release_proposal_service(self.session).open_proposal()
        if proposal is None:
            return []
        id8 = str(proposal.id)[:8]
        report = markers.get_release_report(proposal) or {}
        version = report.get("proposed_version") or "?"
        return [("release", id8, "", f"[Release] v{version} ready")]

    async def _queue_items_for_xposts(self) -> list[tuple[str, str, str, str]]:
        posts = await get_x_post_service(self.session).list_open_posts()
        result = []
        for post in posts:
            id8 = str(post.id)[:8]
            body = markers.get_x_draft_body(post) or post.description or ""
            result.append(("xpost", id8, "", f"[X post] {body[:100]}"))
        return result

    async def _queue_items_for_videos(self) -> list[tuple[str, str, str, str]]:
        posts = await get_video_post_service(self.session).list_held_video_posts()
        result = []
        for post in posts:
            id8 = str(post.id)[:8]
            draft = markers.get_video_draft(post) or {}
            occasion = draft.get("occasion") or "untitled"
            result.append(("video", id8, "", f"[Video] {occasion}"))
        return result

    async def _queue_items_for_roadmap(self) -> list[tuple[str, str, str, str]]:
        cycles = await get_roadmap_service(self.session).list_open_cycles()
        result = []
        for cycle in cycles:
            id8 = str(cycle.id)[:8]
            payload = markers.get_roadmap_cycle(cycle) or {}
            result += self._proposed_roadmap_items(id8, payload)
        return result

    def _proposed_roadmap_items(
        self, id8: str, payload: dict[str, Any]
    ) -> list[tuple[str, str, str, str]]:
        result = []
        for item in payload.get("items", []):
            if item.get("status") != "proposed":
                continue
            item_id = str(item.get("id") or "")
            title = item.get("title") or "untitled"
            result.append(("roadmap", id8, item_id, f"[Roadmap] {title}"))
        return result

    async def _send_queue(self, client: TelegramClient) -> None:
        items = await self._collect_queue_items()
        if not items:
            await client.send_message("Nothing awaiting your approval.")
            return
        await client.send_message(f"{len(items)} item(s) awaiting your approval:")
        for kind, id8, extra, title in items[:_QUEUE_ITEM_CAP]:
            suffix = f":{extra}" if extra else ""
            text = _truncate(f"{title} ({id8}{suffix})")
            await client.send_message(
                text, reply_markup=build_action_keyboard(kind, id8, extra)
            )

    async def _resolve_task(self, id8: str) -> TaskTable | None:
        """Exact id-prefix resolution — ``search_tasks`` also OR-matches
        title/description substrings, so filter back down to only rows whose
        id genuinely starts with ``id8``. None on zero or ambiguous (>1)
        matches; downstream callers surface that as "no such item".

        A generous (but still bounded) limit: a real id-prefix hit could
        otherwise be pushed out of a small window by title/description ILIKE
        hits on newer rows, making a genuine single match look ambiguous or
        vanish entirely.
        """
        candidates = await get_task_service(self.session).search_tasks(id8, limit=50)
        exact = [t for t in candidates if str(t.id).startswith(id8)]
        return exact[0] if len(exact) == 1 else None

    async def _render_task(self, args: str) -> str:
        q = args.strip()
        if not q:
            return "Usage: /task <id8-or-title-fragment>"
        task = await self._resolve_task(q)
        if task is None:
            resolved_or_error = await self._search_task_by_fragment(q)
            if isinstance(resolved_or_error, str):
                return resolved_or_error
            task = resolved_or_error
        return _truncate(self._format_task_detail(task))

    async def _search_task_by_fragment(self, q: str) -> TaskTable | str:
        """A title/description-fragment fallback for ``/task`` when the query
        isn't a resolvable id prefix. Returns either the single match, or a
        rendered error/disambiguation message for the caller to return as-is."""
        candidates = await get_task_service(self.session).search_tasks(q, limit=5)
        if not candidates:
            return f"No task matches {q!r}."
        if len(candidates) > 1:
            listing = "\n".join(f"  {str(t.id)[:8]} - {t.title}" for t in candidates)
            return (
                f"Multiple matches for {q!r}:\n{listing}\n\n"
                "Retry with a more specific id or title."
            )
        return candidates[0]

    def _format_task_detail(self, task: TaskTable) -> str:
        id8 = str(task.id)[:8]
        status_val = (
            task.status.value if hasattr(task.status, "value") else str(task.status)
        )
        team_val = (
            task.team.value if task.team and hasattr(task.team, "value") else "n/a"
        )
        lines = [
            f"[{id8}] {task.title or 'Untitled'}",
            f"Status: {status_val}",
            f"Team: {team_val}",
        ]
        if task.pr_url:
            lines.append(f"PR: {task.pr_url}")
        link = _deep_link("task", id8)
        if link:
            lines.append(link)
        return "\n".join(lines)

    # ---- callback (button) handling ---------------------------------------

    async def _handle_callback(
        self, cq: dict[str, Any], creds: TelegramCredentialsData, client: TelegramClient
    ) -> None:
        origin = cq.get("message") or {}
        chat = origin.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        cq_id = str(cq.get("id", ""))
        if not _authorized_chat(chat_id, creds.chat_id):
            await client.answer_callback_query(cq_id, "Not authorized")
            return
        if not _authorized_sender(cq.get("from"), chat_id):
            await client.answer_callback_query(cq_id, "Not authorized")
            return
        parsed = parse_callback(str(cq.get("data") or ""))
        origin_message_id = origin.get("message_id")
        if parsed is None:
            await client.answer_callback_query(cq_id, "Unrecognized action")
            return
        # Every reject, and a task approve (mirrors ceo-approve's own >=20
        # char note requirement), need free text the button alone can't
        # carry — force_reply-prompt and park a pending action instead of
        # acting immediately.
        needs_reply = parsed.action == "rej" or (
            parsed.action == "apv" and parsed.kind == "task"
        )
        if needs_reply:
            await client.answer_callback_query(cq_id, "Reply with the reason")
            await self._prompt_for_reply(parsed, chat_id, origin_message_id, client)
            return
        await client.answer_callback_query(cq_id, "Working...")
        ok, text = await self._dispatch_approve(
            parsed.kind, parsed.id8, parsed.extra, notes=None
        )
        await self._finish_action(client, origin_message_id, ok, text)

    async def _prompt_for_reply(
        self,
        parsed: ParsedCallback,
        chat_id: str,
        origin_message_id: int | None,
        client: TelegramClient,
    ) -> None:
        field = (
            f"approval notes (>= {_TASK_APPROVE_MIN_CHARS} chars)"
            if parsed.action == "apv"
            else "rejection reason"
        )
        target = f"{parsed.kind}:{parsed.id8}" + (
            f":{parsed.extra}" if parsed.extra else ""
        )
        prompt = await client.send_message(
            f"Reply to THIS message with your {field} for {target}.",
            reply_markup={"force_reply": True},
        )
        if prompt.message_id is None:
            return
        _sweep_expired_replies()
        _PENDING_REPLIES[(chat_id, prompt.message_id)] = _PendingAction(
            kind=parsed.kind,
            id8=parsed.id8,
            extra=parsed.extra,
            action="approve" if parsed.action == "apv" else "reject",
            origin_message_id=(
                int(origin_message_id) if origin_message_id is not None else None
            ),
            expires_at=time.monotonic() + settings.telegram_pending_reply_ttl_seconds,
        )

    async def _consume_reply(
        self, pending: _PendingAction, text: str, client: TelegramClient
    ) -> None:
        if pending.action == "reject":
            ok, result_text = await self._dispatch_reject(
                pending.kind, pending.id8, pending.extra, text
            )
        else:
            ok, result_text = await self._dispatch_approve(
                pending.kind, pending.id8, pending.extra, notes=text
            )
        await self._finish_action(client, pending.origin_message_id, ok, result_text)

    async def _finish_action(
        self, client: TelegramClient, origin_message_id: int | None, ok: bool, text: str
    ) -> None:
        """Clears the original message's button row and stamps the outcome —
        the chat stays honest instead of leaving a stale, still-clickable
        Approve/Reject row after the action already happened."""
        icon = "✅" if ok else "❌"
        rendered = _truncate(f"{icon} {text}")
        if origin_message_id is not None:
            await client.edit_message_reply_markup(int(origin_message_id), None)
            await client.edit_message_text(int(origin_message_id), rendered)
        else:
            await client.send_message(rendered)

    def _mark_audit(
        self, kind: str, task_id: UUID, action: str, *, item_id: str = ""
    ) -> None:
        """One extra AuditLogTable row (no schema change — reused table)
        tagging the action `via=telegram`, alongside whatever audit trail the
        underlying service call itself already produces."""
        details: dict[str, Any] = {"via": "telegram", "kind": kind, "action": action}
        if item_id:
            details["item_id"] = item_id
        self.session.add(
            AuditLogTable(
                event_type=f"telegram.{kind}.{action}",
                agent_id=_CEO_UUID,
                target_type="task",
                target_id=task_id,
                severity="info",
                details=details,
            )
        )

    async def _real_video_post_service(self) -> VideoPostService:
        """Mirrors the video route's own ``_real_video_post_service`` — only
        approve needs live posters; list/reject use the inert Null default."""
        x_creds = await get_x_credentials_service(self.session).get_decrypted()
        x_poster = build_x_video_poster(
            x_creds, timeout=settings.x_request_timeout_seconds
        )
        tiktok_creds = await get_tiktok_credentials_service(
            self.session
        ).get_decrypted()
        tiktok_poster = build_tiktok_poster(
            tiktok_creds,
            session=self.session,
            timeout=settings.video_request_timeout_seconds,
        )
        return get_video_post_service(
            self.session, x_poster=x_poster, tiktok_poster=tiktok_poster
        )

    # ---- dispatch: same service methods the CEO-gated HTTP routes call ----
    #
    # One small `_approve_<kind>`/`_reject_<kind>` handler per kind, looked up
    # by a dict rather than an if/elif ladder — keeps each handler (and the
    # two dispatchers themselves) well under the branch/return-count budget
    # a 5-kind if/elif chain would blow through.

    async def _dispatch_approve(
        self, kind: str, id8: str, extra: str, *, notes: str | None
    ) -> tuple[bool, str]:
        task = await self._resolve_task(id8)
        if task is None:
            return False, f"No such {kind} item: {id8}"
        handler = {
            "task": self._approve_task,
            "release": self._approve_release,
            "xpost": self._approve_xpost,
            "video": self._approve_video,
            "roadmap": self._approve_roadmap,
        }.get(kind)
        if handler is None:
            return False, f"Unknown kind: {kind}"
        return await handler(task, id8, extra, notes)

    async def _approve_task(
        self, task: TaskTable, id8: str, _extra: str, notes: str | None
    ) -> tuple[bool, str]:
        if not notes or len(notes.strip()) < _TASK_APPROVE_MIN_CHARS:
            return False, (
                f"Approval needs notes >= {_TASK_APPROVE_MIN_CHARS} chars — "
                "tap Approve again and reply."
            )
        task_id = cast("UUID", task.id)
        result = await get_task_service(self.session).ceo_approve(
            task_id, notes.strip()
        )
        if result is None:
            return False, f"Could not approve {id8} (not awaiting CEO approval)."
        self._mark_audit("task", task_id, "approve")
        await self.session.commit()
        return True, f"Approved: {task.title or id8}"

    async def _approve_release(
        self, task: TaskTable, id8: str, _extra: str, _notes: str | None
    ) -> tuple[bool, str]:
        # dispatch_approve fires the ~40min execute in the background and
        # returns immediately with no result to inspect — so a refusal (e.g.
        # a stale Approve button on a proposal the CEO already rejected) has
        # to be checked HERE, before dispatch, or the CEO would see a false
        # "dispatched" success while the background task silently no-ops on
        # the service's own CANCELLED guard.
        if task.status == TaskStatus.CANCELLED:
            return False, "This release proposal was already rejected."
        if task.status == TaskStatus.COMPLETED:
            return False, "This release was already approved and published."
        task_id = cast("UUID", task.id)
        dispatch_approve(task_id, get_session_factory())
        self._mark_audit("release", task_id, "approve")
        await self.session.commit()
        return (
            True,
            f"Release approval dispatched for {id8} (publishing in background).",
        )

    async def _approve_xpost(
        self, task: TaskTable, id8: str, _extra: str, _notes: str | None
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        try:
            result = await get_x_post_service(self.session).approve(task_id)
        except XPostBodyTooLongError as exc:
            return False, str(exc)
        if result is None:
            return False, f"No such open X draft: {id8}"
        self._mark_audit("xpost", task_id, "approve")
        await self.session.commit()
        ok = result.status in ("posted", "already_posted")
        return ok, f"X post {result.status}: {result.detail}"

    async def _approve_video(
        self, task: TaskTable, id8: str, _extra: str, _notes: str | None
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        svc = await self._real_video_post_service()
        try:
            result = await svc.approve(task_id)
        except VideoCaptionTooLongError as exc:
            return False, str(exc)
        if result is None:
            return False, f"No such open video draft: {id8}"
        self._mark_audit("video", task_id, "approve")
        await self.session.commit()
        ok = result.status in ("posted", "posted_partial", "already_posted")
        return ok, f"Video {result.status}: {result.detail}"

    async def _approve_roadmap(
        self, task: TaskTable, id8: str, extra: str, _notes: str | None
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        result = await get_roadmap_service(self.session).approve_item(
            task_id, extra, created_by=_CEO_UUID
        )
        if result is None:
            return False, f"No such roadmap item: {id8}:{extra}"
        self._mark_audit("roadmap", task_id, "approve", item_id=extra)
        await self.session.commit()
        ok = result.status in ("approved", "already_approved")
        return ok, f"Roadmap item {result.status}: {result.detail}"

    async def _dispatch_reject(
        self, kind: str, id8: str, extra: str, reason: str
    ) -> tuple[bool, str]:
        try:
            clean_reason = reject_trivial(
                reason,
                field="reason",
                min_chars=_REJECT_MIN_CHARS.get(kind, _DEFAULT_REJECT_MIN_CHARS),
            )
        except ValueError as exc:
            return False, f"Rejection not recorded: {exc}"
        task = await self._resolve_task(id8)
        if task is None:
            return False, f"No such {kind} item: {id8}"
        handler = {
            "task": self._reject_task,
            "release": self._reject_release,
            "xpost": self._reject_xpost,
            "video": self._reject_video,
            "roadmap": self._reject_roadmap,
        }.get(kind)
        if handler is None:
            return False, f"Unknown kind: {kind}"
        return await handler(task, id8, extra, clean_reason)

    async def _reject_task(
        self, task: TaskTable, id8: str, _extra: str, reason: str
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        try:
            result = await get_task_service(self.session).ceo_reject(task_id, reason)
        except ValidationError as exc:
            return False, f"Rejection not recorded: {exc}"
        if result is None:
            return False, f"Could not reject {id8} (not awaiting CEO approval)."
        self._mark_audit("task", task_id, "reject")
        await self.session.commit()
        return True, f"Rejected: {task.title or id8}"

    async def _reject_release(
        self, task: TaskTable, id8: str, _extra: str, reason: str
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        try:
            result = await get_release_proposal_service(self.session).reject(
                task_id, reason
            )
        except _ReleaseDone as exc:
            return False, str(exc)
        if result is None:
            return False, f"No such open release proposal: {id8}"
        self._mark_audit("release", task_id, "reject")
        await self.session.commit()
        return True, f"Release proposal {id8} rejected."

    async def _reject_xpost(
        self, task: TaskTable, id8: str, _extra: str, reason: str
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        try:
            result = await get_x_post_service(self.session).reject(task_id, reason)
        except _XPostDone as exc:
            return False, str(exc)
        if result is None:
            return False, f"No such open X draft: {id8}"
        self._mark_audit("xpost", task_id, "reject")
        await self.session.commit()
        return True, f"X draft {id8} rejected."

    async def _reject_video(
        self, task: TaskTable, id8: str, _extra: str, reason: str
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        try:
            result = await get_video_post_service(self.session).reject(task_id, reason)
        except _VideoDone as exc:
            return False, str(exc)
        if result is None:
            return False, f"No such open video draft: {id8}"
        self._mark_audit("video", task_id, "reject")
        await self.session.commit()
        return True, f"Video draft {id8} rejected."

    async def _reject_roadmap(
        self, task: TaskTable, id8: str, extra: str, reason: str
    ) -> tuple[bool, str]:
        task_id = cast("UUID", task.id)
        result = await get_roadmap_service(self.session).reject_item(
            task_id, extra, reason
        )
        if result is None:
            return False, f"No such roadmap item: {id8}:{extra}"
        self._mark_audit("roadmap", task_id, "reject", item_id=extra)
        await self.session.commit()
        ok = result.status in ("rejected", "already_rejected")
        return ok, f"Roadmap item {result.status}: {result.detail}"


def get_telegram_inbound_engine(
    session: AsyncSession, client: TelegramClient | None = None
) -> TelegramInboundEngine:
    """Construct a TelegramInboundEngine bound to ``session``."""
    return TelegramInboundEngine(session, client)
