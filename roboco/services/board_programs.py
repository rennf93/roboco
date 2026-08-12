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
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from sqlalchemy import func, select

from roboco.config import settings
from roboco.db.tables import (
    AuditLogTable,
    BoardProgramCycleTable,
    ProjectTable,
    TaskTable,
)
from roboco.foundation.policy.board_programs import (
    PROGRAMS,
    TriggerKind,
    program_due,
    project_participates,
)
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.settings import get_settings_service
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.foundation.policy.board_programs import BoardProgram

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELLED)


async def _originate_roadmap(session: AsyncSession) -> TaskTable | None:
    from roboco.services.roadmap_engine import get_roadmap_engine

    return await get_roadmap_engine(session).run_cycle()


async def _originate_x_feature(session: AsyncSession) -> TaskTable | None:
    from roboco.services.x_engine import get_x_engine

    return await get_x_engine(session).open_feature_spotlight_exploration()


async def _originate_pest_control(session: AsyncSession) -> TaskTable | None:
    from roboco.services.pest_control_engine import get_pest_control_engine

    return await get_pest_control_engine(session).run_cycle()


async def _originate_periscope(session: AsyncSession) -> TaskTable | None:
    from roboco.services.periscope_engine import get_periscope_engine

    return await get_periscope_engine(session).run_cycle()


async def _originate_scales(session: AsyncSession) -> TaskTable | None:
    from roboco.services.scales_engine import get_scales_engine

    return await get_scales_engine(session).run_cycle()


async def _originate_coroner(_session: AsyncSession) -> TaskTable | None:
    """Coroner is EVENT-triggered (spec §4) — ``run_due_programs`` skips every
    non-CRON program before it would ever call this (see ``program_due``), so
    this always-None stub only exists to keep ``_ORIGINATORS`` covering the
    registry 1:1 (asserted by tests). A real cycle opens through
    ``CoronerEngine.open_for_incident``, called directly from the bounce/
    cancel/budget-block hooks — it bypasses this dict entirely, building its
    own ``BoardProgramCycleTable`` row the same way ``_originate_and_record``
    does below, since there is no loop tick to route it through."""
    return None


async def _originate_sentinel(session: AsyncSession) -> TaskTable | None:
    from roboco.services.sentinel_engine import get_sentinel_engine

    return await get_sentinel_engine(session).run_cycle()


async def _originate_spackle(session: AsyncSession) -> TaskTable | None:
    from roboco.services.spackle_engine import get_spackle_engine

    return await get_spackle_engine(session).run_cycle()


async def _originate_mirror(session: AsyncSession) -> TaskTable | None:
    from roboco.services.mirror_engine import get_mirror_engine

    return await get_mirror_engine(session).run_cycle()


async def _originate_megaphone(session: AsyncSession) -> TaskTable | None:
    from roboco.services.megaphone_engine import get_megaphone_engine

    return await get_megaphone_engine(session).run_cycle()


async def _originate_librarian(session: AsyncSession) -> TaskTable | None:
    from roboco.services.librarian_engine import get_librarian_engine

    return await get_librarian_engine(session).run_cycle()


async def _originate_war_room(session: AsyncSession) -> TaskTable | None:
    """War Room's ``_ORIGINATORS`` binding — a REAL originator, unlike
    ``_originate_coroner``'s always-None stub. War Room is EVENT-triggered
    same as Coroner (``run_due_programs`` never calls this — see the
    trigger-kind guard there), but its "run now" / CEO on-demand path
    (``BoardProgramEngine.open_program_cycle``, which does NOT check trigger
    kind) genuinely drives a fresh blank-brief cycle through
    ``WarRoomEngine.run_cycle``. The release-publish hook bypasses this dict
    entirely via ``WarRoomEngine.open_for_release`` (mirrors
    ``CoronerEngine.open_for_incident``)."""
    from roboco.services.war_room_engine import get_war_room_engine

    return await get_war_room_engine(session).run_cycle()


async def _originate_barfly(session: AsyncSession) -> TaskTable | None:
    from roboco.services.barfly_engine import get_barfly_engine

    return await get_barfly_engine(session).run_cycle()


