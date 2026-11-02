"""Board Program proposals that become tasks: the pitch, the roadmap cycle,
and the evidence-backed audits (Pest Control, Scales, Spackle, Mirror,
Dogfood), with what every proposal shares.

Mixins composed into ContentActions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.foundation.policy.board_programs import BoardProgram

if TYPE_CHECKING:
    from roboco.services.gateway._content_actions_types import (
        ContentActionsSelf as _Base,
    )
else:
    _Base = object

logger = structlog.get_logger()


# The Board roles that may author a pitch (a product proposal for CEO approval).
_PITCH_ROLES: frozenset[str] = frozenset({"product_owner", "head_marketing"})

# Roadmap cycles are PO-authored in v1 — HoM stays a reviewer via the normal
# board gate when an approved item later ships as real work (see the roadmap
# spec's non-goals).
_ROADMAP_ROLES: frozenset[str] = frozenset({"product_owner"})

# Pest Control (Board Program) bug hunts are Product-Owner-only, mirroring
# _ROADMAP_ROLES.
_PEST_ROLES: frozenset[str] = frozenset({"product_owner"})

# Spackle (Board Program) gap-fill audits are Product-Owner-only, mirroring
# _PEST_ROLES.
_GAP_FILL_ROLES: frozenset[str] = frozenset({"product_owner"})

# Mirror (Board Program) messaging-fix audits are Head-of-Marketing-only —
# the mirror image of _GAP_FILL_ROLES.
_MESSAGING_FIXES_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Text fields on a roadmap item draft, with their anti-soup minimum length.
_ROADMAP_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("rationale", 8),
)

# Text fields on a pest-hunt item draft. ``evidence`` is the load-bearing one
# (spec §4: "a bug hunt without evidence is noise") — required, substantive,
# and capped so a runaway dump can't blow out the marker payload.
_PEST_HUNT_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_PEST_HUNT_EVIDENCE_MAX_CHARS = 2000

# Text fields on a gap-fill item draft. ``evidence`` is the load-bearing one
# (spec §4: evidence must name BOTH sides of the gap — e.g. the route that
# exists and the panel surface that doesn't) — required, substantive, and
# capped so a runaway dump can't blow out the marker payload. Mirrors
# _PEST_HUNT_ITEM_TEXT_FIELDS.
_GAP_FILL_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_GAP_FILL_EVIDENCE_MAX_CHARS = 2000

# Scales (Board Program) rebalance plans are Product-Owner-only, mirroring
# _PEST_ROLES. Unlike a roadmap/pest-control item, a rebalance item never
# drafts a NEW task — it references a LIVE one (``task_ref``) that approval
# mutates (reprioritize) or cancels, so there is no team/project-slug/
# acceptance-criteria shape to validate here, just the action + rationale.
_SCALES_ROLES: frozenset[str] = frozenset({"product_owner"})
_SCALES_ACTIONS: frozenset[str] = frozenset({"reprioritize", "cancel"})
_SCALES_VALID_PRIORITIES: frozenset[int] = frozenset({0, 1, 2, 3})
_SCALES_RATIONALE_MAX_CHARS = 500

# Text fields on a messaging-fix item draft. ``evidence`` is the load-bearing
# one (spec §4: must name the drifted claim AND the reality it contradicts)
# — required, substantive, and capped so a runaway dump can't blow out the
# marker payload. Mirrors _GAP_FILL_ITEM_TEXT_FIELDS.
_MESSAGING_FIX_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_MESSAGING_FIX_EVIDENCE_MAX_CHARS = 2000

# Dogfood (Board Program) friction-fix audits are Product-Owner-only,
# mirroring _GAP_FILL_ROLES.
_DOGFOOD_ROLES: frozenset[str] = frozenset({"product_owner"})

# Text fields on a friction-fix item draft. ``evidence`` is the load-bearing
# one (spec §4: the actual walked path — clicks/pages — plus what broke or
# felt wrong, prose, no screenshots) — required, substantive, and capped so
# a runaway dump can't blow out the marker payload. Mirrors
# _GAP_FILL_ITEM_TEXT_FIELDS.
_FRICTION_FIXES_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_FRICTION_FIXES_EVIDENCE_MAX_CHARS = 2000

# nothing_to_propose is registry-driven, not role-frozenset-gated like every
# verb above — it resolves the caller's NAMED task, derives the program from
# that task's own source, then requires the caller's role to equal THAT
# program's declared explorer role
# (roboco.foundation.policy.board_programs.PROGRAMS), so a program registered
# later needs no edit here.
_NOTHING_TO_PROPOSE_REASON_MIN_CHARS = 15
_NOTHING_TO_PROPOSE_REASON_MAX_CHARS = 800


def _coerce_pitch_cells(target_cells: list[str]) -> list[Any]:
    """Validate target-cell slugs into Team values; raise ValueError on a bad one."""
    from roboco.foundation.identity import CELL_TEAMS, Team

    cells: list[Any] = []
    for c in target_cells:
        try:
            team = Team(c)
        except ValueError as exc:
            raise ValueError(f"unknown cell {c!r}") from exc
        if team not in CELL_TEAMS:
            raise ValueError(f"{c!r} is not a cell team")
        cells.append(team)
    return cells


def _normalize_roadmap_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw item dict into the stored marker shape.

    ``id`` is server-assigned (index-based, stable within the cycle) — the PO
    never sets it, so there's no collision/typo surface for the CEO's
    per-item approve/reject to key on.
    """
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "rationale": str(raw["rationale"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_pest_hunt_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw pest-hunt item dict into the stored marker
    shape. Mirrors ``_normalize_roadmap_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_gap_fill_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw gap-fill item dict into the stored marker
    shape. Mirrors ``_normalize_pest_hunt_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_scales_item(
    idx: int, raw: dict[str, Any], target: Any
) -> dict[str, Any]:
    """Coerce a validated raw rebalance item dict + its resolved target task
    into the stored marker shape. Mirrors ``_normalize_pest_hunt_item`` —
    ``id`` is server-assigned. Unlike a pest-hunt item there is no draft to
    normalize: ``target`` (resolved by ``TaskService.resolve_scales_task_ref``
    before this is called) supplies the id/title actually acted on."""
    action = str(raw["action"]).strip()
    return {
        "id": f"item-{idx}",
        "task_ref": str(raw["task_ref"]).strip(),
        "target_task_id": str(target.id),
        "target_task_title": target.title,
        "action": action,
        "new_priority": raw.get("new_priority") if action == "reprioritize" else None,
        "rationale": str(raw["rationale"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "executed_detail": None,
    }


def _normalize_messaging_fix_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw messaging-fix item dict into the stored marker
    shape. Mirrors ``_normalize_gap_fill_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_friction_fix_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw friction-fix item dict into the stored marker
    shape. Mirrors ``_normalize_messaging_fix_item`` — ``id`` is server-
    assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


class BoardProposalActions(_Base):
    """What every Board Program proposal shares: the durable trace of a
    ``propose_*`` payload, and ``nothing_to_propose``, a cycle's explicit
    empty exit."""

    async def _trace_board_proposal(
        self, *, agent_id: UUID, verb: str, payload: dict[str, Any]
    ) -> None:
        """Durably trace a board-program ``propose_*`` payload BEFORE any
        validation runs.

        The 2026-07-25 incident: a rejected/mis-persisted proposal came back
        as a 200 carrying an error envelope, with the raw payload living
        nowhere but that one HTTP response — unrecoverable once the caller
        moved on. Uses ``AuditService``'s own independent session/commit
        (never ``self.task.session``), so the trace survives even when this
        verb's own validation rejects the call outright and no downstream
        write ever happens. Best-effort — ``AuditService.log_event`` never
        raises, so a trace failure can never block the proposal itself.
        """
        from roboco.services.audit import get_audit_service

        await get_audit_service().log_event(
            event_type="board_program.proposal_trace",
            agent_id=agent_id,
            details={"verb": verb, "payload": payload},
            severity="info",
        )

    async def _resolve_nothing_to_propose_task(
        self, agent_id: UUID, task_id: UUID
    ) -> Envelope | tuple[Any, BoardProgram]:
        """Resolve + validate ``task_id`` for ``nothing_to_propose``: exists,
        its ``source`` is a registered Board Program, it is assigned to the
        caller, it is non-terminal, and the caller's role matches that
        program's declared explorer role. Returns the first failing check's
        envelope, else ``(task, program)`` — split out of the verb itself
        purely to keep its own return count under the lint ceiling.
        """
        from roboco.foundation.policy.board_programs import PROGRAMS

        task = await self.task.get(task_id)
        if task is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        program = next((p for p in PROGRAMS.values() if p.source == task.source), None)
        if program is None:
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} source {task.source!r} is not a registered "
                    "Board Program"
                ),
                remediate="this task is not a Board Program exploration cycle",
                context_briefing={},
            )
        if task.assigned_to != agent_id:
            return Envelope.not_authorized(
                message=f"task {task_id} is not assigned to you",
                remediate=(
                    "nothing_to_propose only completes the caller's own "
                    "exploration task — pass the task_id printed as "
                    "'TASK: <id>' at the top of your prompt"
                ),
                context_briefing={},
            )
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return Envelope.invalid_state(
                message=f"task {task_id} is already {task.status.value}",
                remediate="this exploration cycle is already closed; nothing to do",
                context_briefing={},
            )
        role = await self._caller_role(agent_id)
        if role != program.role:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot resolve {program.key!r}'s exploration "
                    f"task; only {program.role!r} does"
                ),
                remediate=(
                    f"nothing_to_propose only completes a {program.role} "
                    "explorer's own cycle"
                ),
                context_briefing={},
            )
        return task, program

    async def nothing_to_propose(
        self,
        *,
        agent_id: UUID,
        task_id: UUID | str,
        reason: str,
    ) -> Envelope:
        """The explicit "this cycle found nothing worth proposing" exit for
        ANY Board Program exploration task, named explicitly by ``task_id``.

        Every ``propose_*`` verb requires at least one item (or a single
        substantive report), so an explorer that legitimately has nothing —
        Barfly found no worthwhile X conversations, Coroner has no autopsy
        subject worth a process change — had no way to complete its task; it
        correctly declined and called ``i_am_idle()``, leaving the task
        PENDING forever: a permanent, expensive respawn loop, and (worse)
        ``BoardProgramEngine``'s one-open-cycle dedup wedges that whole
        program shut, since the ledger row never closes.

        ``task_id`` is REQUIRED, not inferred: one explorer role owns several
        independently-cadenced programs at once (e.g. head_marketing owns
        x_feature/periscope/mirror/megaphone/war_room/barfly), each assigning
        its own exploration task to the same agent — so several of one
        agent's exploration tasks can be open simultaneously by design, and
        guessing (e.g. "the oldest one") completes the WRONG cycle and
        stamps its LEARN reason onto the wrong ledger row. The exploration
        prompt always prints "TASK: <id>", so the caller has it in hand
        (mirrors ``curate_vault``'s explicit ``task_id`` convention). See
        ``_resolve_nothing_to_propose_task`` for the resolution + validation
        (exists, registered program, assigned to caller, non-terminal, role
        matches) — registry-driven, not a hardcoded role set, so a program
        registered later needs no edit here.

        Completes the task (mirrors ``propose_conversation_replies``'/
        ``propose_postmortem``'s complete-at-propose pattern) and records the
        reason onto the LEARN ledger row so the next cycle's exploration
        prompt sees WHY, not just a bare "proposed 0, approved 0".
        """
        if rej := self._reject_soup(
            reason,
            field="reason",
            min_chars=_NOTHING_TO_PROPOSE_REASON_MIN_CHARS,
        ):
            return rej
        if len(reason) > _NOTHING_TO_PROPOSE_REASON_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"reason is {len(reason)} chars, over the "
                    f"{_NOTHING_TO_PROPOSE_REASON_MAX_CHARS}-char cap"
                ),
                remediate="shorten the reason",
                context_briefing={},
            )

        resolved_ref = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved_ref, Envelope):
            return resolved_ref
        task_id = resolved_ref
        resolved = await self._resolve_nothing_to_propose_task(agent_id, task_id)
        if isinstance(resolved, Envelope):
            return resolved
        task, program = resolved

        reason = reason.strip()
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

        from roboco.services.board_programs import get_board_program_engine

        try:
            # Isolated in its own savepoint: record_nothing_to_propose does
            # its own flush() on this SAME session, and a bare try/except
            # around a same-session flush is not enough — a genuine DB
            # failure there leaves the session pending-rollback, so the
            # completion flushed just above would be silently discarded at
            # the outer commit despite this except swallowing the error.
            async with self.task.session.begin_nested():
                await get_board_program_engine(
                    self.task.session
                ).record_nothing_to_propose(program.key, cast("UUID", task.id), reason)
        except Exception:
            logger.warning(
                "nothing_to_propose: LEARN record failed (best-effort)",
                program=program.key,
                task_id=str(task.id),
            )

        return Envelope.ok(
            status="nothing_to_propose",
            task_id=str(task.id),
            next="i_am_idle()",
            context_briefing={"program": program.key, "reason": reason},
        )


