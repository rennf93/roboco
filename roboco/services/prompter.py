"""
Prompter Service

Server-side helper for the live SDK-intake flow: turns a confirmed structured
draft into a real Task (``create_task_from_draft`` / ``confirm_live_draft``),
plus the pure helpers that compose a task description and parse the interview
readiness signal.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import select

from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.identity import CELL_TEAMS
from roboco.models.base import (
    AgentRole,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.base import NotFoundError, ServiceError, ValidationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# A board/advisory assignee means a product coordination root is still in board
# review — it stays team=board until the CEO's Approve & Start hands it to Main
# PM. Mirrors `_BOARD_ADVISORY_ROLES` in TaskService.
_BOARD_REVIEW_ROLES: frozenset[AgentRole] = frozenset(
    {AgentRole.PRODUCT_OWNER, AgentRole.HEAD_MARKETING, AgentRole.AUDITOR}
)


@dataclass
class ReadinessTag:
    """Parsed contents of an assistant turn's trailing roboco-meta block."""

    covered: list[str] = field(default_factory=list)
    ready: bool = False
    scale: str | None = None


class PrompterService:
    """Create tasks from confirmed intake drafts.

    Accepts an optional SQLAlchemy ``AsyncSession`` for the DB-backed task
    creation. The pure draft/description helpers below need no session.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.log = logger.bind(component="prompter_service")
        self._db = db

    @property
    def _session(self) -> AsyncSession:
        """Return DB session, raising if not configured."""
        if self._db is None:
            raise ServiceError(
                "PrompterService was created without a DB session; "
                "session-based methods are unavailable"
            )
        return self._db

    async def _assignee_is_board(self, agent_id: UUID) -> bool:
        """True if ``agent_id`` is a board/advisory role (PO / marketing / auditor)."""
        result = await self._session.execute(
            select(AgentTable.role).where(AgentTable.id == agent_id)
        )
        return result.scalar_one_or_none() in _BOARD_REVIEW_ROLES

    async def create_task_from_draft(
        self,
        draft_data: dict[str, Any],
        agent_id: UUID,
        *,
        status: TaskStatus = TaskStatus.BACKLOG,
        assigned_to: UUID | None = None,
    ) -> TaskTable:
        """Create a Task from a structured draft.

        Recomposes the description, validates exactly-one target, coerces enums,
        routes the owning team (product → Main PM, project → lead cell), and
        persists via ``TaskService.create``. Mutates ``draft_data['description']``
        in place. ``confirmed_by_human=True`` — the CEO confirmed it.

        ``status`` defaults to ``BACKLOG``. The live-intake buttons pass
        ``PENDING`` + an ``assigned_to`` (a board agent for "Board review &
        Start", main-pm for "Approve & Start") so the task starts immediately on
        the chosen review path. An explicit ``assigned_to`` wins over any
        assignee carried on the draft.
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

        # Explicit assignment (from the confirm button) wins; else fall back to
        # any assignee carried on the draft. Resolved before team routing — the
        # owner decides the team for a product.
        resolved_assigned_to: UUID | None = assigned_to
        if resolved_assigned_to is None and draft_data.get("assigned_to"):
            with contextlib.suppress(ValueError):
                resolved_assigned_to = UUID(str(draft_data["assigned_to"]))

        # Adaptive routing. A project target is a single-cell executable task
        # owned by the lead cell. A product target is a board-led coordination
        # root whose team follows the start mode (encoded in the assignee): the
        # "Board review & Start" path assigns a board reviewer, so it must stay
        # team=board until approved — otherwise the CEO's Approve & Start gate,
        # which keys on team=board, never appears and the task strands. "Approve
        # & Start" (assignee main-pm) and the post-approval state are team=main_pm.
        if resolved_product_id is None:
            team = self._lead_cell_team(draft_data, default=_lead)
        elif resolved_assigned_to is not None and await self._assignee_is_board(
            resolved_assigned_to
        ):
            team = Team.BOARD
        else:
            team = Team.MAIN_PM

        req = TaskCreateRequest(
            title=draft_data["title"],
            description=draft_data["description"],
            acceptance_criteria=draft_data["acceptance_criteria"],
            team=team,
            created_by=agent_id,
            task_type=task_type,
            nature=nature,
            estimated_complexity=complexity,
            priority=self._coerce_priority(draft_data.get("priority")),
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

        For a board-informed *re-draft* of an existing task the route calls
        :meth:`update_live_draft` instead (updates in place, no new task).

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

    async def update_live_draft(
        self,
        task_id: UUID,
        draft: dict[str, Any],
        *,
        route: Literal["board", "main_pm"] = "main_pm",
    ) -> UUID:
        """Apply a board-informed re-draft to an existing task, then route it.

        The board reviewed the task; the prompter folded that feedback into a
        revised draft. This updates the *same* coordination task in place
        (title / description / acceptance criteria) — never a new task, which
        would duplicate the one the board already reviewed — then routes per the
        button the CEO pressed on the re-draft:

        - ``"main_pm"`` ("Approve & Start") → hand the revised task to the Main
          PM via ``approve_and_start`` (board review is already complete).
        - ``"board"`` → send it back for another review round: clear
          ``board_review_complete`` so the orchestrator re-dispatches the board.
        """
        from roboco.services.task import get_task_service

        draft_data: dict[str, Any] = dict(draft)
        draft_data["description"] = compose_description(draft_data)
        task_service = get_task_service(self._session)
        task = await task_service.update(
            task_id,
            title=draft_data.get("title"),
            description=draft_data["description"],
            acceptance_criteria=draft_data.get("acceptance_criteria"),
        )
        if task is None:
            raise NotFoundError(resource_type="Task", resource_id=str(task_id))

        if route == "main_pm":
            await task_service.approve_and_start(
                task_id,
                notes="Re-drafted with board feedback; approved to build.",
            )
        else:  # re-board: another review round on the revised draft
            await task_service.update(task_id, board_review_complete=False)
        self.log.info(
            "Live intake re-draft applied",
            task_id=str(task_id),
            route=route,
        )
        return task_id

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

    @staticmethod
    def _coerce_priority(value: Any) -> int:
        """Coerce the draft's priority to a valid int (0=urgent … 3=low).

        priority is the one non-enum field the intake agent guesses, and it
        guesses a word ("high") as often as a number. Map the words, clamp
        numbers to 0-3, and default to 2 (medium) on anything unrecognized so the
        launch never crashes on a priority guess (it did: ``int("high")``).
        """
        words = {
            "urgent": 0,
            "critical": 0,
            "high": 1,
            "medium": 2,
            "normal": 2,
            "low": 3,
        }
        if isinstance(value, bool):
            return 2
        if isinstance(value, int):
            return min(max(value, 0), 3)
        if isinstance(value, str):
            key = value.strip().lower()
            if key in words:
                return words[key]
            try:
                return min(max(int(key), 0), 3)
            except ValueError:
                return 2
        return 2


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no state)
# ---------------------------------------------------------------------------


_META_FENCE_RE = re.compile(r"```roboco-meta\s*(.*?)```", re.DOTALL)

# Below this length the composed body is too thin to be a valid task, so we
# fall back to any model-provided description text.
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


def format_board_briefing(entries: list[dict[str, Any]]) -> str:
    """Render board review entries into a markdown briefing for the prompter.

    Used to seed a re-draft intake session with the Product Owner + Head of
    Marketing analysis so the agent revises the draft against real feedback.
    """
    if not entries:
        return ""
    role_label = {
        "product_owner": "Product Owner",
        "head_marketing": "Head of Marketing",
    }
    blocks: list[str] = [
        "The board reviewed your draft. Revise it to incorporate their feedback, "
        "then propose the updated draft. Their reviews:",
    ]
    for e in entries:
        who = role_label.get(str(e.get("author_role")), str(e.get("author") or "Board"))
        title = str(e.get("title") or "").strip()
        content = str(e.get("content") or "").strip()
        header = f"### {who}" + (f" — {title}" if title else "")
        blocks.append(f"{header}\n\n{content}")
    return "\n\n".join(blocks)


def compose_redraft_message(task: TaskTable, entries: list[dict[str, Any]]) -> str:
    """Seed message for a re-draft intake session: the current draft + board review.

    Gives the prompter the existing task draft to revise plus the Product Owner /
    Head of Marketing feedback to fold in, so the fresh session re-drafts the same
    task rather than starting from scratch.
    """
    criteria = "\n".join(f"- {c}" for c in (task.acceptance_criteria or []))
    briefing = format_board_briefing(entries)
    return (
        "You are revising an existing task draft with board feedback.\n\n"
        f"## Current draft: {task.title}\n\n{task.description}\n\n"
        f"### Acceptance criteria\n{criteria}\n\n{briefing}"
    ).strip()


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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_prompter_service(db: AsyncSession | None = None) -> PrompterService:
    """Create a PrompterService instance.

    Pass ``db`` for the DB-backed task-creation interface; omit for the pure
    draft/description helpers.
    """
    return PrompterService(db=db)