async def _originate_dogfood(session: AsyncSession) -> TaskTable | None:
    """Unlike Coroner's never-firing stub, this is a REAL originator: a
    Dogfood cycle needs no external incident id, just the next opted-in
    project in rotation, so both the release-publish hook and a CEO "run
    now" call open a cycle through the ordinary ``open_program_cycle`` path.
    The cron loop still never reaches this — ``program_due`` refuses any
    non-CRON trigger before ``_run_due_one`` would call it — see
    ``roboco.services.dogfood_engine``'s module docstring."""
    from roboco.services.dogfood_engine import get_dogfood_engine

    return await get_dogfood_engine(session).run_cycle()


# Origination bindings live here, not in the pure foundation registry — one
# entry per PROGRAMS key, asserted by tests. Each program's ``source`` is
# separately asserted equal to the service-layer constant it duplicates
# (ROADMAP_SOURCE, X_FEATURE_EXPLORATION_SOURCE, PEST_CONTROL_SOURCE,
# PERISCOPE_SOURCE, CORONER_SOURCE, SENTINEL_SOURCE, SPACKLE_SOURCE,
# SCALES_SOURCE, MIRROR_SOURCE, MEGAPHONE_SOURCE, LIBRARIAN_SOURCE,
# WAR_ROOM_SOURCE, BARFLY_SOURCE, DOGFOOD_SOURCE) so the two can't drift.
_ORIGINATORS: dict[str, Callable[[AsyncSession], Awaitable[TaskTable | None]]] = {
    "roadmap": _originate_roadmap,
    "x_feature": _originate_x_feature,
    "pest_control": _originate_pest_control,
    "periscope": _originate_periscope,
    "coroner": _originate_coroner,
    "sentinel": _originate_sentinel,
    "spackle": _originate_spackle,
    "scales": _originate_scales,
    "mirror": _originate_mirror,
    "megaphone": _originate_megaphone,
    "librarian": _originate_librarian,
    "war_room": _originate_war_room,
    "barfly": _originate_barfly,
    "dogfood": _originate_dogfood,
}


# Sentinel "last explored" timestamp for a project a rotation has never
# targeted — older than any real ``opened_at``, so it always sorts first.
_NEVER_EXPLORED = datetime.min.replace(tzinfo=UTC)


async def pick_rotation_target(
    session: AsyncSession, projects: list[ProjectTable], *, source: str
) -> ProjectTable:
    """The opted-in project due this cycle for a project-scoped program's
    round-robin: never-explored beats explored, else the oldest
    last-explored timestamp wins — ties (including every never-explored
    project) break by ``projects``' own deterministic order
    (``BoardProgramEngine.opted_in_projects``' ORDER BY). ``source`` is the
    program's own exploration-task source tag (e.g. ``PEST_CONTROL_SOURCE``),
    read via ``_last_explored_at`` rather than the LEARN ledger — see that
    function's docstring. Shared by every project-scoped program's rotation
    (Pest Control, Spackle, Mirror, Dogfood) so they rotate identically."""
    if len(projects) == 1:
        return projects[0]
    last_explored = await _last_explored_at(session, source)
    return min(
        projects,
        key=lambda p: (
            p.id in last_explored,
            last_explored.get(cast("UUID", p.id), _NEVER_EXPLORED),
        ),
    )


async def _last_explored_at(session: AsyncSession, source: str) -> dict[UUID, datetime]:
    """Most recent exploration task's ``created_at`` per project id, for a
    given task ``source`` — a project-scoped rotation's memory of which
    opted-in project went last. Reads the exploration tasks themselves
    rather than the LEARN ledger (``board_program_cycles``): that ledger is
    only populated by a ``BoardProgramEngine``-mediated call
    (``open_program_cycle`` / ``run_due_programs``), so keying off it would
    leave the rotation blind whenever an engine's own ``run_cycle`` runs
    directly. Every prior cycle is guaranteed terminal by the time this
    runs — the one-open-cycle dedup in each engine's ``run_cycle`` already
    refused a new cycle while any project's exploration task was still
    open."""
    result = await session.execute(
        select(TaskTable.project_id, func.max(TaskTable.created_at))
        .where(TaskTable.source == source)
        .group_by(TaskTable.project_id)
    )
    return {pid: created for pid, created in result.all() if pid is not None}


