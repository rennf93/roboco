"""Operator maintenance pause: CRUD + the ``is_paused`` chokepoint.

State lives in the ``system_settings`` KV store, one JSON row per scope
(``maintenance_pause.{scope}``), the same settings-store pattern
``board_program.{key}.enabled`` uses, so no new table. ``is_paused`` is THE
chokepoint every gate call site (the stale-claim reaper, the delivery
dispatch tick, board-program origination, each originating engine) consults,
mirroring ``roboco.services.board_programs.program_armed``'s shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from roboco.foundation.policy.maintenance_pause import (
    DEFAULT_PAUSE_HOURS,
    MAX_PAUSE_HOURS,
    PauseScope,
    PauseState,
    payload_from_state,
    resume_state,
    setting_key,
    state_from_payload,
)
from roboco.services.base import BaseService
from roboco.services.settings import get_settings_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class MaintenancePauseError(ValueError):
    """Raised on an invalid pause request (bad hours, blank actor)."""


class MaintenancePauseService(BaseService):
    """CRUD over the three maintenance-pause scopes."""

    service_name = "maintenance_pause"

    async def get(self, scope: PauseScope) -> PauseState:
        """``scope``'s live state, expiry already resolved."""
        raw = await get_settings_service(self.session).get(setting_key(scope))
        payload = json.loads(raw) if raw else None
        return state_from_payload(scope, payload, now=datetime.now(UTC))

    async def all(self) -> dict[PauseScope, PauseState]:
        """Every scope's live state, backing the GET-all panel read."""
        return {scope: await self.get(scope) for scope in PauseScope}

    async def pause(
        self,
        scope: PauseScope,
        *,
        by: str,
        reason: str | None = None,
        hours: float = DEFAULT_PAUSE_HOURS,
    ) -> PauseState:
        """Pause ``scope``, stamping who/when/why + a self-clearing expiry.

        Caller commits (mirrors ``SettingsService.set``'s convention).
        """
        if not by or not by.strip():
            raise MaintenancePauseError("paused_by is required")
        if hours <= 0 or hours > MAX_PAUSE_HOURS:
            raise MaintenancePauseError(f"hours must be > 0 and <= {MAX_PAUSE_HOURS}")
        now = datetime.now(UTC)
        state = PauseState(
            scope=scope,
            paused=True,
            paused_by=by.strip(),
            paused_at=now,
            reason=(reason.strip() or None) if reason else None,
            expires_at=now + timedelta(hours=hours),
        )
        await get_settings_service(self.session).set(
            setting_key(scope), json.dumps(payload_from_state(state))
        )
        return state

    async def resume(self, scope: PauseScope) -> PauseState:
        """Explicit + idempotent: clears ``scope`` regardless of its current
        state (already-resumed, expired, or actively paused)."""
        state = resume_state(scope)
        await get_settings_service(self.session).set(
            setting_key(scope), json.dumps(payload_from_state(state))
        )
        return state


def get_maintenance_pause_service(session: AsyncSession) -> MaintenancePauseService:
    """Construct a MaintenancePauseService bound to ``session``."""
    return MaintenancePauseService(session)


# Per-scope timestamp of the most recent ``is_paused`` lookup failure, set
# on failure and cleared the moment a later lookup for that scope succeeds.
# In-memory only: the orchestrator's dispatch loop and the API process are
# the SAME process (see roboco/bootstrap.py -- one uvicorn worker, no
# multi-process fan-out), so this is a reliable same-process signal without
# a settings-store write on the very path that just failed to read one.
# Read by ``lookup_degraded_at`` so the CEO-facing GET /maintenance-pause
# route can flag a scope whose runtime gate is CURRENTLY reading fail-closed
# even while the persisted state (and this same route's own read) genuinely
# says not-paused -- otherwise a transient blip halts every dispatcher in
# that scope with zero operator-visible trace beyond an orchestrator log.
_degraded_since: dict[PauseScope, datetime] = {}


def lookup_degraded_at(scope: PauseScope) -> datetime | None:
    """When ``scope``'s most recent ``is_paused`` read failed closed, else
    None (its last attempt succeeded, or none has run yet this process)."""
    return _degraded_since.get(scope)


async def is_paused(session: AsyncSession, scope: PauseScope) -> bool:
    """Whether ``scope`` is paused right now, THE chokepoint every gate uses.

    Fails CLOSED (treats a lookup error as paused): this function is called
    from many independent origination/dispatch sites, several inside a core
    task-lifecycle transaction (e.g. Coroner's bounce hook) that must not be
    disrupted by a settings-store blip, so the error is swallowed here rather
    than left to each caller's own wrapping. A transient outage skipping one
    tick's work is the safe trade, the opposite failure (silently spawning
    /originating during a declared maintenance window because a read failed)
    is the one this feature exists to prevent.

    The read runs under its own SAVEPOINT (``begin_nested``): 11 of this
    function's 14 call sites pass a SHARED, reused session (several inside a
    core lifecycle transaction), so an unguarded caught exception here would
    otherwise leave that session poisoned -- the caller's very next statement
    raises ``PendingRollbackError`` and the surrounding transaction dies,
    strictly worse than the pause failing closed on its own. A savepoint
    rollback only expires attributes of ORM objects MUTATED inside it; this
    function only reads, so no caller object needs a post-failure refresh.
    """
    try:
        async with session.begin_nested():
            state = await MaintenancePauseService(session).get(scope)
    except Exception:
        logger.warning(
            "maintenance-pause lookup failed; treating scope as paused",
            scope=scope.value,
        )
        _degraded_since.setdefault(scope, datetime.now(UTC))
        return True
    _degraded_since.pop(scope, None)
    return state.paused
