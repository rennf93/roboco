"""
Prompter Service

Conversational LLM assistant that helps users draft tasks.
Uses the project's local LLM (Ollama, OpenAI-compatible) for
natural-language interaction and structured JSON draft generation —
the same engine as RAG/HyDE, so no external API key is required.

Provides both a session-based approach (DB-persisted) and a
legacy stateless interface for backward compatibility.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import (
    PrompterMessageTable,
    PrompterSessionTable,
    TaskDraftTable,
    TaskTable,
)
from roboco.foundation.identity import CELL_TEAMS
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
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
    draft: dict[str, Any] | None = None


@dataclass
class ReadinessTag:
    """Parsed contents of an assistant turn's trailing roboco-meta block."""

    covered: list[str] = field(default_factory=list)
    ready: bool = False
    scale: str | None = None


@dataclass
class TurnResult:
    """Outcome of a chat turn: the message list plus the readiness signal."""

    messages: list[PrompterMessageTable]
    draft_ready: bool = False
    scale: str | None = None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROMPTER_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter — the intake interviewer for an AI agentic "
    "software company. A human describes something they want built; you ask a "
    "few sharp questions, then a launch-ready task spec is handed to the dev "
    "teams.\n\n"
    "How RoboCo is organized:\n"
    "- A human CEO sits above a Board (Product Owner, Head of Marketing, "
    "Auditor).\n"
    "- The Main PM coordinates three delivery cells — Backend, Frontend, and "
    "UX/UI. Each cell has developers, a QA, a PM, and a documenter.\n"
    "- Small, single-domain work (a bug fix, one endpoint, one component) is "
    "one task owned by one cell.\n"
    "- A real feature is board-led: the Board sets requirements, the Main PM "
    "delegates one subtask per participating cell, and the cells deliver in "
    "parallel.\n\n"
    "What a well-formed task looks like (the house standard):\n"
    "- Objective — the outcome, not the implementation.\n"
    "- What This Builds — the concrete artifacts.\n"
    "- The Work — the per-cell breakdown (one cell for small work; Backend, "
    "Frontend, UX/UI for a feature).\n"
    "- Notes — constraints, what to reuse, anything to confirm with the human.\n"
    "- Success Criteria — verifiable acceptance criteria.\n\n"
    "Your interview discipline:\n"
    "- Open by reflecting back, in one or two sentences, what you understand "
    "they want, so they can correct course immediately.\n"
    "- Then ask only the highest-leverage questions you are actually missing — "
    "one or two per turn. Never dump a checklist.\n"
    "- Before you can draft, cover: (1) the true objective, (2) scope "
    "boundaries — what is explicitly out, (3) the surface — which page, "
    "endpoint, or component, grounded in the projects/products you are shown, "
    "(4) reuse vs build — what existing code or services to lean on, "
    "(5) the audience, (6) what 'done' looks like.\n"
    "- Stop as soon as objective, scope, surface, and acceptance are clear. "
    "Aim for two to four turns total. Do not pad the conversation.\n"
    "- Use the real project and product names you are given; prefer an "
    "existing surface over inventing one.\n\n"
    "Every reply ends with exactly one fenced control block the human never "
    "sees, reporting coverage and readiness:\n"
    "```roboco-meta\n"
    '{"covered": ["objective", "scope", "surface", "acceptance"], '
    '"ready": false, "scale": "single"}\n'
    "```\n"
    "- covered: which of objective / scope / surface / reuse / audience / "
    "acceptance you have nailed down.\n"
    "- ready: true only when you could write a complete task spec right now.\n"
    "- scale: 'single' for one-cell work, 'multi' for a board-led feature "
    "across cells.\n"
    "Write nothing after that block."
)