async def _pest_control_rework_spike(session: AsyncSession) -> bool:
    """True when the trailing-7-day rework rate crosses
    ``settings.pest_rework_threshold`` — the "metric" half of Pest Control's
    "weekly cron OR rework-rate spike" trigger (spec §4). Mirrors the
    strategy-engine idle-trigger pattern rather than a new TriggerKind: the
    program's own trigger stays CRON, and this predicate lets a cycle open
    off-schedule on top of that cadence."""
    from roboco.services.metrics import get_metrics_service

    report = await get_metrics_service(session).get_rework_metrics(days=7)
    return report.rate > settings.pest_rework_threshold


# Metric-predicate bindings, mirroring ``_ORIGINATORS`` — one entry per
# program that wants an off-schedule accelerator on top of its CRON cadence.
# Absent from this dict = cron-only (every program but pest_control today).
_METRIC_PREDICATES: dict[str, Callable[[AsyncSession], Awaitable[bool]]] = {
    "pest_control": _pest_control_rework_spike,
}


def learn_ref(item: dict[str, Any], limit: int = 80) -> str:
    """The ``item_ref`` a per-item approve/reject records for LEARN.

    The item's own title, because the ref's only consumer is
    ``_render_cycle``, whose output goes into the NEXT cycle's exploration
    prompt. Passing the stored ``id`` instead (``item-0``/``item-1``, a
    per-cycle index — see ``_normalize_roadmap_item``) rendered
    "rejected: item-1 — <reason>": the CEO's reason survived, but nothing
    said which proposal it was about, and the index means something
    different in every cycle. Falls back to the id when a title is missing.

    ``target_task_title`` covers Scales, whose items name the live task they
    mutate rather than carrying a draft title of their own.
    """
    title = str(item.get("title") or item.get("target_task_title") or "").strip()
    ref = title or str(item.get("id") or "")
    return ref[:limit].rstrip() if len(ref) > limit else ref


# ---------------------------------------------------------------------------
# Item-payload snapshotting (gap: TaskService.delete hard-deletes the
# exploration task the full per-item payload lives on; board_program_cycles
# FK is ondelete=SET NULL, so a decision survives with nothing behind it).
#
# ``record_decision`` now stamps a BOUNDED snapshot onto each decision entry
# at decision time, while the payload is still definitely alive, so the
# history stands alone after the task is gone. Two sources feed it:
#  - an explicit ``item_payload`` the caller already has in hand (x_post_
#    service's materialized X drafts, PlaybookService's playbook rows: the
#    item IS the object being decided, not an entry in a task's marker list);
#  - for the "queue" programs (roadmap/pest_control/spackle/mirror/dogfood/
#    scales/sentinel/periscope/coroner), auto-resolved from the exploration
#    task's own marker payload via ``_QUEUE_ITEM_SHAPES`` below. Every one
#    of those callers already passes ``exploration_task_id``, so no caller
#    file needs to change to get this.
# ---------------------------------------------------------------------------

_SNAPSHOT_TEXT_CAP = 400
_SNAPSHOT_AC_CAP = 10
_SNAPSHOT_AC_ITEM_CAP = 200
_SNAPSHOT_FIELDS = (
    "title",
    "evidence",
    "description",
    "status",
    "reject_reason",
    "materialized_task_id",
)


