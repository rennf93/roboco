"""MaintenancePauseService CRUD + the ``is_paused`` chokepoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import roboco.services.maintenance_pause as mp_module
from roboco.foundation.policy.maintenance_pause import DEFAULT_PAUSE_HOURS, PauseScope
from roboco.services.maintenance_pause import (
    MaintenancePauseError,
    get_maintenance_pause_service,
    is_paused,
    lookup_degraded_at,
)
from sqlalchemy import text


@pytest.mark.asyncio
async def test_get_unset_scope_is_not_paused(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    state = await svc.get(PauseScope.DISPATCH)
    assert state.paused is False


@pytest.mark.asyncio
async def test_pause_then_get_roundtrips(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    await svc.pause(PauseScope.DISPATCH, by="ceo", reason="NAS reboot", hours=2)

    state = await svc.get(PauseScope.DISPATCH)
    assert state.paused is True
    assert state.paused_by == "ceo"
    assert state.reason == "NAS reboot"
    assert state.expires_at is not None
    assert state.paused_at is not None


@pytest.mark.asyncio
async def test_pause_defaults_hours_when_omitted(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    state = await svc.pause(PauseScope.ENGINES, by="ceo")
    assert state.expires_at is not None
    assert state.paused_at is not None
    delta_hours = (state.expires_at - state.paused_at).total_seconds() / 3600
    assert delta_hours == pytest.approx(DEFAULT_PAUSE_HOURS, abs=0.01)


@pytest.mark.asyncio
async def test_pause_rejects_blank_actor(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    with pytest.raises(MaintenancePauseError):
        await svc.pause(PauseScope.DISPATCH, by="   ")


@pytest.mark.asyncio
async def test_pause_rejects_non_positive_hours(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    with pytest.raises(MaintenancePauseError):
        await svc.pause(PauseScope.DISPATCH, by="ceo", hours=0)


@pytest.mark.asyncio
async def test_pause_rejects_excessive_hours(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    with pytest.raises(MaintenancePauseError):
        await svc.pause(PauseScope.DISPATCH, by="ceo", hours=100_000)


@pytest.mark.asyncio
async def test_resume_clears_an_active_pause(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    await svc.pause(PauseScope.BOARD_PROGRAMS, by="ceo", hours=1)
    assert (await svc.get(PauseScope.BOARD_PROGRAMS)).paused is True

    state = await svc.resume(PauseScope.BOARD_PROGRAMS)
    assert state.paused is False
    assert (await svc.get(PauseScope.BOARD_PROGRAMS)).paused is False


@pytest.mark.asyncio
async def test_resume_is_idempotent_on_an_already_clear_scope(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    state = await svc.resume(PauseScope.ENGINES)  # never paused
    assert state.paused is False
    state_again = await svc.resume(PauseScope.ENGINES)
    assert state_again.paused is False


@pytest.mark.asyncio
async def test_scopes_are_independent(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    await svc.pause(PauseScope.BOARD_PROGRAMS, by="ceo", hours=1)

    assert (await svc.get(PauseScope.BOARD_PROGRAMS)).paused is True
    assert (await svc.get(PauseScope.DISPATCH)).paused is False
    assert (await svc.get(PauseScope.ENGINES)).paused is False


@pytest.mark.asyncio
async def test_all_returns_every_scope(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    await svc.pause(PauseScope.ENGINES, by="ceo", hours=1)
    states = await svc.all()
    assert set(states) == set(PauseScope)
    assert states[PauseScope.ENGINES].paused is True
    assert states[PauseScope.DISPATCH].paused is False


@pytest.mark.asyncio
async def test_is_paused_true_when_scope_paused(db_session: Any) -> None:
    svc = get_maintenance_pause_service(db_session)
    await svc.pause(PauseScope.DISPATCH, by="ceo", hours=1)
    assert await is_paused(db_session, PauseScope.DISPATCH) is True


@pytest.mark.asyncio
async def test_is_paused_false_when_scope_unset(db_session: Any) -> None:
    assert await is_paused(db_session, PauseScope.DISPATCH) is False


@pytest.mark.asyncio
async def test_is_paused_fails_closed_on_lookup_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings-store read error must never silently allow autonomous
    work to proceed during a declared maintenance window: fails CLOSED."""
    broken = AsyncMock(side_effect=RuntimeError("db blip"))
    monkeypatch.setattr(mp_module.MaintenancePauseService, "get", broken, raising=True)
    assert await is_paused(db_session, PauseScope.DISPATCH) is True


@pytest.mark.asyncio
async def test_is_paused_lookup_error_does_not_poison_shared_session(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT 2: is_paused must fail closed WITHOUT leaving a shared, reused
    session poisoned. Reverting the savepoint wrap makes this fail with
    PendingRollbackError on the post-call query below -- 11 of is_paused's
    14 call sites pass a shared session (several inside a core lifecycle
    transaction), not a throwaway loop-local one, so a swallowed real DB
    error here must not doom the caller's next statement."""

    async def _broken_get(self: Any, _scope: PauseScope) -> Any:
        # A genuine DB-level failure (not just a raised Python exception) --
        # this is what actually poisons an unguarded async session's
        # transaction until it is rolled back.
        await self.session.execute(text("SELECT 1/0"))
        raise AssertionError("unreachable: the divide-by-zero above must raise")

    monkeypatch.setattr(mp_module.MaintenancePauseService, "get", _broken_get)

    assert await is_paused(db_session, PauseScope.DISPATCH) is True

    # The caller's next statement on the SAME session must still work.
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_lookup_degraded_at_tracks_and_clears_across_calls(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT 3: a fail-closed decision must become observable (here, via
    the in-process tracker the GET route reads) and must self-clear the
    moment a later lookup succeeds again -- both branches."""
    # The tracker is a module-level dict shared across this file's tests
    # (several exercise PauseScope.DISPATCH lookup failures too); start
    # from a clean slate so this test's own assertions don't depend on
    # what ran before it.
    mp_module._degraded_since.pop(PauseScope.DISPATCH, None)
    assert lookup_degraded_at(PauseScope.DISPATCH) is None

    broken = AsyncMock(side_effect=RuntimeError("db blip"))
    monkeypatch.setattr(mp_module.MaintenancePauseService, "get", broken, raising=True)
    assert await is_paused(db_session, PauseScope.DISPATCH) is True
    assert lookup_degraded_at(PauseScope.DISPATCH) is not None

    monkeypatch.undo()
    assert await is_paused(db_session, PauseScope.DISPATCH) is False
    assert lookup_degraded_at(PauseScope.DISPATCH) is None