_DRAFT_SYSTEM_PROMPT = (
    "You are the RoboCo Prompter's drafting engine. Given a finished "
    "conversation, output a single JSON object — a structured task "
    "draft. No markdown, no prose, no code fence.\n\n"
    "Required fields:\n"
    "- title: concise, actionable (max 200 chars).\n"
    "- objective: the outcome in one or two sentences.\n"
    "- what_this_builds: array of concrete artifacts (strings).\n"
    "- the_work: array of per-cell slices. Each item is "
    '{"team": backend|frontend|ux_ui, "summary": one line, '
    '"items": [deliverables]}. One entry for single-cell work; one entry per '
    "participating cell for a board-led feature.\n"
    "- acceptance_criteria: array of verifiable criteria (at least one) — "
    "these become Success Criteria.\n"
    "- notes: array of constraints, reuse pointers, things to confirm (may be "
    "empty).\n"
    "- team: the primary cell (backend|frontend|ux_ui). For a multi-cell "
    "feature set the lead cell here; the backend routes it through the Main "
    "PM.\n"
    "- task_type: one of code, documentation, research, planning, design, "
    "administrative.\n"
    "- nature: technical or non_technical.\n"
    "- estimated_complexity: low, medium, high.\n"
    "- priority: integer 0-3 (0 highest, 3 lowest).\n\n"
    "Optional, only if unambiguous from context: project_id, product_id, "
    "assigned_to, target_date.\n\n"
    "Do NOT write a 'description' field — the backend composes it from your "
    "structured fields.\n\n"
    "Example shape (abbreviated):\n"
    '{"title": "...", "objective": "...", "what_this_builds": ["..."], '
    '"the_work": [{"team": "backend", "summary": "...", "items": ["..."]}, '
    '{"team": "frontend", "summary": "...", "items": ["..."]}], '
    '"acceptance_criteria": ["..."], "notes": ["..."], "team": "backend", '
    '"task_type": "code", "nature": "technical", '
    '"estimated_complexity": "high", "priority": 1}\n\n'
    "Return ONLY the JSON object."
)


