"""roboco/foundation/policy/maintenance_pause.py

Operator maintenance pause: the pure shape. A pause DRAINS, it stops NEW
agent spawns / autonomous origination within one scope, but never touches a
container already running (that agent finishes its turn untouched). Three
scopes cover the CEO's stated complaint: the normal dev/delivery dispatch
tick, the Board Program registry (roadmap/pest-control/coroner/etc.), and
the originating engines (CI-watch, dep-update, docs-sync, release manager,
env-sync, X, video, self-heal). A new loop opts in by checking
``roboco.services.maintenance_pause.is_paused`` next to its own existing
``settings.xxx_enabled`` gate, with no central registry to edit. Foundation
purity: stdlib only, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

# Default + outer-bound pause duration, hours. The default keeps a forgotten
# pause from becoming a silent multi-day outage; the max is a sanity rail, not
# a product limit (resuming and re-pausing is one call away for longer work).
DEFAULT_PAUSE_HOURS = 4.0
MAX_PAUSE_HOURS = 24.0 * 14

_SETTING_KEY_PREFIX = "maintenance_pause."


class PauseScope(StrEnum):
    """The three independently-controllable pause switches."""

    DISPATCH = "dispatch"
    BOARD_PROGRAMS = "board_programs"
    ENGINES = "engines"


def setting_key(scope: PauseScope) -> str:
    """The ``system_settings`` row key backing ``scope``'s state."""
    return f"{_SETTING_KEY_PREFIX}{scope.value}"


@dataclass(frozen=True)
class PauseState:
    """One scope's live pause status.

    ``paused`` already resolves expiry: a stored pause past its
    ``expires_at`` reads back as ``paused=False`` (self-clearing on read, no
    background sweep required to lift it).
    """

    scope: PauseScope
    paused: bool
    paused_by: str | None = None
    paused_at: datetime | None = None
    reason: str | None = None
    expires_at: datetime | None = None


def resume_state(scope: PauseScope) -> PauseState:
    """The cleared (not-paused) state for ``scope``."""
    return PauseState(scope=scope, paused=False)


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _str_or_none(raw: Any) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def state_from_payload(
    scope: PauseScope, payload: dict[str, Any] | None, *, now: datetime
) -> PauseState:
    """Parse a stored JSON payload into a live ``PauseState``.

    A malformed or missing payload reads as not-paused (fail-open on READ,
    since a corrupt row must never wedge the fleet into a phantom pause the
    CEO can't see or clear). A payload past its own ``expires_at`` also reads
    as not-paused (auto-expiry, design point 4).
    """
    if not payload or not payload.get("paused"):
        return resume_state(scope)
    expires_at = _parse_dt(payload.get("expires_at"))
    if expires_at is not None and now >= expires_at:
        return resume_state(scope)
    return PauseState(
        scope=scope,
        paused=True,
        paused_by=_str_or_none(payload.get("paused_by")),
        paused_at=_parse_dt(payload.get("paused_at")),
        reason=_str_or_none(payload.get("reason")),
        expires_at=expires_at,
    )


def payload_from_state(state: PauseState) -> dict[str, Any]:
    """The JSON-serializable payload ``MaintenancePauseService`` persists."""
    if not state.paused:
        return {"paused": False}
    return {
        "paused": True,
        "paused_by": state.paused_by,
        "paused_at": state.paused_at.isoformat() if state.paused_at else None,
        "reason": state.reason,
        "expires_at": state.expires_at.isoformat() if state.expires_at else None,
    }


def validate_pause_payload(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` on a structurally invalid pause payload.

    The shape ``roboco.services.settings``'s writable-setting validator
    enforces before a write ever lands -- the ONE funnel every write to
    ``maintenance_pause.*`` goes through, including the pre-existing generic
    ``PUT /api/settings/{key}`` route, not just ``MaintenancePauseService.
    pause()``'s own Python-level bound. The ``expires_at``-past-``paused_at``
    and ``MAX_PAUSE_HOURS`` checks in ``_validate_pause_span`` below make
    that 14-day rail structural (hold regardless of which door the write
    comes through) instead of a convention only one caller happens to honor.
    A resumed (``paused=False``) payload needs nothing else.
    """
    if _validate_pause_envelope(payload):
        _validate_paused_fields(payload)


def _validate_pause_envelope(payload: dict[str, Any]) -> bool:
    """Validate the outer shape and return the boolean 'paused' flag."""
    if not isinstance(payload, dict):
        raise ValueError("pause payload must be a JSON object")
    paused = payload.get("paused")
    if not isinstance(paused, bool):
        raise ValueError("pause payload must carry a boolean 'paused'")
    return paused


def _validate_paused_fields(payload: dict[str, Any]) -> None:
    """Validate the fields a paused=True payload must carry: who paused it,
    when, until when (span-checked by ``_validate_pause_span``), and an
    optional reason."""
    paused_by = payload.get("paused_by")
    if not isinstance(paused_by, str) or not paused_by.strip():
        raise ValueError("a paused payload requires a non-empty 'paused_by'")
    paused_at = _parse_dt(payload.get("paused_at"))
    if paused_at is None:
        raise ValueError("a paused payload requires a valid ISO 'paused_at'")
    expires_at = _parse_dt(payload.get("expires_at"))
    if expires_at is None:
        raise ValueError("a paused payload requires a valid ISO 'expires_at'")
    _validate_pause_span(paused_at, expires_at)
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("'reason' must be a string when present")


def _validate_pause_span(paused_at: datetime, expires_at: datetime) -> None:
    """Enforce ``expires_at > paused_at`` and the ``MAX_PAUSE_HOURS`` bound --
    the one structural guarantee that a pause cannot be written permanent.

    The ``try/except TypeError`` turns a naive-versus-aware datetime mismatch
    (unorderable, raises on comparison) into a clean ``ValueError`` instead of
    leaking a confusing ``TypeError`` to the settings-write caller.
    """
    try:
        overdue = expires_at <= paused_at
        overlong = expires_at - paused_at > timedelta(hours=MAX_PAUSE_HOURS)
    except TypeError as exc:
        raise ValueError(
            "'paused_at' and 'expires_at' must both be timezone-aware or both naive"
        ) from exc
    if overdue:
        raise ValueError("'expires_at' must be after 'paused_at'")
    if overlong:
        raise ValueError(f"pause duration must not exceed {MAX_PAUSE_HOURS} hours")