def _cap_item_payload(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bound an item payload to a fixed, known field set before it lands in
    the jsonb ``decisions`` column: this is a snapshot, not an archive."""
    if not raw:
        return None
    out: dict[str, Any] = {}
    for key in _SNAPSHOT_FIELDS:
        val = raw.get(key)
        if val:
            out[key] = str(val)[:_SNAPSHOT_TEXT_CAP]
    ac = raw.get("acceptance_criteria")
    if isinstance(ac, list) and ac:
        out["acceptance_criteria"] = [
            str(a)[:_SNAPSHOT_AC_ITEM_CAP] for a in ac[:_SNAPSHOT_AC_CAP]
        ]
    return out or None


class _QueueItemShape(NamedTuple):
    """How to find + read one item out of a "queue" program's exploration-
    task marker payload. ``list_key`` is None for Coroner, whose payload
    carries a single ``process_change`` dict rather than a list."""

    getter: Any  # Callable[[TaskTable], dict[str, Any] | None]
    list_key: str | None
    title_field: str
    evidence_field: str | None
    description_field: str | None


# program_key -> shape, one entry per queue-style program (the four X-backed
# programs and Librarian carry their item on a different object entirely and
# reach record_decision through the explicit ``item_payload`` path instead).
_QUEUE_ITEM_SHAPES: dict[str, _QueueItemShape] = {
    "roadmap": _QueueItemShape(
        markers.get_roadmap_cycle, "items", "title", "rationale", "description"
    ),
    "pest_control": _QueueItemShape(
        markers.get_pest_hunt, "items", "title", "evidence", "description"
    ),
    "spackle": _QueueItemShape(
        markers.get_gap_fill, "items", "title", "evidence", "description"
    ),
    "mirror": _QueueItemShape(
        markers.get_messaging_fixes, "items", "title", "evidence", "description"
    ),
    "dogfood": _QueueItemShape(
        markers.get_friction_fixes, "items", "title", "evidence", "description"
    ),
    "scales": _QueueItemShape(
        markers.get_rebalance_plan, "items", "target_task_title", "rationale", None
    ),
    "sentinel": _QueueItemShape(
        markers.get_quality_report,
        "items",
        "suggested_action",
        "evidence",
        "observation",
    ),
    "periscope": _QueueItemShape(
        markers.get_market_brief, "findings", "claim", "source_url", "relevance"
    ),
    "coroner": _QueueItemShape(
        markers.get_coroner_postmortem, None, "description", None, None
    ),
}


def _queue_item_candidates(
    stored: dict[str, Any], shape: _QueueItemShape
) -> list[dict[str, Any]]:
    """The item dicts on a queue-shaped marker payload worth matching
    against: a coroner-style singleton's ``process_change``, else the
    program's own ``list_key`` list."""
    if shape.list_key is None:
        pc = stored.get("process_change")
        return [pc] if isinstance(pc, dict) else []
    return [it for it in stored.get(shape.list_key, []) if isinstance(it, dict)]


def _snapshot_from_item(
    item: dict[str, Any], shape: _QueueItemShape
) -> dict[str, Any] | None:
    """Build + cap the snapshot dict for one matched item, per ``shape``'s
    field-name mapping."""
    raw = {
        "title": item.get(shape.title_field),
        "evidence": item.get(shape.evidence_field) if shape.evidence_field else None,
        "description": item.get(shape.description_field)
        if shape.description_field
        else None,
        "acceptance_criteria": item.get("acceptance_criteria"),
        "status": item.get("status"),
        "reject_reason": item.get("reject_reason"),
        "materialized_task_id": item.get("materialized_task_id")
        or item.get("target_task_id"),
    }
    return _cap_item_payload(raw)


def _resolve_queue_item_snapshot(
    task: TaskTable, program_key: str, item_ref: str
) -> dict[str, Any] | None:
    """Find the item on ``task``'s marker payload whose recomputed ref
    matches ``item_ref`` and return its bounded snapshot, or None when the
    program isn't queue-shaped, the marker is missing, or no item matches
    (e.g. an item authored before this feature shipped, or the caller's own
    ref computation used a field this resolver doesn't know about)."""
    shape = _QUEUE_ITEM_SHAPES.get(program_key)
    if shape is None:
        return None
    stored = shape.getter(task)
    if not isinstance(stored, dict):
        return None
    for item in _queue_item_candidates(stored, shape):
        if learn_ref({"title": item.get(shape.title_field)}) == item_ref:
            return _snapshot_from_item(item, shape)
    return None


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
        """Originate a cycle for every enabled, due CRON program, PLUS every
        program whose metric predicate (``_METRIC_PREDICATES``) fires this
        tick even off-schedule.

        Returns the keys that opened a new cycle. One program's failure — cron
        or metric — is logged and never blocks the rest, mirrors the CI-watch
        sweep. A metric hit for a program already opened by the cron pass is
        a cheap no-op (``open_program_cycle`` re-checks dedup itself).
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
        await self._run_due_metric_predicates(opened)
        return opened

    async def _run_due_metric_predicates(self, opened: list[str]) -> None:
        """Append to ``opened`` any program whose metric predicate fires this
        tick, off-schedule — split out of ``run_due_programs`` to keep its
        complexity down (xenon budget).

        Cheap gates first: scope (any project opted in?) and dedup (already
        an open cycle?) are checked BEFORE the predicate runs, so a predicate
        that costs several queries (e.g. the rework-rate check's 8-11 query
        ``MetricsService`` call) is never evaluated on a tick that would have
        been rejected anyway — a spike-prone metric shouldn't pay full price
        every tick just to be discarded by a guard it was always going to
        fail.
        """
        for key, predicate in _METRIC_PREDICATES.items():
            if key in opened or not await self.enabled(key):
                continue
            program = PROGRAMS[key]
            try:
                if not await self._scope_gate(program):
                    continue
                blocked, _ = await self._dedup_state(key)
                if blocked:
                    continue
                if await predicate(self.session) and (
                    await self.open_program_cycle(key) is not None
                ):
                    opened.append(key)
            except Exception:
                self.log.exception("board-program metric predicate failed", program=key)

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
        """Active projects where ``project_participates(program, ...)`` holds,
        deterministically ordered (created_at, then id) — callers that pick
        a single project out of this list (e.g. ``PestControlEngine``'s
        rotation) need a stable order, not whatever Postgres happens to
        return without an ORDER BY."""
        result = await self.session.execute(
            select(ProjectTable)
            .where(ProjectTable.is_active.is_(True))
            .order_by(ProjectTable.created_at, ProjectTable.id)
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

    async def _resolve_decision_cycle(
        self, program_key: str, exploration_task_id: UUID | None
    ) -> BoardProgramCycleTable | None:
        """The exact cycle for ``exploration_task_id`` when it resolves,
        else the most-recent cycle for ``program_key`` (or None); see
        ``record_decision``'s docstring for the attribution rationale."""
        if exploration_task_id is not None:
            cycle = await self._cycle_for_exploration(program_key, exploration_task_id)
            if cycle is not None:
                return cycle
        return await self._latest_cycle(program_key)

    async def _resolve_decision_snapshot(
        self,
        cycle: BoardProgramCycleTable,
        program_key: str,
        item_ref: str,
        item_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """An explicit ``item_payload`` wins (capped); otherwise a queue-
        shaped program auto-resolves its snapshot from the exploration
        task's own marker payload; see ``record_decision``'s docstring."""
        snapshot = _cap_item_payload(item_payload)
        if snapshot is not None or cycle.exploration_task_id is None:
            return snapshot
        task = await get_task_service(self.session).get(
            cast("UUID", cycle.exploration_task_id)
        )
        if task is None:
            return None
        return _resolve_queue_item_snapshot(task, program_key, item_ref)

    async def record_decision(
        self,
        program_key: str,
        item_ref: str,
        verdict: str,
        reason: str | None = None,
        *,
        exploration_task_id: UUID | None = None,
        item_payload: dict[str, Any] | None = None,
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

        ``item_payload``, when given, is stamped (bounded) onto the decision
        entry as ``item_snapshot``: the caller already holds the full item
        (an X draft task, a playbook row) in hand. When omitted, a queue-
        shaped program (roadmap/pest_control/spackle/mirror/dogfood/scales/
        sentinel/periscope/coroner) auto-resolves its own snapshot from the
        exploration task's marker payload (see ``_resolve_queue_item_
        snapshot``), which is what makes the cycle history stand alone once
        ``TaskService.delete`` removes the exploration task the payload used
        to live exclusively on.
        """
        cycle = await self._resolve_decision_cycle(program_key, exploration_task_id)
        if cycle is None:
            return
        snapshot = await self._resolve_decision_snapshot(
            cycle, program_key, item_ref, item_payload
        )
        # Savepoint: every one of this method's callers (the per-program
        # `_record_learn` family) wraps this call in its own best-effort
        # try/except with no rollback — a mid-flush failure here would
        # otherwise poison the caller's shared session (e.g. RoadmapService.
        # approve_item does an UNGUARDED session.flush() right after this
        # returns). Fixed once at the source instead of in every caller.
        async with self.session.begin_nested():
            cycle.items_proposed += 1
            if verdict == "approved":
                cycle.items_approved += 1
            else:
                cycle.items_rejected += 1
            entry: dict[str, Any] = {
                "item_ref": item_ref,
                "verdict": verdict,
                "reason": reason,
            }
            if snapshot:
                entry["item_snapshot"] = snapshot
            cycle.decisions = [*cycle.decisions, entry]
            self.session.add(
                AuditLogTable(
                    event_type="board_program.decision",
                    target_type="board_program_cycle",
                    target_id=cycle.id,
                    severity="info",
                    details={
                        "program_key": program_key,
                        "item_ref": item_ref[:200],
                        "verdict": verdict,
                        "reason": (reason or "")[:300],
                    },
                )
            )
            if cycle.closed_at is None:
                await self._maybe_close(cycle)
            await self.session.flush()

    async def record_nothing_to_propose(
        self, program_key: str, exploration_task_id: UUID, reason: str
    ) -> None:
        """Record an explorer's "genuinely nothing worth proposing this
        cycle" verdict onto the cycle row for THIS exploration task, so the
        next cycle's LEARN context explains a proposed-0 cycle instead of
        rendering a bare "proposed 0, approved 0" (see ``_render_cycle``).

        Unlike ``record_decision`` this never touches items_proposed/
        approved/rejected or ``decisions`` — no item was proposed. Does NOT
        close the row; that stays ``_maybe_close``'s job once the (already
        COMPLETED, by the time this runs) exploration task is observed
        terminal. A best-effort no-op when no cycle row matches — mirrors
        every ``record_decision`` producer's own best-effort wrapping.
        """
        cycle = await self._cycle_for_exploration(program_key, exploration_task_id)
        if cycle is None:
            return
        # Savepoint: same reasoning as record_decision above — every caller
        # here also wraps this in its own best-effort try/except.
        async with self.session.begin_nested():
            cycle.nothing_to_propose_reason = reason
            await self.session.flush()

    async def list_cycles(
        self, program_key: str, *, limit: int = 20
    ) -> list[BoardProgramCycleTable]:
        """Every recorded cycle for ``program_key``, newest-opened-first,
        capped 1-100: the durable LEARN-history read surface (``GET
        /board-programs/{key}/cycles``). Each cycle's ``decisions`` carries
        its own bounded ``item_snapshot`` where ``record_decision`` resolved
        one, so this reads back complete even once the exploration task
        behind an old cycle has been deleted."""
        result = await self.session.execute(
            select(BoardProgramCycleTable)
            .where(BoardProgramCycleTable.program_key == program_key)
            .order_by(BoardProgramCycleTable.opened_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())

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
        if cycle.items_proposed == 0 and cycle.nothing_to_propose_reason:
            return f"proposed 0 — nothing to propose: {cycle.nothing_to_propose_reason}"
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
        """The shared origination chokepoint every registered program's cron
        (``_run_due_one``) AND off-schedule (``open_program_cycle``: CEO
        run-now, a metric-predicate accelerator, the strategy-engine idle
        trigger, the Dogfood release-publish hook) path funnels through, so
        one ``board_programs``-scope maintenance-pause check here covers all
        of them at once. Coroner and War Room's own EVENT-hook entry points
        (``open_for_incident`` / ``open_for_release``) bypass this function
        entirely and carry their own identical check."""
        from roboco.services.maintenance_pause import PauseScope, is_paused

        if await is_paused(self.session, PauseScope.BOARD_PROGRAMS):
            return None
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