class PrompterService:
    """Service for Prompter chat, session management, and structured draft generation.

    Accepts an optional SQLAlchemy ``AsyncSession`` for the session-based
    (DB-persisted) interface. When no session is provided, only the legacy
    stateless ``chat()`` and ``draft()`` methods are available.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.log = logger.bind(component="prompter_service")
        self._db = db

    async def _create_message(
        self, *, messages: list[dict[str, str]], max_tokens: int
    ) -> str:
        """Call the local LLM and return the reply text.

        Uses the project's local LLM — the same OpenAI-compatible Ollama
        endpoint as RAG/HyDE (``settings.local_llm_*``), so no external API key
        is required. ``messages`` is an OpenAI-style list (system + turns). This
        is the single seam the prompter tests substitute.
        """
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.local_llm_base_url}/chat/completions",
                json={
                    "model": settings.local_llm_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "options": {"num_ctx": 8192},
                },
            )
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"] or "").strip()

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

    async def create_session(self, agent_id: UUID) -> PrompterSessionTable:
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
    ) -> TurnResult:
        """
        Append a user message, call the LLM for a reply, persist both, and
        return all messages plus the readiness signal for this turn.
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

        # Ground the interview in the real projects/products the human can target
        live_context = await self._assemble_live_context()

        # Call the LLM
        llm_reply = await self._llm_chat(
            messages=chat_messages,
            context=context,
            live_context=live_context,
        )

        # Persist the assistant reply (control block already stripped)
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

        return TurnResult(
            messages=await self._load_messages(session_id),
            draft_ready=bool(llm_reply["draft_ready"]),
            scale=llm_reply.get("scale"),
        )

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

        # Get or generate the draft. A human-edited structured draft, if passed,
        # replaces the stored one before overrides and re-composition.
        draft_record = await self.get_or_generate_draft(session_id, agent_id)
        if ov.draft is not None:
            draft_data: dict[str, Any] = dict(ov.draft)
            draft_data["source"] = "prompter"
            draft_data["confirmed_by_human"] = False
        else:
            draft_data = dict(draft_record.draft_data)
        self._apply_overrides(draft_data, ov)

        task = await self.create_task_from_draft(draft_data, agent_id)

        # Persist the launched draft so the stored record reflects reality.
        draft_record.draft_data = draft_data

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
        return UUID(str(task.id))

    async def create_task_from_draft(
        self,
        draft_data: dict[str, Any],
        agent_id: UUID,
        *,
        status: TaskStatus = TaskStatus.BACKLOG,
        assigned_to: UUID | None = None,
    ) -> TaskTable:
        """Create a Task from a structured draft.

        Shared by both prompter confirm paths (``confirm_draft`` and the
        live-intake ``confirm_live_draft``): recomposes the description,
        validates exactly-one target, coerces enums, routes the owning team
        (product → Main PM, project → lead cell), and persists via
        ``TaskService.create``. Mutates ``draft_data['description']`` in place.
        ``confirmed_by_human=True`` — the CEO confirmed it.

        ``status`` defaults to ``BACKLOG`` (legacy ``confirm_draft`` behaviour).
        The live-intake buttons pass ``PENDING`` + an ``assigned_to`` (a board
        agent for "Board review & Start", main-pm for "Approve & Start") so the
        task starts immediately on the chosen review path. An explicit
        ``assigned_to`` wins over any assignee carried on the draft.
        """
        # Recompose the description from the (possibly edited) structured fields —
        # the task always carries a freshly-composed, consistent description.
        draft_data["description"] = compose_description(draft_data)

        resolved_project_id = self._resolve_uuid_field(draft_data, "project_id")
        resolved_product_id = self._resolve_uuid_field(draft_data, "product_id")
        if resolved_project_id is None and resolved_product_id is None:
            raise ValidationError(
                message=(
                    "The draft must target a project (single-cell) or a product "
                    "(board-led, multi-cell). Pick one in the confirm step."
                ),
                field="project_id",
            )
        if resolved_project_id is not None and resolved_product_id is not None:
            raise ValidationError(
                message="Set exactly one of project_id or product_id, not both.",
                field="product_id",
            )

        _lead, task_type, nature, complexity = self._coerce_draft_enums(draft_data)

        # Adaptive routing: a product target is a board-led coordination root
        # owned by the Main PM (who fans out per cell); a project target is a
        # single-cell executable task owned by the cell doing the work.
        if resolved_product_id is not None:
            team = Team.MAIN_PM
        else:
            team = self._lead_cell_team(draft_data, default=_lead)

        # Explicit assignment (from the confirm button) wins; else fall back to
        # any assignee carried on the draft.
        resolved_assigned_to: UUID | None = assigned_to
        if resolved_assigned_to is None and draft_data.get("assigned_to"):
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
            status=status,
            source="prompter",
            confirmed_by_human=True,
        )

        # Import TaskService lazily to avoid circular imports
        from roboco.services.task import get_task_service

        task_service = get_task_service(self._session)
        return await task_service.create(req)

    async def confirm_live_draft(
        self,
        draft: dict[str, Any],
        agent_id: UUID,
        *,
        project_id: UUID | None = None,
        product_id: UUID | None = None,
        route: Literal["board", "main_pm"] = "board",
    ) -> UUID:
        """Confirm a live-intake draft → create + start the task; return its id.

        The human picked one of two start buttons (``route``):

        - ``"board"`` ("Board review & Start") → task at PENDING assigned to the
          Product Owner, so the orchestrator dispatches the full Board review
          (PO + Head of Marketing) before it reaches the Main PM.
        - ``"main_pm"`` ("Approve & Start") → task at PENDING assigned to the Main
          PM, who delegates to the cells directly (Board review skipped).

        Enum fields the dialog doesn't surface default to sane values so a
        confirm never fails on a missing ``nature``.
        """
        from roboco.seeds.initial_data import AGENT_UUIDS

        draft_data: dict[str, Any] = dict(draft)
        if project_id is not None:
            draft_data["project_id"] = str(project_id)
        if product_id is not None:
            draft_data["product_id"] = str(product_id)
        # Fields the confirm dialog doesn't expose — default rather than reject.
        draft_data.setdefault("task_type", TaskType.CODE.value)
        draft_data.setdefault("nature", TaskNature.TECHNICAL.value)
        draft_data.setdefault("estimated_complexity", Complexity.MEDIUM.value)
        draft_data.setdefault("priority", 2)

        assignee_slug = "product-owner" if route == "board" else "main-pm"
        assigned_to = UUID(AGENT_UUIDS[assignee_slug])
        task = await self.create_task_from_draft(
            draft_data, agent_id, status=TaskStatus.PENDING, assigned_to=assigned_to
        )
        self.log.info(
            "Live intake draft confirmed — task started",
            task_id=str(task.id),
            route=route,
            assigned_to=assignee_slug,
        )
        return UUID(str(task.id))

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
    def _lead_cell_team(draft_data: dict[str, Any], default: Team) -> Team:
        """Owner of a single-cell task: first *valid* cell in the_work, else default.

        Skips cell names that aren't valid ``Team`` values rather than raising —
        the intake agent is an LLM and can emit an off-enum cell name.
        """
        for raw in _cell_teams(draft_data.get("the_work") or []):
            try:
                return Team(raw)
            except ValueError:
                continue
        return default

    @staticmethod
    def _coerce_draft_enums(
        draft_data: dict[str, Any],
    ) -> tuple[Team, TaskType, TaskNature, Complexity]:
        """Coerce the draft's enum fields to valid values; default on invalid/missing.

        The intake agent is an LLM and will occasionally emit an off-enum value
        (e.g. ``task_type="feature"``, which is not a ``TaskType``). The
        confirm/launch action must NEVER hard-fail on a cosmetic enum guess — that
        forces the agent to self-correct in-chat, which is unacceptable UX. Coerce
        to a sane default instead; ``team`` falls back to the lead cell, then backend.
        """
        try:
            team = Team(draft_data["team"])
        except (KeyError, ValueError, TypeError):
            team = PrompterService._lead_cell_team(draft_data, Team.BACKEND)
        try:
            task_type = TaskType(draft_data["task_type"])
        except (KeyError, ValueError, TypeError):
            task_type = TaskType.CODE
        try:
            nature = TaskNature(draft_data["nature"])
        except (KeyError, ValueError, TypeError):
            nature = TaskNature.TECHNICAL
        try:
            complexity = Complexity(draft_data["estimated_complexity"])
        except (KeyError, ValueError, TypeError):
            complexity = Complexity.MEDIUM
        return team, task_type, nature, complexity

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
            raise NotFoundError("Prompter session", str(session_id))
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

    async def _assemble_live_context(self) -> str | None:
        """Build a compact 'Available projects / products' block for the interview.

        Grounds the assistant in the real targets the human can launch against,
        so it references existing surfaces and can resolve project/product
        itself. Best-effort: a lookup failure degrades to no context rather than
        breaking the chat. Returns None when nothing is registered.
        """
        if self._db is None:
            return None

        from roboco.services.product import get_product_service
        from roboco.services.project import get_project_service

        lines: list[str] = []
        try:
            projects = await get_project_service(self._session).list_all(
                active_only=True, limit=50
            )
        except Exception as exc:
            self.log.warning("Live project list unavailable", error=str(exc))
            projects = []
        if projects:
            lines.append("Available projects (single-cell tasks target one of these):")
            lines.extend(f"  - {p.name} (slug: {p.slug}, id: {p.id})" for p in projects)

        try:
            products = await get_product_service(self._session).list_all(limit=50)
        except Exception as exc:
            self.log.warning("Live product list unavailable", error=str(exc))
            products = []
        if products:
            lines.append(
                "Available products (board-led multi-cell features target one "
                "of these):"
            )
            lines.extend(
                f"  - {pr.name} (slug: {pr.slug}, id: {pr.id})" for pr in products
            )

        return "\n".join(lines) if lines else None

    # -----------------------------------------------------------------------
    # Shared LLM helpers
    # -----------------------------------------------------------------------

    async def _llm_chat(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        live_context: str | None = None,
    ) -> dict[str, Any]:
        """Call the LLM for a chat response.

        Returns ``{message, draft_ready, scale}`` where ``message`` is the
        user-visible reply with the trailing roboco-meta control block stripped.
        """
        user_prompt = _build_chat_prompt(messages, context, live_context)
        try:
            content = await self._create_message(
                messages=[
                    {"role": "system", "content": _PROMPTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
        except Exception as e:
            self.log.error("Prompter chat LLM call failed", error=str(e))
            raise ServiceError(f"LLM chat failed: {e}") from e

        if not content:
            raise ServiceError("LLM returned empty content")

        clean, tag = parse_readiness(content)
        # If the model omitted the control block, fall back to the clean text
        # so the user still sees a reply rather than an empty bubble.
        message = clean or content
        return {
            "message": message,
            "draft_ready": bool(tag and tag.ready),
            "scale": tag.scale if tag else None,
        }

    async def _llm_draft(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Call the LLM to generate a structured draft. Returns {draft, reasoning}."""
        user_prompt = _build_draft_prompt(messages, context)
        try:
            content = await self._create_message(
                messages=[
                    {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
        except Exception as e:
            self.log.error("Prompter draft LLM call failed", error=str(e))
            raise ServiceError(f"LLM draft generation failed: {e}") from e

        if not content:
            raise ServiceError("LLM returned empty content for draft")

        try:
            draft_data = json.loads(_strip_code_fences(content))
        except json.JSONDecodeError as e:
            self.log.warning("Draft JSON parse failed", content_preview=content[:200])
            raise ValidationError(
                message=f"Draft response was not valid JSON: {e}",
                field="draft",
            ) from e

        draft_data["source"] = "prompter"
        draft_data["confirmed_by_human"] = False
        # Compose the markdown description from the structured fields — the model
        # never hand-formats it, so the description is always consistent.
        draft_data["description"] = compose_description(draft_data)
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
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Continue a Prompter conversation (stateless)."""
        return await self._llm_chat(
            messages=messages,
            context=context,
            max_tokens=max_tokens,
        )

    async def draft(
        self,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate a structured task draft from conversation context (stateless)."""
        return await self._llm_draft(
            messages=messages,
            context=context,
            max_tokens=max_tokens,
        )


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no state)
# ---------------------------------------------------------------------------


def _build_chat_prompt(
    messages: list[dict[str, str]],
    context: dict[str, Any] | None,
    live_context: str | None = None,
) -> str:
    lines: list[str] = []
    if live_context:
        lines.append(live_context)
        lines.append("")
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
        "Continue the conversation as the Prompter assistant. End with the "
        "roboco-meta control block. If you can write a complete task spec now, "
        "set ready to true."
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


def _strip_code_fences(content: str) -> str:
    """Strip a wrapping markdown code fence (```json ... ```) if present.

    Local models often wrap JSON output in a fenced block; drop the opening
    fence line and the closing fence so the body parses cleanly as JSON.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


_META_FENCE_RE = re.compile(r"```roboco-meta\s*(.*?)```", re.DOTALL)

# Mirror of PrompterDraftTask.description min_length — below this the composed
# body is too thin to be a valid task, so we fall back to any provided text.
_MIN_DESCRIPTION_LEN = 20

_TEAM_LABELS: dict[str, str] = {
    "backend": "Backend",
    "frontend": "Frontend",
    "ux_ui": "UX/UI",
    "main_pm": "Main PM",
    "board": "Board",
}


def parse_readiness(content: str) -> tuple[str, ReadinessTag | None]:
    """Split an assistant reply into (clean_text, readiness_tag).

    The interview prompt instructs the model to end each turn with a fenced
    ``roboco-meta`` JSON block. This extracts the last such block, strips it
    from the user-visible text, and parses it. A missing or malformed block
    yields ``None`` (treated as not-ready) so the conversation never breaks.
    """
    matches = list(_META_FENCE_RE.finditer(content))
    if not matches:
        return content.strip(), None

    # Strip every control block from the visible text (a well-behaved model
    # emits one; remove any strays too), and read readiness from the last.
    clean = _META_FENCE_RE.sub("", content).strip()
    try:
        data = json.loads(matches[-1].group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return clean, None
    if not isinstance(data, dict):
        return clean, None

    raw_scale = data.get("scale")
    scale = str(raw_scale) if raw_scale in ("single", "multi") else None
    covered = [str(c) for c in data.get("covered") or [] if isinstance(c, str)]
    return clean, ReadinessTag(
        covered=covered,
        ready=bool(data.get("ready", False)),
        scale=scale,
    )


def _cell_teams(the_work: list[dict[str, Any]]) -> list[str]:
    """Distinct cell teams (backend/frontend/ux_ui) present in the_work, in order."""
    cell_values = {t.value for t in CELL_TEAMS}
    seen: list[str] = []
    for entry in the_work:
        team = str(entry.get("team", ""))
        if team in cell_values and team not in seen:
            seen.append(team)
    return seen


def derive_scale(the_work: list[dict[str, Any]]) -> str:
    """'multi' when more than one cell participates, else 'single'."""
    return "multi" if len(_cell_teams(the_work)) > 1 else "single"


def _clean_list(value: Any) -> list[str]:
    """Trimmed, non-empty string items from a possibly-missing list field."""
    return [str(i).strip() for i in (value or []) if str(i).strip()]


def _text(value: Any) -> str:
    """Trimmed string from a possibly-missing scalar field."""
    return str(value or "").strip()


def _bullets(items: list[str]) -> str:
    """Render a markdown bullet list."""
    return "\n".join(f"- {i}" for i in items)


def _cell_label(team: str) -> str:
    """Display label for a team value."""
    return _TEAM_LABELS.get(team) or team.replace("_", " ").title() or "Work"


def _render_work_entry(entry: dict[str, Any]) -> str:
    """Render one cell's slice: a bold heading and its deliverables."""
    head = f"**{_cell_label(_text(entry.get('team')))}**"
    summary = _text(entry.get("summary"))
    if summary:
        head = f"{head} — {summary}"
    items = _clean_list(entry.get("items"))
    return f"{head}\n{_bullets(items)}" if items else head


def _render_the_work(the_work: list[dict[str, Any]]) -> str:
    """Render The Work section, with a board-led lead line when multi-cell."""
    blocks = [_render_work_entry(e) for e in the_work]
    if len(_cell_teams(the_work)) > 1:
        blocks.insert(
            0,
            "Board-led: the Board sets requirements and the Main PM "
            "delegates one subtask per cell.",
        )
    return "\n\n".join(blocks)


def _section(sections: list[str], heading: str, body: str) -> None:
    """Append a markdown section when its body is non-empty."""
    if body:
        sections.append(f"## {heading}\n\n{body}")


def compose_description(draft: dict[str, Any]) -> str:
    """Build the markdown description deterministically from structured fields.

    Sections present only when their field has content. ``acceptance_criteria``
    renders under Success Criteria. A multi-cell task gets a board-led lead
    line. Falls back to any model-provided ``description`` if the structured
    fields are too sparse to clear the schema's 20-char minimum.
    """
    the_work = draft.get("the_work") or []
    sections: list[str] = []
    _section(sections, "Objective", _text(draft.get("objective")))
    _section(
        sections,
        "What This Builds",
        _bullets(_clean_list(draft.get("what_this_builds"))),
    )
    _section(sections, "The Work", _render_the_work(the_work) if the_work else "")
    _section(sections, "Notes", _bullets(_clean_list(draft.get("notes"))))
    _section(
        sections,
        "Success Criteria",
        _bullets(_clean_list(draft.get("acceptance_criteria"))),
    )

    composed = "\n\n".join(sections).strip()
    if len(composed) >= _MIN_DESCRIPTION_LEN:
        return composed
    return _text(draft.get("description")) or composed


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
