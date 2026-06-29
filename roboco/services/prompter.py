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
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select

from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.identity import CELL_TEAMS
from roboco.foundation.policy.batch import is_batch_umbrella, main_pm_cannot_own_code
from roboco.foundation.policy.content.validators import coerce_str_list
from roboco.foundation.policy.sequencing.models import DraftSurface, SequencePlan
from roboco.models.base import (
    AgentRole,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.product import ProductCellMapping
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

# Per-cell developer headcount the MegaTask analyzer uses to *warn* (never block)
# when a wave puts more same-cell root-subtasks in flight than the cell has devs.
# Each delivery cell ships two developers (see the org blueprint in CLAUDE.md);
# advisory only, so a coarse constant is sufficient.
_CELL_CAPACITY: dict[str, int] = {
    Team.BACKEND.value: 2,
    Team.FRONTEND.value: 2,
    Team.UX_UI.value: 2,
}

# A MegaTask must span at least this many distinct projects — fewer is a
# single-repo batch, which is just an ordinary (multi-)task, not a MegaTask.
_MIN_MEGATASK_PROJECTS = 2

# A draft whose per-cell map covers at least this many cells targets the ad-hoc
# multi-cell shape (a root-subtask with a cell->project map, no single project).
# Below it, a 1-cell map collapses to the single-project shape.
_MULTI_CELL_MIN = 2


@dataclass
class ReadinessTag:
    """Parsed contents of an assistant turn's trailing roboco-meta block."""

    covered: list[str] = field(default_factory=list)
    ready: bool = False
    scale: str | None = None


@dataclass(frozen=True)
class BatchPlacement:
    """Where a draft sits inside a MegaTask batch.

    All four are set together by the batch create path and left at their
    defaults for an ordinary single-draft confirm. ``team_override`` pins the
    owning team for the whole batch; ``parent_task_id`` is the umbrella (or None
    for the umbrella itself); ``batch_id`` is the shared batch identity; and
    ``sequence`` is the item's wave index.
    """

    parent_task_id: UUID | None = None
    batch_id: UUID | None = None
    sequence: int = 0
    team_override: Team | None = None


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

    @staticmethod
    def _validate_draft_target(
        project_id: UUID | None,
        product_id: UUID | None,
        *,
        is_umbrella: bool,
        has_cell_projects: bool = False,
    ) -> None:
        """A draft targets exactly one of project / product / per-cell map — or
        none when it is a MegaTask umbrella (branchless; its root-subtasks carry
        the projects). The per-cell map is the multi-cell ad-hoc shape (a
        root-subtask mixing per-cell projects from different products / OSS libs).
        """
        targets = bool(project_id) + bool(product_id) + bool(has_cell_projects)
        if is_umbrella:
            if targets != 0:
                raise ValidationError(
                    message=(
                        "A MegaTask umbrella targets neither project nor product "
                        "(it is branchless); its root-subtasks carry the projects."
                    ),
                    field="project_id",
                )
            return
        if targets != 1:
            raise ValidationError(
                message=(
                    "The draft must target exactly one of a project (single-cell), "
                    "a product (board-led, multi-cell), or a per-cell project map "
                    "(ad-hoc multi-cell). Pick one in the confirm step."
                ),
                field="project_id",
            )

    async def _resolve_owning_team(
        self,
        draft_data: dict[str, Any],
        *,
        resolved_product_id: UUID | None,
        resolved_assigned_to: UUID | None,
        team_override: Team | None,
        default_lead: Team,
    ) -> Team:
        """Route the owning team for a draft.

        ``team_override`` pins the team for a MegaTask batch (umbrella + every
        root-subtask share one owner). Otherwise: a project target is a
        single-cell executable owned by the lead cell; a product target OR an
        ad-hoc per-cell map (≥2 cells in ``the_work``) is a multi-cell
        coordination root owned by the Main PM — the cell map mirrors a product
        fan-out root, so a cell PM (which can only delegate within its own cell)
        must NOT own it (that would deadlock on the cross-cell fan-out). A
        product's team follows the start mode (encoded in the assignee) — the
        "Board review & Start" path assigns a board reviewer, so it stays
        team=board until approved (else the CEO's Approve & Start gate, which
        keys on team=board, never appears and the task strands). "Approve &
        Start" (assignee main-pm) and the post-approval state are team=main_pm.
        """
        if team_override is not None:
            return team_override
        if len(_draft_cell_map(draft_data)) >= _MULTI_CELL_MIN:
            # Ad-hoc multi-cell map → coordination root, like a product root.
            return Team.MAIN_PM
        if resolved_product_id is None:
            return self._lead_cell_team(draft_data, default=default_lead)
        if resolved_assigned_to is not None and await self._assignee_is_board(
            resolved_assigned_to
        ):
            return Team.BOARD
        return Team.MAIN_PM

    def _validate_and_coerce_draft(self, draft_data: dict[str, Any]) -> None:
        """Validate title + acceptance criteria, then flatten the list-shaped
        fields (acceptance_criteria / what_this_builds / notes / each the_work
        unit's items) to ``list[str]`` in place.

        Raises ``ValidationError`` (clean 400) for a missing title or empty /
        missing acceptance criteria — a malformed draft (e.g. an incomplete
        ``propose_batch`` item) would otherwise hit a bare ``KeyError`` and
        surface as an opaque 500. Coercion runs here too because a draft can
        arrive via re-draft / localStorage, not only the intake choke point.
        """
        if not draft_data.get("title"):
            raise ValidationError(
                message="This task draft is missing a title.", field="title"
            )
        if not draft_data.get("acceptance_criteria"):
            raise ValidationError(
                message="This task draft is missing acceptance criteria.",
                field="acceptance_criteria",
            )
        draft_data["acceptance_criteria"] = coerce_str_list(
            draft_data.get("acceptance_criteria")
        )
        draft_data["what_this_builds"] = coerce_str_list(
            draft_data.get("what_this_builds")
        )
        draft_data["notes"] = coerce_str_list(draft_data.get("notes"))
        for unit in draft_data.get("the_work") or []:
            if isinstance(unit, dict):
                unit["items"] = coerce_str_list(unit.get("items"))
        if not draft_data["acceptance_criteria"]:
            raise ValidationError(
                message="This task draft is missing acceptance criteria.",
                field="acceptance_criteria",
            )

    def _resolve_draft_assignee(
        self, assigned_to: UUID | None, draft_data: dict[str, Any]
    ) -> UUID | None:
        """Explicit confirm-button assignment wins; else fall back to any assignee
        carried on the draft."""
        if assigned_to is not None:
            return assigned_to
        if not draft_data.get("assigned_to"):
            return None
        with contextlib.suppress(ValueError):
            return UUID(str(draft_data["assigned_to"]))
        return None

    async def create_task_from_draft(
        self,
        draft_data: dict[str, Any],
        agent_id: UUID,
        *,
        status: TaskStatus = TaskStatus.BACKLOG,
        assigned_to: UUID | None = None,
        placement: BatchPlacement | None = None,
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

        ``placement`` carries the MegaTask batch position (see
        :class:`BatchPlacement`): a batch umbrella targets neither project nor
        product (it is branchless), and a root-subtask carries its own project
        plus the umbrella as parent. The collision descriptors
        (``intends_to_touch`` / ``adds_migration`` / ``touches_shared``) ride the
        draft through to the task so the analyzer's surface is persisted.
        """
        place = placement or BatchPlacement()
        self._validate_and_coerce_draft(draft_data)
        # Recompose the description from the (possibly edited) structured fields —
        # the task always carries a freshly-composed, consistent description.
        draft_data["description"] = compose_description(draft_data)

        resolved_project_id = self._resolve_uuid_field(draft_data, "project_id")
        resolved_product_id = self._resolve_uuid_field(draft_data, "product_id")
        # The ad-hoc per-cell map: ≥2 cells → multi-cell root-subtask (cell map
        # shape, no project/product); 1 cell → single-project (use that project);
        # 0 → fall back to the top-level project_id (single-cell legacy).
        cell_map = _draft_cell_map(draft_data)
        cell_projects: list[ProductCellMapping] = []
        if len(cell_map) >= _MULTI_CELL_MIN:
            cell_projects = [
                ProductCellMapping(team=team, project_id=pid) for team, pid in cell_map
            ]
            resolved_project_id = None
            resolved_product_id = None
        elif len(cell_map) == 1:
            resolved_project_id = cell_map[0][1]
            resolved_product_id = None
        self._validate_draft_target(
            resolved_project_id,
            resolved_product_id,
            is_umbrella=is_batch_umbrella(
                batch_id=place.batch_id, parent_task_id=place.parent_task_id
            ),
            has_cell_projects=bool(cell_projects),
        )

        _lead, task_type, nature, complexity = self._coerce_draft_enums(draft_data)

        # Explicit assignment (from the confirm button) wins; else fall back to
        # any assignee carried on the draft. Resolved before team routing — the
        # owner decides the team for a product.
        resolved_assigned_to = self._resolve_draft_assignee(assigned_to, draft_data)

        team = await self._resolve_owning_team(
            draft_data,
            resolved_product_id=resolved_product_id,
            resolved_assigned_to=resolved_assigned_to,
            team_override=place.team_override,
            default_lead=_lead,
        )

        # A Main PM coordinates — it never owns a code task. A main_pm + code
        # draft is the structural mismatch behind the 2026-06-27 MegaTask
        # meltdown (the git/PR/review layer treated the root as code while the
        # ownership layer treated it as coordination → pr_fail loop). Intake
        # coerces code -> planning here so the combo can never persist; a
        # root-subtask / umbrella / single-task main_pm route is a coordination
        # root whose code ACs live on the delegated cell/dev leaves. The
        # TaskService.create backstop rejects main_pm + code for non-intake
        # create paths (the HTTP route).
        if main_pm_cannot_own_code(team=team, task_type=task_type):
            self.log.info(
                "Main-PM intake task coerced code->planning",
                team=str(getattr(team, "value", team)),
                title=_text(draft_data.get("title")) or "",
            )
            task_type = TaskType.PLANNING

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
            cell_projects=cell_projects,
            status=status,
            parent_task_id=place.parent_task_id,
            batch_id=place.batch_id,
            sequence=place.sequence,
            intends_to_touch=_clean_list(draft_data.get("intends_to_touch")) or None,
            adds_migration=bool(draft_data.get("adds_migration")),
            touches_shared=bool(draft_data.get("touches_shared")),
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

    def _sequence_drafts(self, drafts: list[dict[str, Any]]) -> SequencePlan:
        """Build each draft's collision surface and sequence them into waves.

        Pure (no DB, no side effects). The single source of the wave plan, shared
        by ``preview_batch`` (panel pre-confirm preview) and ``confirm_live_batch``
        (create), so the previewed waves are exactly the ones that get wired.
        """
        from roboco.foundation.policy.sequencing.models import SequencingError
        from roboco.services.sequencing import SequencingService

        surfaces = [
            DraftSurface(
                idx=idx,
                priority=self._coerce_priority(d.get("priority")),
                intends_to_touch=_clean_list(d.get("intends_to_touch")),
                adds_migration=bool(d.get("adds_migration")),
                touches_shared=bool(d.get("touches_shared")),
                project_id=str(d["project_id"]) if d.get("project_id") else None,
            )
            for idx, d in enumerate(drafts)
        ]
        cell_by_idx = {
            idx: self._lead_cell_team(d, default=Team.BACKEND).value
            for idx, d in enumerate(drafts)
        }
        try:
            return SequencingService().analyze(
                surfaces, lambda i: cell_by_idx[i], _CELL_CAPACITY
            )
        except SequencingError as exc:
            # A cyclic / malformed collision graph is a user-actionable input
            # problem, not a server fault — surface it as a clean 400.
            raise ValidationError(
                message=(
                    "These tasks can't be sequenced into conflict-free waves: "
                    f"{exc}. Adjust what they touch and try again."
                ),
                field="drafts",
            ) from exc

    def preview_batch(self, drafts: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute a MegaTask's waves + warnings WITHOUT creating anything.

        The panel calls this once the agent proposes a batch so the human can
        review the sequencing before confirming. ``waves`` is a list of waves,
        each a list of draft indices that run together.
        """
        if not drafts:
            raise ValidationError(
                message="A MegaTask needs at least one task draft.", field="drafts"
            )
        plan = self._sequence_drafts(drafts)
        return {"waves": plan.waves, "warnings": plan.warnings}

    @staticmethod
    def _validate_batch_scope(
        drafts: list[dict[str, Any]], project_ids: list[UUID]
    ) -> None:
        """A MegaTask's drafts must each target one of the scoped repos (the only
        ones the intake agent read), and collectively span at least two distinct
        projects — otherwise it's a single-repo batch, not a MegaTask.

        Each draft targets its repos via its per-cell ``the_work[].project_id``
        map (a multi-cell draft) or, falling back, its top-level ``project_id``
        (a single-cell draft). Every targeted project must be in scope, and the
        union across all drafts' cells must clear ``_MIN_MEGATASK_PROJECTS``
        (a single 2-cell draft already satisfies it).
        """
        scope = {str(p) for p in project_ids}
        seen: set[str] = set()
        for idx, draft in enumerate(drafts):
            cell_map = _draft_cell_map(draft)
            top_pid = draft.get("project_id")
            if cell_map:
                draft_pids = [str(pid) for _, pid in cell_map]
            elif top_pid:
                draft_pids = [str(top_pid)]
            else:
                raise ValidationError(
                    message=(
                        f"Task {idx + 1} has no project. Point each of its cells at "
                        "one of the scoped projects."
                    ),
                    field="drafts",
                )
            for pid in draft_pids:
                if pid not in scope:
                    raise ValidationError(
                        message=(
                            f"Task {idx + 1} targets a project outside this "
                            "MegaTask's selected repos. Point it at one of the scoped "
                            "projects."
                        ),
                        field="drafts",
                    )
                seen.add(pid)
        if len(seen) < _MIN_MEGATASK_PROJECTS:
            raise ValidationError(
                message=(
                    "A MegaTask must span at least two distinct projects — use a "
                    "single-project task for work in one repo."
                ),
                field="drafts",
            )

    async def confirm_live_batch(
        self,
        title: str,
        drafts: list[dict[str, Any]],
        agent_id: UUID,
        *,
        project_ids: list[UUID],
        route: Literal["board", "main_pm"] = "board",
    ) -> dict[str, Any]:
        """Confirm a MegaTask: create the umbrella + N sequenced root-subtasks.

        Each draft carries its own ``project_id`` (one of the scoped ``project_ids``
        the intake agent read) and a collision surface (``intends_to_touch`` /
        ``adds_migration`` / ``touches_shared``). The pure :class:`SequencingService`
        turns those surfaces into conflict-free waves; the umbrella (branchless,
        batch-owning coordination root) groups the root-subtasks, and the existing
        dependency-gate executes the waves — each root-subtask keeping its own
        project / branch / PR.

        ``route`` picks the start path exactly like a single draft. ``"board"``
        sends the umbrella to the Board (PO + HoM) for one batch review, with the
        root-subtasks held in ``BACKLOG`` until the umbrella is approved.
        ``"main_pm"`` hands the umbrella straight to the Main PM and creates the
        root-subtasks ``PENDING`` so the dependency-gate dispatches wave 0 at once.

        Returns ``{umbrella_task_id, root_subtask_ids, waves, warnings}`` for the
        panel's wave/DAG review.
        """
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.task import get_task_service

        if not drafts:
            raise ValidationError(
                message="A MegaTask needs at least one task draft.", field="drafts"
            )
        self._validate_batch_scope(drafts, project_ids)

        batch_id = uuid4()
        # 1. Sequence the drafts into conflict-free waves (same plan the panel
        #    previewed via preview_batch — _sequence_drafts is the single source).
        plan = self._sequence_drafts(drafts)
        wave_of = {idx: w for w, wave in enumerate(plan.waves) for idx in wave}

        # 2. Route → owning team + umbrella assignee + held status for the items.
        #    "board" holds the root-subtasks until the batch review approves the
        #    umbrella; "main_pm" lets the dependency-gate dispatch wave 0 at once.
        if route == "main_pm":
            owning_team = Team.MAIN_PM
            umbrella_assignee = UUID(AGENT_UUIDS["main-pm"])
            subtask_status = TaskStatus.PENDING
        else:
            owning_team = Team.BOARD
            umbrella_assignee = UUID(AGENT_UUIDS["product-owner"])
            subtask_status = TaskStatus.BACKLOG

        # 3. The umbrella: a branchless coordination root that owns the batch and
        #    is the single board-review / CEO-approve / Main-PM-coordinate unit.
        umbrella = await self.create_task_from_draft(
            _compose_umbrella_draft(title, drafts, plan),
            agent_id,
            status=TaskStatus.PENDING,
            assigned_to=umbrella_assignee,
            placement=BatchPlacement(batch_id=batch_id, team_override=owning_team),
        )
        umbrella_id = UUID(str(umbrella.id))

        # 4. The root-subtasks: each carries its own project / branch / PR, with
        #    sequence = its wave index and the umbrella as parent.
        task_of: dict[int, UUID] = {}
        for idx, draft in enumerate(drafts):
            sub = await self.create_task_from_draft(
                dict(draft),
                agent_id,
                status=subtask_status,
                placement=BatchPlacement(
                    parent_task_id=umbrella_id,
                    batch_id=batch_id,
                    sequence=wave_of[idx],
                    team_override=owning_team,
                ),
            )
            task_of[idx] = UUID(str(sub.id))

        # 5. Wire the dependency edges. An edge ``(a, b)`` means *b waits on a*,
        #    so b depends on a — the dependency-gate then releases each wave only
        #    once the prior wave's items reach a terminal state.
        task_service = get_task_service(self._session)
        for a, b in plan.edges:
            await task_service.add_dependency(task_of[b], task_of[a])

        self.log.info(
            "MegaTask batch confirmed",
            umbrella_task_id=str(umbrella_id),
            items=len(drafts),
            waves=len(plan.waves),
            route=route,
        )
        return {
            "umbrella_task_id": str(umbrella_id),
            "root_subtask_ids": [str(task_of[i]) for i in range(len(drafts))],
            "waves": plan.waves,
            "warnings": plan.warnings,
        }

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


def _as_work_entry(entry: Any) -> dict[str, Any]:
    """Normalize one ``the_work`` entry to a dict.

    The intake agent is an LLM and sometimes emits ``the_work`` as a list of
    bare team-name strings (``"backend"``) instead of the documented
    ``{team, summary, items}`` objects. Treat a bare string as
    ``{"team": <str>}`` so every consumer can keep calling ``.get("team")`` /
    ``.get("items")`` instead of crashing with ``'str' has no 'get'``
    (regression: ``preview-batch`` 500'd on this shape).
    """
    if isinstance(entry, str):
        return {"team": entry.strip()}
    if isinstance(entry, dict):
        return entry
    return {}


def _cell_teams(the_work: list[Any]) -> list[str]:
    """Distinct cell teams (backend/frontend/ux_ui) present in the_work, in order."""
    cell_values = {t.value for t in CELL_TEAMS}
    seen: list[str] = []
    for entry in the_work:
        team = str(_as_work_entry(entry).get("team", ""))
        if team in cell_values and team not in seen:
            seen.append(team)
    return seen


def _draft_cell_map(draft: dict[str, Any]) -> list[tuple[Team, UUID]]:
    """The draft's ad-hoc per-cell project map.

    One ``(team, project_id)`` per ``the_work`` entry whose ``team`` is a valid
    cell AND that carries a ``project_id``, in ``the_work`` order, de-duped by
    team (the first mapping for a cell wins — a ``task_cell_projects`` row is
    unique per ``(task, team)``). Empty when no entry carries a project_id — the
    draft then falls back to its top-level ``project_id`` (single-cell legacy).

    This is the multi-cell MegaTask root-subtask seam: a draft whose map has
    ≥2 entries targets the ad-hoc cell-map shape (no project, no product), and
    ``create_task_from_draft`` persists those rows on the root-subtask.
    """
    cell_values = {t.value for t in CELL_TEAMS}
    out: list[tuple[Team, UUID]] = []
    seen_teams: set[Team] = set()
    for entry in draft.get("the_work") or []:
        e = _as_work_entry(entry)
        team_raw = str(e.get("team", ""))
        if team_raw not in cell_values:
            continue
        team = Team(team_raw)
        if team in seen_teams:
            continue
        pid_raw = e.get("project_id")
        if not pid_raw:
            continue
        try:
            pid = UUID(str(pid_raw))
        except (ValueError, TypeError):
            continue
        seen_teams.add(team)
        out.append((team, pid))
    return out


def derive_scale(the_work: list[Any]) -> str:
    """'multi' when more than one cell participates, else 'single'."""
    return "multi" if len(_cell_teams(the_work)) > 1 else "single"


def _clean_list(value: Any) -> list[str]:
    """Trimmed, non-empty string items from a possibly-missing list field.

    Uses :func:`coerce_str_list` so an LLM's dict-wrapped items (``{"$text": …}``
    etc.) are extracted to text rather than dumped as ``str(dict)``.
    """
    return coerce_str_list(value)


def _text(value: Any) -> str:
    """Trimmed string from a possibly-missing scalar field."""
    return str(value or "").strip()


def _bullets(items: list[str]) -> str:
    """Render a markdown bullet list."""
    return "\n".join(f"- {i}" for i in items)


def _cell_label(team: str) -> str:
    """Display label for a team value."""
    return _TEAM_LABELS.get(team) or team.replace("_", " ").title() or "Work"


def _render_work_entry(entry: Any) -> str:
    """Render one cell's slice: a bold heading and its deliverables.

    ``entry`` may be a bare team-name string (see ``_as_work_entry``); a bare
    string renders as just the cell heading, with no summary/items.
    """
    e = _as_work_entry(entry)
    head = f"**{_cell_label(_text(e.get('team')))}**"
    summary = _text(e.get("summary"))
    if summary:
        head = f"{head} — {summary}"
    items = _clean_list(e.get("items"))
    return f"{head}\n{_bullets(items)}" if items else head


def _render_the_work(the_work: list[Any]) -> str:
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


def _compose_umbrella_draft(
    title: str, drafts: list[dict[str, Any]], plan: Any
) -> dict[str, Any]:
    """Build the umbrella's draft from the batch + its computed wave plan.

    The umbrella targets neither project nor product (it is branchless) and
    carries no collision surface of its own — it exists to group the batch, hold
    the wave plan in its description for review, and be the one board-review /
    CEO-approve / Main-PM-coordinate unit. It is a Main-PM coordination root, so
    ``task_type=planning`` (a Main PM coordinates, it does not execute code); the
    branch/PR exemption comes from the batch identity, not the type.
    """
    item_titles = [
        _text(d.get("title")) or f"Task {i + 1}" for i, d in enumerate(drafts)
    ]
    wave_lines = [
        f"Wave {w + 1}: " + ", ".join(item_titles[i] for i in wave)
        for w, wave in enumerate(plan.waves)
    ]
    return {
        "title": f"MegaTask: {title}",
        "objective": (
            f"Coordinate {len(drafts)} sequenced tasks as one MegaTask. The Board "
            "reviews the batch once; the Main PM coordinates the root-subtasks, "
            "which the dependency-gate dispatches in collision-free waves, each "
            "keeping its own pull request."
        ),
        "what_this_builds": item_titles,
        "notes": [*wave_lines, *plan.warnings],
        "acceptance_criteria": [
            "Every root-subtask in the MegaTask is completed and merged.",
        ],
        "task_type": TaskType.PLANNING.value,
        "nature": TaskNature.TECHNICAL.value,
        "estimated_complexity": Complexity.HIGH.value,
        "priority": 1,
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_prompter_service(db: AsyncSession | None = None) -> PrompterService:
    """Create a PrompterService instance.

    Pass ``db`` for the DB-backed task-creation interface; omit for the pure
    draft/description helpers.
    """
    return PrompterService(db=db)