class PitchActions(_Base):
    """A Board pitch: the Product Owner or Head of Marketing proposes a
    product for the CEO to approve, and the CEO is nudged."""

    async def pitch(
        self,
        *,
        agent_id: UUID,
        title: str,
        slug: str,
        problem: str,
        proposed_solution: str,
        target_cells: list[str],
    ) -> Envelope:
        """Board (PO / Head of Marketing) proposes a product for the CEO to approve.

        A pitch is content, not a lifecycle transition: it records the Board's
        proposal. On CEO approval the system provisions a repo per target cell,
        registers the projects, and seeds the first Main-PM task.
        """
        for _pf, _pv, _min in (
            ("title", title, 5),
            ("slug", slug, 2),
            ("problem", problem, 15),
            ("proposed_solution", proposed_solution, 15),
        ):
            if rej := self._reject_soup(_pv, field=_pf, min_chars=_min):
                return rej
        from pydantic import ValidationError as PydanticValidationError

        from roboco.models.pitch import PitchCreate
        from roboco.services.base import ConflictError, ValidationError
        from roboco.services.pitch import get_pitch_service

        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role not in _PITCH_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {caller_role!r} cannot pitch; only the Board "
                    "(product_owner / head_marketing) may propose products"
                ),
                remediate="this verb is Board-only",
                context_briefing={},
            )
        try:
            create = PitchCreate(
                title=title,
                slug=slug,
                problem=problem,
                proposed_solution=proposed_solution,
                target_cells=_coerce_pitch_cells(target_cells),
            )
            pitch = await get_pitch_service(self.task.session).create(
                create, created_by=agent_id
            )
        except (
            ConflictError,
            ValidationError,
            PydanticValidationError,
            ValueError,
        ) as exc:
            detail = getattr(exc, "message", None) or str(exc)
            return Envelope.invalid_state(
                message=detail,
                remediate="fix the pitch fields and retry",
                context_briefing={},
            )
        await self._notify_pitch(pitch)
        return Envelope.ok(
            status="proposed",
            task_id=str(pitch.id),
            next="await the CEO's approval in the Pitches queue",
            context_briefing={},
        )

    async def _notify_pitch(self, pitch: Any) -> None:
        """Best-effort CEO nudge the moment a pitch is proposed — without it
        a pitch rots silently until the CEO happens to open the Pitches
        queue. A send failure never fails ``pitch()`` itself."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: this persists a notification row, so a mid-flush DB
            # failure swallowed here would otherwise poison the session and
            # blow up the commit-at-send with PendingRollbackError.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_pitch(pitch=pitch)
        except Exception as exc:
            logger.warning("pitch telegram notify failed (best-effort)", error=str(exc))


class RoadmapActions(_Base):
    """Roadmap: the Product Owner's themed roadmap cycle, each item's
    validation, the excluded-project check, and the per-item DMs."""

    @classmethod
    def _reject_roadmap_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + acceptance-criteria fields of one item dict."""
        for field, min_chars in _ROADMAP_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_roadmap_item_team(raw: dict[str, Any], idx: int) -> Envelope | None:
        """Validate the item's ``team`` is a known cell (backend/frontend/ux_ui)."""
        from roboco.foundation.identity import CELL_TEAMS, Team

        try:
            team = Team(str(raw.get("team")))
        except ValueError:
            team = None
        if team is None or team not in CELL_TEAMS:
            return Envelope.invalid_state(
                message=f"item {idx} has an unknown team {raw.get('team')!r}",
                remediate="team must be one of: backend, frontend, ux_ui",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_roadmap_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw roadmap item dict's shape/fields; None when clean.

        Synchronous — no DB access. Split from ``_reject_roadmap_item`` (which
        adds the Task-6b project-exclusion check) so the shape checks stay
        classmethod-testable without a session.
        """
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/rationale"
                ),
                context_briefing={},
            )
        if rej := cls._reject_roadmap_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_roadmap_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw roadmap item dict; None when clean.

        Folds the shape/fields/team checks with the Task-6b project-exclusion
        check into one call so the ``propose_roadmap`` loop keeps a single
        return point per item (xenon/PLR0911 budget).
        """
        if rej := self._reject_roadmap_item_shape(raw, idx):
            return rej
        return await self._reject_excluded_roadmap_project(raw, idx)

    async def _reject_excluded_roadmap_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that excluded itself from the
        roadmap program (``!roadmap`` in its ``board_programs``) — the PO
        learns this at propose time instead of a silent materialize-time skip.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time as it already did before Task
        6b (this check only ever narrows an otherwise-valid slug).
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["roadmap"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which excluded "
                    "itself from the roadmap program"
                ),
                remediate=(
                    f"drop item {idx} or retarget it to a project not "
                    "excluded via '!roadmap'"
                ),
                context_briefing={},
            )
        return None

    async def propose_roadmap(
        self,
        *,
        agent_id: UUID,
        cycle_goal: str,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a themed roadmap cycle (goal + item drafts).

        Persists the cycle onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the roadmap queue. One call per cycle: the exploration task
        stays open (and this verb keeps refusing) until every item is
        terminal.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_roadmap",
            payload={"cycle_goal": cycle_goal, "items": items},
        )
        role = await self._caller_role(agent_id)
        if role not in _ROADMAP_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a roadmap cycle; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        if rej := self._reject_soup(cycle_goal, field="cycle_goal", min_chars=8):
            return rej
        min_items = settings.roadmap_min_items_per_cycle
        max_items = settings.roadmap_max_items_per_cycle
        if not (min_items <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a cycle needs {min_items}-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between {min_items} and {max_items} roadmap items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_roadmap_item(raw, idx):
                return rej
            normalized.append(_normalize_roadmap_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_roadmap_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_roadmap_cycle(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open roadmap exploration task assigned to you",
                remediate=(
                    "propose_roadmap only runs against an active exploration "
                    "cycle spawned by the roadmap engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_roadmap_cycle(
            task, {"goal": cycle_goal.strip(), "items": normalized}
        )
        await self.task.session.flush()
        await self._notify_roadmap_items(task, normalized)
        return Envelope.ok(
            status="roadmap_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the roadmap queue",
            context_briefing={
                "cycle_goal": cycle_goal.strip(),
                "item_count": len(normalized),
            },
        )

    async def _notify_roadmap_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — this is the moment a
        roadmap item first becomes CEO-actionable (the engine's own
        exploration-task origination has nothing to review yet), so the DM
        fires here rather than from ``RoadmapEngine``. A send failure never
        blocks ``propose_roadmap`` itself."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="roadmap",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "roadmap telegram notify failed (best-effort)", error=str(exc)
                )


class PestControlActions(_Base):
    """Pest Control: the Product Owner's evidence-backed bug hunt, each
    item's validation, the opted-in project check, and the per-item DMs."""

    @classmethod
    def _reject_pest_hunt_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one pest-hunt item dict."""
        for field, min_chars in _PEST_HUNT_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_pest_hunt_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        pest-hunt item dict — split from the text-fields loop above to keep
        ``_reject_pest_hunt_item_fields`` under the xenon complexity budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _PEST_HUNT_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_PEST_HUNT_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_PEST_HUNT_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_pest_hunt_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one pest-hunt item dict. Mirrors ``_reject_roadmap_item_fields``."""
        if rej := cls._reject_pest_hunt_item_text_fields(raw, idx):
            return rej
        return cls._reject_pest_hunt_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_pest_hunt_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw pest-hunt item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for both item kinds."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_pest_hunt_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_pest_hunt_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw pest-hunt item dict; None when clean. Mirrors
        ``_reject_roadmap_item``."""
        if rej := self._reject_pest_hunt_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_pest_control_project(raw, idx)

    async def _reject_unparticipating_pest_control_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        pest_control program (``"pest_control"`` absent from its
        ``board_programs``) — the positive-gate mirror of
        ``_reject_excluded_roadmap_project``'s ``!roadmap`` exclusion check:
        project-scoped programs are opt-in, so the polarity flips.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as roadmap's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["pest_control"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the pest_control program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "pest_control on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_bug_hunt(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Pest Control bug hunt (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the hunt onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the pest-control queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_roadmap`` — no top-level theme goal here,
        just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_bug_hunt", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _PEST_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a bug hunt; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["pest_control"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a bug hunt needs 1-{max_items} item drafts, got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_pest_hunt_item(raw, idx):
                return rej
            normalized.append(_normalize_pest_hunt_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_pest_control_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_pest_hunt(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open pest-control exploration task assigned to you",
                remediate=(
                    "propose_bug_hunt only runs against an active exploration "
                    "cycle spawned by the pest-control engine; wait for the "
                    "next cycle"
                ),
                context_briefing={},
            )
        markers.set_pest_hunt(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_pest_hunt_items(task, normalized)
        return Envelope.ok(
            status="pest_hunt_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the pest-control queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_pest_hunt_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_roadmap_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="pest_control",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "pest-control telegram notify failed (best-effort)",
                    error=str(exc),
                )


class ScalesActions(_Base):
    """Scales: the Product Owner's portfolio-rebalance plan, each item's
    validation with the live task it references, and the per-item DMs."""

    @staticmethod
    def _reject_scales_item_action_and_priority(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate ``action`` + the conditional ``new_priority`` requirement
        of one raw rebalance item dict — split out to keep
        ``_reject_scales_item_shape`` under the xenon/PLR0911 budget."""
        action = raw.get("action")
        if action not in _SCALES_ACTIONS:
            return Envelope.invalid_state(
                message=f"item {idx} has an invalid action {action!r}",
                remediate="action must be 'reprioritize' or 'cancel'",
                context_briefing={},
            )
        if action != "reprioritize":
            return None
        new_priority = raw.get("new_priority")
        if (
            not isinstance(new_priority, int)
            or isinstance(new_priority, bool)
            or new_priority not in _SCALES_VALID_PRIORITIES
        ):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} is 'reprioritize' but new_priority is {new_priority!r}"
                ),
                remediate=(
                    "new_priority is required for a reprioritize item and must "
                    "be one of 0 (P0/highest) .. 3 (P3/lowest)"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_scales_item_rationale(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the required ``rationale`` field — split out of
        ``_reject_scales_item_shape`` to keep its own return-statement count
        under the xenon/PLR0911 budget."""
        rationale = raw.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'rationale'",
                remediate=f"provide a substantive rationale for item {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            rationale, field=f"item {idx} rationale", min_chars=8
        ):
            return rej
        if len(rationale) > _SCALES_RATIONALE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} rationale is {len(rationale)} chars, over the "
                    f"{_SCALES_RATIONALE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s rationale to "
                    f"{_SCALES_RATIONALE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_scales_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw rebalance item dict's shape/fields; None when
        clean (before ``task_ref`` resolution, which needs the DB)."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with task_ref/action/"
                    "new_priority/rationale"
                ),
                context_briefing={},
            )
        task_ref = raw.get("task_ref")
        if not isinstance(task_ref, str) or not task_ref.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'task_ref'",
                remediate=(
                    f"provide the id8 or exact title of the live task item "
                    f"{idx} targets"
                ),
                context_briefing={},
            )
        if rej := cls._reject_scales_item_action_and_priority(raw, idx):
            return rej
        return cls._reject_scales_item_rationale(raw, idx)

    async def _reject_scales_item(
        self, raw: dict[str, Any], idx: int
    ) -> tuple[Envelope | None, Any]:
        """Validate one raw rebalance item, then resolve its ``task_ref``.

        Returns ``(None, target_task)`` when clean, ``(rejection, None)``
        otherwise. Resolution happens here (not a separate pass) since a
        ``task_ref`` only makes sense checked against a real live task.
        """
        if rej := self._reject_scales_item_shape(raw, idx):
            return rej, None
        from roboco.services.task import get_task_service

        target = await get_task_service(self.task.session).resolve_scales_task_ref(
            str(raw["task_ref"]).strip()
        )
        if target is None:
            return (
                Envelope.invalid_state(
                    message=(
                        f"item {idx} task_ref {raw['task_ref']!r} does not "
                        "resolve to a live BACKLOG/PENDING task"
                    ),
                    remediate=(
                        f"item {idx}'s task_ref must be the id8 or exact title "
                        "of a live BACKLOG/PENDING task"
                    ),
                    context_briefing={},
                ),
                None,
            )
        return None, target

    async def propose_rebalance(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Scales portfolio-rebalance plan (1-N
        re-priority/cancellation items against the LIVE backlog, N = the
        registry's ``max_items_per_cycle``).

        Persists the plan onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the Scales queue. One call per cycle: the exploration task
        stays open (and this verb keeps refusing) until every item is
        terminal. Unlike ``propose_roadmap``/``propose_bug_hunt`` an item
        never drafts a NEW task — it references a LIVE one (``task_ref``,
        resolved to a real BACKLOG/PENDING task here) that approval MUTATES
        (reprioritize) or cancels, never creates.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_rebalance", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _SCALES_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a rebalance plan; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["scales"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a rebalance plan needs 1-{max_items} item drafts, got "
                    f"{len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            rejection, target = await self._reject_scales_item(raw, idx)
            if rejection is not None:
                return rejection
            normalized.append(_normalize_scales_item(idx, raw, target))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_scales_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_rebalance_plan(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open scales exploration task assigned to you",
                remediate=(
                    "propose_rebalance only runs against an active exploration "
                    "cycle spawned by the scales engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_rebalance_plan(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_rebalance_items(task, normalized)
        return Envelope.ok(
            status="rebalance_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the Scales queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_rebalance_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="scales",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("target_task_title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "scales telegram notify failed (best-effort)", error=str(exc)
                )


class SpackleActions(_Base):
    """Spackle: the Product Owner's evidence-backed gap-fill audit, each
    item's validation, the opted-in project check, and the per-item DMs."""

    @classmethod
    def _reject_gap_fill_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one gap-fill item dict. Mirrors
        ``_reject_pest_hunt_item_text_fields``."""
        for field, min_chars in _GAP_FILL_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_gap_fill_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        gap-fill item dict — split from the text-fields loop above to keep
        ``_reject_gap_fill_item_fields`` under the xenon complexity budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _GAP_FILL_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_GAP_FILL_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_GAP_FILL_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_gap_fill_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one gap-fill item dict. Mirrors ``_reject_pest_hunt_item_fields``."""
        if rej := cls._reject_gap_fill_item_text_fields(raw, idx):
            return rej
        return cls._reject_gap_fill_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_gap_fill_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw gap-fill item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_gap_fill_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_gap_fill_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw gap-fill item dict; None when clean. Mirrors
        ``_reject_pest_hunt_item``."""
        if rej := self._reject_gap_fill_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_spackle_project(raw, idx)

    async def _reject_unparticipating_spackle_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        spackle program (``"spackle"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_pest_control_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as pest_control's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["spackle"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the spackle program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "spackle on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_gap_fill(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Spackle gap-fill audit (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the spackle queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_bug_hunt`` — no top-level theme goal
        here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_gap_fill", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _GAP_FILL_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a gap-fill audit; only "
                    "the Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["spackle"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a gap-fill audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_gap_fill_item(raw, idx):
                return rej
            normalized.append(_normalize_gap_fill_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_spackle_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_gap_fill(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open spackle exploration task assigned to you",
                remediate=(
                    "propose_gap_fill only runs against an active exploration "
                    "cycle spawned by the spackle engine; wait for the next "
                    "cycle"
                ),
                context_briefing={},
            )
        markers.set_gap_fill(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_gap_fill_items(task, normalized)
        return Envelope.ok(
            status="gap_fill_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the spackle queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_gap_fill_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="spackle",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "spackle telegram notify failed (best-effort)",
                    error=str(exc),
                )


class MirrorActions(_Base):
    """Mirror: the Head of Marketing's positioning audit (messaging fixes),
    each item's validation, the opted-in project check, and the per-item
    DMs."""

    @classmethod
    def _reject_messaging_fix_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one messaging-fix item dict. Mirrors
        ``_reject_gap_fill_item_text_fields``."""
        for field, min_chars in _MESSAGING_FIX_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_messaging_fix_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        messaging-fix item dict — split from the text-fields loop above to
        keep ``_reject_messaging_fix_item_fields`` under the xenon complexity
        budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _MESSAGING_FIX_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_MESSAGING_FIX_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_MESSAGING_FIX_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_messaging_fix_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one messaging-fix item dict. Mirrors ``_reject_gap_fill_item_fields``."""
        if rej := cls._reject_messaging_fix_item_text_fields(raw, idx):
            return rej
        return cls._reject_messaging_fix_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_messaging_fix_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw messaging-fix item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_messaging_fix_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_messaging_fix_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw messaging-fix item dict; None when clean. Mirrors
        ``_reject_gap_fill_item``."""
        if rej := self._reject_messaging_fix_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_mirror_project(raw, idx)

    async def _reject_unparticipating_mirror_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        mirror program (``"mirror"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_spackle_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as spackle's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["mirror"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the mirror program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "mirror on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_messaging_fixes(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Head of Marketing authors a Mirror positioning audit (1-N
        evidence-backed item drafts, N = the registry's
        ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the mirror queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_gap_fill`` — no top-level theme goal
        here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_messaging_fixes",
            payload={"items": items},
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _MESSAGING_FIXES_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose messaging fixes; only "
                    "the Head of Marketing authors this audit"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        max_items = PROGRAMS["mirror"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a messaging-fixes audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_messaging_fix_item(raw, idx):
                return rej
            normalized.append(_normalize_messaging_fix_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_mirror_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_messaging_fixes(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open mirror exploration task assigned to you",
                remediate=(
                    "propose_messaging_fixes only runs against an active "
                    "exploration cycle spawned by the mirror engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_messaging_fixes(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_messaging_fix_items(task, normalized)
        return Envelope.ok(
            status="messaging_fixes_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the mirror queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_messaging_fix_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_gap_fill_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="mirror",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "mirror telegram notify failed (best-effort)",
                    error=str(exc),
                )


class DogfoodActions(_Base):
    """Dogfood: the Product Owner's friction audit from a walked path, each
    item's validation, the opted-in project check, and the per-item DMs."""

    @classmethod
    def _reject_friction_fix_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one friction-fix item dict. Mirrors
        ``_reject_messaging_fix_item_text_fields``."""
        for field, min_chars in _FRICTION_FIXES_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_friction_fix_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        friction-fix item dict — split from the text-fields loop above to
        keep ``_reject_friction_fix_item_fields`` under the xenon complexity
        budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _FRICTION_FIXES_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_FRICTION_FIXES_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_FRICTION_FIXES_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_friction_fix_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one friction-fix item dict. Mirrors ``_reject_messaging_fix_item_fields``."""
        if rej := cls._reject_friction_fix_item_text_fields(raw, idx):
            return rej
        return cls._reject_friction_fix_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_friction_fix_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw friction-fix item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_friction_fix_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_friction_fix_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw friction-fix item dict; None when clean. Mirrors
        ``_reject_messaging_fix_item``."""
        if rej := self._reject_friction_fix_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_dogfood_project(raw, idx)

    async def _reject_unparticipating_dogfood_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        dogfood program (``"dogfood"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_mirror_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as mirror's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["dogfood"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the dogfood program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "dogfood on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_friction_fixes(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Dogfood friction audit (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the dogfood queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_messaging_fixes`` — no top-level theme
        goal here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_friction_fixes",
            payload={"items": items},
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _DOGFOOD_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose friction fixes; only "
                    "the Product Owner authors this audit"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["dogfood"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a friction audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_friction_fix_item(raw, idx):
                return rej
            normalized.append(_normalize_friction_fix_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_dogfood_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_friction_fixes(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open dogfood exploration task assigned to you",
                remediate=(
                    "propose_friction_fixes only runs against an active "
                    "exploration cycle spawned by the dogfood engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_friction_fixes(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_friction_fix_items(task, normalized)
        return Envelope.ok(
            status="friction_fixes_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the dogfood queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_friction_fix_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_messaging_fix_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="dogfood",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "dogfood telegram notify failed (best-effort)",
                    error=str(exc),
                )
