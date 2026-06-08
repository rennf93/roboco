"""
Prompter Service

Conversational LLM assistant that helps users draft tasks.
Uses Anthropic Claude for natural-language interaction and
structured JSON draft generation.

Provides both a session-based approach (DB-persisted) and a
legacy stateless interface for backward compatibility.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import (
    PrompterMessageTable,
    PrompterSessionTable,
    TaskDraftTable,
    TaskTable,
)
from roboco.models.base import Complexity, TaskNature, TaskType, Team
from roboco.models.task import TaskCreateRequest
from roboco.services.base import NotFoundError, ServiceError, ValidationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass
class ConfirmOverrides:
    """Optional overrides applied when confirming a draft to create a task."""

    project_id: UUID | None = None
    product_id: UUID | None = None
    assigned_to: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROMPTER_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter — a conversational assistant that helps "
    "users draft tasks for an AI agentic company.\n\n"
    "Your job is to:\n"
    "1. Ask clarifying questions to gather requirements.\n"
    "2. Keep the conversation focused on producing a well-scoped task.\n"
    "3. When you believe you have enough context, signal that a draft is "
    "ready.\n"
    "4. Never create the task yourself — only help the user articulate what "
    "needs to be built.\n\n"
    "Key rules:\n"
    "- Be concise but thorough.\n"
    "- Always ask for acceptance criteria if the user hasn't provided them.\n"
    "- Suggest a team (backend, frontend, ux_ui) based on the work "
    "described.\n"
    "- Estimate complexity (low, medium, high) and task type (code, "
    "documentation, research, planning, design, administrative).\n"
    "- Determine nature (technical vs non_technical).\n"
    "- If the user describes a bug, suggest a code task with technical "
    "nature.\n"
    "- If the user describes a feature, determine whether it's backend, "
    "frontend, or UX/UI work.\n\n"
    "When you have enough information to produce a complete draft, say so "
    "explicitly with 'I have enough information to draft a task' or "
    "'ready to draft'."
)

_DRAFT_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter — an expert at converting conversations "
    "into structured task drafts.\n\n"
    "Given a conversation between a user and the Prompter assistant, "
    "produce a JSON task draft that conforms to the RoboCo task schema.\n\n"
    "Required fields:\n"
    "- title: concise, actionable task title (max 200 chars)\n"
    "- description: detailed description, min 20 chars, explaining what "
    "needs to be done\n"
    "- acceptance_criteria: list of strings, each a verifiable criterion "
    "(min 1)\n"
    "- team: one of backend, frontend, ux_ui\n"
    "- task_type: one of code, documentation, research, planning, design, "
    "administrative\n"
    "- nature: one of technical, non_technical\n"
    "- estimated_complexity: one of low, medium, high\n"
    "- priority: integer 0-3 (0=P0 highest, 3=P3 lowest)\n\n"
    "Optional fields:\n"
    "- project_id: UUID string if known from context\n"
    "- product_id: UUID string if known from context (only one of "
    "project_id/product_id should be set)\n"
    "- assigned_to: agent slug or UUID if the user specified one\n"
    "- target_date: ISO-8601 date string if mentioned\n\n"
    'Always set source="prompter" and confirmed_by_human=false.\n\n'
    "Return ONLY valid JSON matching the PrompterDraftTask schema. No "
    "markdown, no preamble."
)


class PrompterService:
    """Service for Prompter chat, session management, and structured draft generation.

    Accepts an optional SQLAlchemy ``AsyncSession`` for the session-based
    (DB-persisted) interface. When no session is provided, only the legacy
    stateless ``chat()`` and ``draft()`` methods are available.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.log = logger.bind(component="prompter_service")
        self._client: AsyncAnthropic | None = None
        self._db = db

    def _get_client(self) -> AsyncAnthropic:
        """Lazy-init Anthropic client."""
        if self._client is None:
            api_key = settings.anthropic_api_key
            if not api_key:
                raise ServiceError("Anthropic API key not configured")
            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    async def _create_message(self, **kwargs: Any) -> Any:
        """Single seam for the Anthropic ``messages.create`` call.

        The SDK exposes ``messages`` as a cached_property, so it can't be
        patched at the client-class level; tests substitute this method.
        """
        client = self._get_client()
        return await client.messages.create(**kwargs)

    @property
    def _session(self) -> AsyncSession:
        """Return DB session, raising if not configured."""
        if self._db is None:
            raise ServiceError(
                "PrompterService was created without a DB session; "
                "session-based methods are unavailable"
            )
        return self._db

    # -----------------------------------------------------------------------
    # Session-based interface
    # -----------------------------------------------------------------------

    async def create_session(
        self,
        agent_id: UUID,
        context: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> PrompterSessionTable:
        """Create a new Prompter conversation session."""
        session = PrompterSessionTable(
            id=uuid4(),
            agent_id=agent_id,
            status="active",
            created_at=datetime.now(UTC),
        )
        self._session.add(session)
        await self._session.flush()
        self.log.info("Prompter session created", session_id=str(session.id))
        return session

    async def send_message(
        self,
        session_id: UUID,
        agent_id: UUID,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[PrompterMessageTable]:
        """
        Append a user message, call the LLM for a reply, persist both,
        and return all messages in the session.
        """
        session = await self._get_session(session_id, agent_id)

        # Persist the user message first
        user_msg = PrompterMessageTable(
            id=uuid4(),
            session_id=session_id,
            role="user",
            content=content,
            created_at=datetime.now(UTC),
        )
        self._session.add(user_msg)
        await self._session.flush()

        # Load full conversation history for the LLM call
        history = await self._load_messages(session_id)
        chat_messages = [{"role": m.role, "content": m.content} for m in history]

        # Call the LLM
        llm_reply = await self._llm_chat(
            messages=chat_messages,
            context=context,
        )

        # Persist the assistant reply
        assistant_msg = PrompterMessageTable(
            id=uuid4(),
            session_id=session_id,
            role="assistant",
            content=llm_reply["message"],
            created_at=datetime.now(UTC),
        )
        self._session.add(assistant_msg)

        # Update session status if draft is ready
        if llm_reply["draft_ready"] and session.status == "active":
            session.status = "draft_ready"

        await self._session.flush()
        self.log.info(
            "Message processed",
            session_id=str(session_id),
            draft_ready=llm_reply["draft_ready"],
        )

        # Return all messages in order
        return await self._load_messages(session_id)

    async def get_or_generate_draft(
        self,
        session_id: UUID,
        agent_id: UUID,
    ) -> TaskDraftTable:
        """
        Return an existing draft for the session, or generate one via LLM
        if none exists yet.
        """
        await self._get_session(session_id, agent_id)

        # Check for an existing draft
        result = await self._session.execute(
            select(TaskDraftTable)
            .where(TaskDraftTable.session_id == session_id)
            .order_by(TaskDraftTable.created_at.desc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # No draft yet — generate one from conversation history
        history = await self._load_messages(session_id)
        if not history:
            raise ValidationError(
                message=(
                    "Cannot generate a draft from an empty conversation; "
                    "send at least one message first."
                ),
                field="messages",
            )

        chat_messages = [{"role": m.role, "content": m.content} for m in history]
        draft_result = await self._llm_draft(
            messages=chat_messages,
        )

        draft_record = TaskDraftTable(
            id=uuid4(),
            session_id=session_id,
            draft_data=draft_result["draft"],
            created_at=datetime.now(UTC),
        )
        self._session.add(draft_record)
        await self._session.flush()
        return draft_record

    async def confirm_draft(
        self,
        session_id: UUID,
        agent_id: UUID,
        confirm_overrides: ConfirmOverrides | None = None,
    ) -> UUID:
        """
        Validate the draft and create a real Task via the TaskService.

        Returns the newly created task's UUID.
        """
        session_rec = await self._get_session(session_id, agent_id)
        ov = confirm_overrides or ConfirmOverrides()

        # Get or generate the draft, then merge confirm-time overrides
        draft_record = await self.get_or_generate_draft(session_id, agent_id)
        draft_data: dict[str, Any] = dict(draft_record.draft_data)
        self._apply_overrides(draft_data, ov)

        resolved_project_id = self._resolve_uuid_field(draft_data, "project_id")
        resolved_product_id = self._resolve_uuid_field(draft_data, "product_id")
        if resolved_project_id is None and resolved_product_id is None:
            raise ValidationError(
                message=(
                    "The draft must have either project_id or product_id set. "
                    "Pass one via the confirm request body."
                ),
                field="project_id",
            )

        team, task_type, nature, complexity = self._coerce_draft_enums(draft_data)

        # Resolve assigned_to as UUID if possible
        resolved_assigned_to: UUID | None = None
        if draft_data.get("assigned_to"):
            with contextlib.suppress(ValueError):
                resolved_assigned_to = UUID(str(draft_data["assigned_to"]))

        req = TaskCreateRequest(
            title=draft_data["title"],
            description=draft_data["description"],
            acceptance_criteria=draft_data["acceptance_criteria"],
            team=team,
            created_by=agent_id,
            task_type=task_type,
            nature=nature,
            estimated_complexity=complexity,
            priority=int(draft_data.get("priority", 2)),
            assigned_to=resolved_assigned_to,
            project_id=resolved_project_id,
            product_id=resolved_product_id,
            source="prompter",
            confirmed_by_human=True,
        )

        # Import TaskService lazily to avoid circular imports
        from roboco.services.task import get_task_service

        task_service = get_task_service(self._session)
        task: TaskTable = await task_service.create(req)

        # Mark draft as confirmed
        now = datetime.now(UTC)
        draft_record.confirmed_at = now
        draft_record.task_id = task.id
        session_rec.status = "confirmed"
        await self._session.flush()

        self.log.info(
            "Draft confirmed — task created",
            session_id=str(session_id),
            task_id=str(task.id),
        )
        return task.id  # type: ignore[return-value]

    @staticmethod
    def _apply_overrides(draft_data: dict[str, Any], ov: ConfirmOverrides) -> None:
        """Merge confirm-time overrides onto the draft data in place."""
        if ov.project_id is not None:
            draft_data["project_id"] = str(ov.project_id)
        if ov.product_id is not None:
            draft_data["product_id"] = str(ov.product_id)
        if ov.assigned_to is not None:
            draft_data["assigned_to"] = ov.assigned_to
        if ov.extra:
            draft_data.update(ov.extra)

    @staticmethod
    def _resolve_uuid_field(draft_data: dict[str, Any], key: str) -> UUID | None:
        """Parse ``draft_data[key]`` as a UUID; None if absent, raises if malformed."""
        raw = draft_data.get(key)
        if not raw:
            return None
        try:
            return UUID(str(raw))
        except ValueError as exc:
            raise ValidationError(
                message=f"Invalid {key} UUID: {raw}",
                field=key,
            ) from exc

    @staticmethod
    def _coerce_draft_enums(
        draft_data: dict[str, Any],
    ) -> tuple[Team, TaskType, TaskNature, Complexity]:
        """Coerce the draft's required enum fields, raising on missing/invalid."""
        try:
            return (
                Team(draft_data["team"]),
                TaskType(draft_data["task_type"]),
                TaskNature(draft_data["nature"]),
                Complexity(draft_data["estimated_complexity"]),
            )
        except (KeyError, ValueError) as exc:
            raise ValidationError(
                message=f"Draft has invalid or missing required fields: {exc}",
                field="draft",
            ) from exc

    # -----------------------------------------------------------------------
    # Private helpers (session-based)
    # -----------------------------------------------------------------------

    async def _get_session(
        self, session_id: UUID, agent_id: UUID
    ) -> PrompterSessionTable:
        """Load and authorize a PrompterSession."""
        result = await self._session.execute(
            select(PrompterSessionTable).where(PrompterSessionTable.id == session_id)
        )
        rec = result.scalar_one_or_none()
        if rec is None:
            raise NotFoundError(f"Prompter session {session_id} not found")
        if rec.agent_id != agent_id:
            raise ServiceError(
                f"Session {session_id} does not belong to agent {agent_id}"
            )
        return rec

    async def _load_messages(self, session_id: UUID) -> list[PrompterMessageTable]:
        """Return all messages for a session ordered by creation time."""
        result = await self._session.execute(
            select(PrompterMessageTable)
            .where(PrompterMessageTable.session_id == session_id)
            .order_by(PrompterMessageTable.created_at)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Shared LLM helpers
    # -----------------------------------------------------------------------

    async def _llm_chat(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Call the LLM for a chat response. Returns {message, draft_ready}."""
        user_prompt = _build_chat_prompt(messages, context)
        try:
            response = await self._create_message(
                model=model,
                max_tokens=max_tokens,
                system=_PROMPTER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            self.log.error("Prompter chat LLM call failed", error=str(e))
            raise ServiceError(f"LLM chat failed: {e}") from e

        content = _extract_text(response)
        if not content:
            raise ServiceError("LLM returned empty content")

        return {
            "message": content,
            "draft_ready": _detect_draft_ready(content),
        }

    async def _llm_draft(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Call the LLM to generate a structured draft. Returns {draft, reasoning}."""
        user_prompt = _build_draft_prompt(messages, context)
        try:
            response = await self._create_message(
                model=model,
                max_tokens=max_tokens,
                system=_DRAFT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            self.log.error("Prompter draft LLM call failed", error=str(e))
            raise ServiceError(f"LLM draft generation failed: {e}") from e

        content = _extract_text(response)
        if not content:
            raise ServiceError("LLM returned empty content for draft")

        try:
            draft_data = json.loads(content)
        except json.JSONDecodeError as e:
            self.log.warning("Draft JSON parse failed", content_preview=content[:200])
            raise ValidationError(
                message=f"Draft response was not valid JSON: {e}",
                field="draft",
            ) from e

        draft_data["source"] = "prompter"
        draft_data["confirmed_by_human"] = False
        return {
            "draft": draft_data,
            "reasoning": _build_reasoning(messages, draft_data),
        }

    # -----------------------------------------------------------------------
    # Legacy stateless interface
    # -----------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Continue a Prompter conversation (stateless)."""
        return await self._llm_chat(
            messages=messages,
            context=context,
            model=model,
            max_tokens=max_tokens,
        )

    async def draft(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate a structured task draft from conversation context (stateless)."""
        return await self._llm_draft(
            messages=messages,
            context=context,
            model=model,
            max_tokens=max_tokens,
        )


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no state)
# ---------------------------------------------------------------------------


def _build_chat_prompt(
    messages: list[dict[str, str]],
    context: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    if context:
        lines.append("Context:")
        for key, value in context.items():
            lines.append(f"  {key}: {value}")
        lines.append("")
    lines.append("Conversation:")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    lines.append("")
    lines.append(
        "Continue the conversation as the Prompter assistant. "
        "If you have enough information to draft a complete task, "
        "say so explicitly."
    )
    return "\n".join(lines)


def _build_draft_prompt(
    messages: list[dict[str, str]],
    context: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines.append(
        "Produce a JSON task draft from the following conversation. "
        "Return ONLY valid JSON — no markdown, no preamble."
    )
    if context:
        lines.append("")
        lines.append("Overrides:")
        for key, value in context.items():
            lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append("Conversation:")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts).strip()


def _detect_draft_ready(content: str) -> bool:
    signals = [
        "i have enough information",
        "ready to generate a draft",
        "ready to draft",
        "i can now draft",
        "draft_ready=true",
        "draft ready",
    ]
    lower = content.lower()
    return any(sig in lower for sig in signals)


def _build_reasoning(
    messages: list[dict[str, str]],
    draft_data: dict[str, Any],
) -> str:
    title = draft_data.get("title", "Untitled")
    team = draft_data.get("team", "unknown")
    complexity = draft_data.get("estimated_complexity", "unknown")
    return (
        f"Draft generated from conversation of {len(messages)} messages. "
        f"Proposed task '{title}' for team {team} "
        f"with complexity {complexity}."
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_prompter_service(db: AsyncSession | None = None) -> PrompterService:
    """Create a PrompterService instance.

    Pass ``db`` for the session-based interface; omit for the stateless
    legacy interface.
    """
    return PrompterService(db=db)
