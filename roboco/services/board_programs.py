"""BoardProgramEngine — the generic trigger/dedup/originate/LEARN engine every
Board Program registry entry (``roboco.foundation.policy.board_programs``)
rides, replacing per-engine loops + dedup ledgers.

``run_due_programs`` is what the orchestrator's ``_board_program_loop`` calls
on a tick: for each CRON program, check enabled, dedup against the
``board_program_cycles`` ledger, check the cron interval, and — on due —
delegate origination to the program's proven callable (``RoadmapEngine.
run_cycle`` / ``XEngine.open_feature_spotlight_exploration``). This engine
never authors content itself, same posture as the engines it wraps.

Dedup note: an open ledger row (``closed_at IS NULL``) blocks a new cycle,
but a row is only a REAL block while its exploration task is still
non-terminal. A task going terminal (COMPLETED/CANCELLED) — the roadmap
service's own "every item decided" rule, or the X engine completing the
exploration the instant ``propose_feature_spotlight`` runs — auto-closes the
row on the next check. Without this, a ledger row can outlive the condition
it was tracking (e.g. an x_feature exploration completes at propose time,
long before the CEO decides the materialized draft) and would otherwise wedge
the program's dedup forever, a regression from the per-engine dedup this
replaces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import BoardProgramCycleTable, ProjectTable
from roboco.foundation.policy.board_programs import (
    PROGRAMS,
    TriggerKind,
    program_due,
    project_participates,
)
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.settings import get_settings_service
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.foundation.policy.board_programs import BoardProgram

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELLED)


async def _originate_roadmap(session: AsyncSession) -> TaskTable | None:
    from roboco.services.roadmap_engine import get_roadmap_engine

    return await get_roadmap_engine(session).run_cycle()


async def _originate_x_feature(session: AsyncSession) -> TaskTable | None:
    from roboco.services.x_engine import get_x_engine

    return await get_x_engine(session).open_feature_spotlight_exploration()


# Origination bindings live here, not in the pure foundation registry — one
# entry per PROGRAMS key, asserted by tests. Each program's ``source`` is
# separately asserted equal to the service-layer constant it duplicates
# (ROADMAP_SOURCE, X_FEATURE_EXPLORATION_SOURCE) so the two can't drift.
_ORIGINATORS: dict[str, Callable[[AsyncSession], Awaitable[TaskTable | None]]] = {
    "roadmap": _originate_roadmap,
    "x_feature": _originate_x_feature,
}


def _legacy_enabled(key: str) -> bool:
    """The pre-registry flag(s) each program aliases while both exist."""
    if key == "roadmap":
        return settings.roadmap_engine_enabled
    if key == "x_feature":
        return settings.x_engine_enabled and settings.x_feature_spotlight_enabled
    return False


def _interval_override(key: str) -> int | None:
    """Per-program configured cadence, when the operator has one set."""
    if key == "roadmap":
        return settings.roadmap_interval_seconds
    if key == "x_feature":
        return settings.x_feature_spotlight_interval_seconds
    return None


async def program_armed(session: AsyncSession, key: str) -> bool:
    """Whether program ``key`` is armed: the settings-store per-program
    override when a row exists, else the legacy boot flag(s) it aliases.

    THE single chokepoint every origination gate must route through —
    ``BoardProgramEngine.enabled`` (below), ``RoadmapEngine.run_cycle``,
    ``XEngine.open_feature_spotlight_exploration``, and — via
    ``BoardProgramEngine.open_program_cycle`` — the strategy-engine's idle
    trigger. Before this existed, ``run_cycle``/``open_feature_spotlight_
    exploration`` re-checked their OWN legacy flag internally instead of
    this resolver, so a settings-store-True + legacy-False combination (the
    exact state the shipped panel toggle produces) silently originated
    nothing forever.
    """
    return await get_settings_service(session).get_bool(
        f"board_program.{key}.enabled", _legacy_enabled(key)
    )


class BoardProgramEngine(BaseService):
    """Trigger/dedup/originate/LEARN over every registered Board Program."""

    service_name = "board_program_engine"

    async def enabled(self, key: str) -> bool:
        """Per-program settings-store override, else the legacy flag."""
        return await program_armed(self.session, key)

    async def run_due_programs(self) -> list[str]:
        """Originate a cycle for every enabled, due CRON program.

        Returns the keys that opened a new cycle. One program's failure is
        logged and never blocks the rest — mirrors the CI-watch sweep.
        """
        opened: list[str] = []
        now = datetime.now(UTC)
        for key, program in PROGRAMS.items():
            if program.trigger is not TriggerKind.CRON:
                continue
            try:
                if await self._run_due_one(key, now):
                    opened.append(key)
            except Exception:
                self.log.exception("board-program cycle failed", program=key)
        return opened

    async def _run_due_one(self, key: str, now: datetime) -> bool:
        if not await self.enabled(key):
            return False
        program = PROGRAMS[key]
        if not await self._scope_gate(program):
            return False
        blocked, last_opened_at = await self._dedup_state(key)
        if blocked:
            return False
        if not program_due(
            program,
            now=now,
            last_opened_at=last_opened_at,
            interval_override=_interval_override(key),
        ):
            return False
        return await self._originate_and_record(key) is not None

    async def open_program_cycle(self, key: str) -> TaskTable | None:
        """Originate a cycle for ``key`` off-schedule (enabled + dedup only,
        no cron-due check) — the strategy-engine trigger + "run now" seam."""
        if key not in PROGRAMS or not await self.enabled(key):
            return None
        program = PROGRAMS[key]
        if not await self._scope_gate(program):
            return None
        blocked, _ = await self._dedup_state(key)
        if blocked:
            return None
        return await self._originate_and_record(key)

    async def _scope_gate(self, program: BoardProgram) -> bool:
        """Project-scoped programs need at least one opted-in project before
        a cycle is worth opening; org-scoped programs have no run-side gate
        (their scoping is output-side only — see ``project_participates``)."""
        if program.scope != "project":
            return True
        if await self.opted_in_projects(program):
            return True
        self.log.info(
            "board-program: no project opted in, skipping cycle", program=program.key
        )
        return False

    async def opted_in_projects(self, program: BoardProgram) -> list[ProjectTable]:
        """Active projects where ``project_participates(program, ...)`` holds."""
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.is_active.is_(True))
        )
        return [
            p
            for p in result.scalars().all()
            if project_participates(program, p.board_programs)
        ]

    async def cycle_state(self, key: str) -> tuple[bool, datetime | None]:
        """(open_cycle, last_opened_at) — reconciled via the same auto-close
        dedup logic ``run_due_programs``/``open_program_cycle`` consult, so a
        reader (the API/panel) sees exactly what a "run now" call would."""
        return await self._dedup_state(key)

    async def record_decision(
        self,
        program_key: str,
        item_ref: str,
        verdict: str,
        reason: str | None = None,
        *,
        exploration_task_id: UUID | None = None,
    ) -> None:
        """Accrue one CEO approve/reject onto a cycle for this program.

        When ``exploration_task_id`` is given, targets the cycle row for
        THAT exploration task exactly (regardless of open/closed) — the
        caller holds the real originating task in hand (e.g. RoadmapService,
        reading the item off its own exploration task), so attribution stays
        exact even when a newer cycle has since opened for the same program
        (e.g. the CEO's decision lands after the original cycle auto-closed
        with undecided items — the admin-cancel edge — and a fresh cycle
        already opened in the meantime). Falls back to the most RECENT cycle
        (open or closed) when omitted or unresolved — x_post_service's X
        drafts don't carry their originating exploration task id, so this is
        the original, unchanged fallback for that caller. A best-effort
        no-op when no matching cycle exists.
        """
        cycle = None
        if exploration_task_id is not None:
            cycle = await self._cycle_for_exploration(program_key, exploration_task_id)
        if cycle is None:
            cycle = await self._latest_cycle(program_key)
        if cycle is None:
            return
        cycle.items_proposed += 1
        if verdict == "approved":
            cycle.items_approved += 1
        else:
            cycle.items_rejected += 1
        cycle.decisions = [
            *cycle.decisions,
            {"item_ref": item_ref, "verdict": verdict, "reason": reason},
        ]
        if cycle.closed_at is None:
            await self._maybe_close(cycle)
        await self.session.flush()

    async def prior_cycle_context(self, program_key: str, limit: int = 2) -> str:
        """Render the last ``limit`` CLOSED cycles for prompt injection, oldest
        first; empty string when none exist yet."""
        result = await self.session.execute(
            select(BoardProgramCycleTable)
            .where(
                BoardProgramCycleTable.program_key == program_key,
                BoardProgramCycleTable.closed_at.isnot(None),
            )
            .order_by(BoardProgramCycleTable.closed_at.desc())
            .limit(limit)
        )
        cycles = list(result.scalars().all())
        if not cycles:
            return ""
        return "\n".join(self._render_cycle(c) for c in reversed(cycles))

    def _render_cycle(self, cycle: BoardProgramCycleTable) -> str:
        line = f"proposed {cycle.items_proposed}, approved {cycle.items_approved}"
        rejected = [d for d in cycle.decisions if d.get("verdict") == "rejected"]
        reasons = "; ".join(
            f"{d.get('item_ref')} — {d.get('reason')}"
            for d in rejected
            if d.get("reason")
        )
        if reasons:
            line += f"; rejected: {reasons}"
        return line

    # ---- dedup / ledger plumbing --------------------------------------

    async def _dedup_state(self, key: str) -> tuple[bool, datetime | None]:
        """(blocked, last_opened_at) — blocked when a still-genuinely-open
        cycle row exists after an attempted auto-close."""
        latest = await self._latest_cycle(key)
        if latest is None:
            return False, None
        if latest.closed_at is None and not await self._maybe_close(latest):
            return True, latest.opened_at
        return False, latest.opened_at

    async def _maybe_close(self, cycle: BoardProgramCycleTable) -> bool:
        """Close ``cycle`` when its exploration task is terminal (or gone);
        returns whether it is now closed."""
        if cycle.exploration_task_id is None:
            cycle.closed_at = datetime.now(UTC)
            await self.session.flush()
            return True
        task = await get_task_service(self.session).get(
            cast("UUID", cycle.exploration_task_id)
        )
        if task is None or task.status in _TERMINAL_STATUSES:
            cycle.closed_at = datetime.now(UTC)
            await self.session.flush()
            return True
        return False

    async def _latest_cycle(self, key: str) -> BoardProgramCycleTable | None:
        result = await self.session.execute(
            select(BoardProgramCycleTable)
            .where(BoardProgramCycleTable.program_key == key)
            .order_by(BoardProgramCycleTable.opened_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _cycle_for_exploration(
        self, key: str, exploration_task_id: UUID
    ) -> BoardProgramCycleTable | None:
        """The cycle row that opened FOR this exact exploration task, or None
        — used by ``record_decision`` for exact attribution over the
        most-recent fallback."""
        result = await self.session.execute(
            select(BoardProgramCycleTable)
            .where(
                BoardProgramCycleTable.program_key == key,
                BoardProgramCycleTable.exploration_task_id == exploration_task_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _originate_and_record(self, key: str) -> TaskTable | None:
        task = await _ORIGINATORS[key](self.session)
        if task is None:
            return None
        self.session.add(
            BoardProgramCycleTable(
                program_key=key,
                exploration_task_id=task.id,
                opened_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return task


def get_board_program_engine(session: AsyncSession) -> BoardProgramEngine:
    """Construct a BoardProgramEngine bound to ``session``."""
    return BoardProgramEngine(session)
