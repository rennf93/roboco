"""tests/unit/foundation/test_maintenance_pause.py

Pure-shape coverage for the operator maintenance pause: payload parsing,
auto-expiry resolution, and the structural validator the settings-store
writer enforces before a write lands.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from roboco.foundation.policy.maintenance_pause import (
    MAX_PAUSE_HOURS,
    PauseScope,
    PauseState,
    payload_from_state,
    resume_state,
    setting_key,
    state_from_payload,
    validate_pause_payload,
)


def test_setting_key_is_namespaced_per_scope() -> None:
    assert setting_key(PauseScope.DISPATCH) == "maintenance_pause.dispatch"
    assert setting_key(PauseScope.BOARD_PROGRAMS) == "maintenance_pause.board_programs"
    assert setting_key(PauseScope.ENGINES) == "maintenance_pause.engines"


def test_resume_state_is_not_paused() -> None:
    state = resume_state(PauseScope.DISPATCH)
    assert state.paused is False
    assert state.paused_by is None


def test_state_from_payload_none_reads_not_paused() -> None:
    now = datetime.now(UTC)
    state = state_from_payload(PauseScope.ENGINES, None, now=now)
    assert state.paused is False


def test_state_from_payload_malformed_fails_open_not_paused() -> None:
    """A corrupt/legacy row must never wedge the fleet into a phantom pause
    the CEO can't see or clear: reads as not-paused."""
    now = datetime.now(UTC)
    state = state_from_payload(PauseScope.ENGINES, {"garbage": True}, now=now)
    assert state.paused is False


def test_state_from_payload_active_pause_round_trips() -> None:
    now = datetime.now(UTC)
    payload = {
        "paused": True,
        "paused_by": "ceo",
        "paused_at": now.isoformat(),
        "reason": "NAS maintenance",
        "expires_at": (now + timedelta(hours=4)).isoformat(),
    }
    state = state_from_payload(PauseScope.DISPATCH, payload, now=now)
    assert state.paused is True
    assert state.paused_by == "ceo"
    assert state.reason == "NAS maintenance"
    assert state.expires_at is not None


def test_state_from_payload_past_expiry_self_clears() -> None:
    """Design point 4: past expiry the pause lifts by itself on read."""
    now = datetime.now(UTC)
    payload = {
        "paused": True,
        "paused_by": "ceo",
        "paused_at": (now - timedelta(hours=5)).isoformat(),
        "reason": None,
        "expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    state = state_from_payload(PauseScope.DISPATCH, payload, now=now)
    assert state.paused is False


def test_payload_from_state_round_trips_through_state_from_payload() -> None:
    now = datetime.now(UTC)
    original = PauseState(
        scope=PauseScope.BOARD_PROGRAMS,
        paused=True,
        paused_by="ceo",
        paused_at=now,
        reason="quarterly maintenance",
        expires_at=now + timedelta(hours=2),
    )
    payload = payload_from_state(original)
    restored = state_from_payload(PauseScope.BOARD_PROGRAMS, payload, now=now)
    assert restored.paused is True
    assert restored.paused_by == original.paused_by
    assert restored.reason == original.reason


def test_payload_from_state_resumed_is_minimal() -> None:
    assert payload_from_state(resume_state(PauseScope.ENGINES)) == {"paused": False}


def test_validate_pause_payload_accepts_resumed() -> None:
    validate_pause_payload({"paused": False})  # no raise


def test_validate_pause_payload_accepts_full_paused_payload() -> None:
    now = datetime.now(UTC)
    validate_pause_payload(
        {
            "paused": True,
            "paused_by": "ceo",
            "paused_at": now.isoformat(),
            "reason": "maintenance",
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
    )  # no raise


def test_validate_pause_payload_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_pause_payload(cast("Any", "not a dict"))


def test_validate_pause_payload_rejects_missing_paused_bool() -> None:
    with pytest.raises(ValueError, match="boolean 'paused'"):
        validate_pause_payload({})


def test_validate_pause_payload_rejects_paused_without_paused_by() -> None:
    with pytest.raises(ValueError, match="paused_by"):
        validate_pause_payload(
            {
                "paused": True,
                "paused_at": datetime.now(UTC).isoformat(),
                "expires_at": datetime.now(UTC).isoformat(),
            }
        )


def test_validate_pause_payload_rejects_paused_without_expires_at() -> None:
    with pytest.raises(ValueError, match="expires_at"):
        validate_pause_payload(
            {
                "paused": True,
                "paused_by": "ceo",
                "paused_at": datetime.now(UTC).isoformat(),
            }
        )


def test_validate_pause_payload_rejects_non_string_reason() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="reason"):
        validate_pause_payload(
            {
                "paused": True,
                "paused_by": "ceo",
                "paused_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "reason": 12345,
            }
        )


def test_validate_pause_payload_rejects_expires_at_not_after_paused_at() -> None:
    """DEFECT 4: the 14-day rail depends on a real ``paused_at``..``expires_at``
    span; an equal or earlier ``expires_at`` (already-expired-at-issue) must
    be rejected structurally, not just by ``MaintenancePauseService.pause()``'s
    own Python bound."""
    now = datetime.now(UTC).isoformat()
    with pytest.raises(ValueError, match="after"):
        validate_pause_payload(
            {
                "paused": True,
                "paused_by": "ceo",
                "paused_at": now,
                "expires_at": now,
            }
        )


def test_validate_pause_payload_rejects_duration_beyond_max_hours() -> None:
    """DEFECT 4: the shared validator, not just ``MaintenancePauseService.
    pause()``'s own bound, must refuse a span past ``MAX_PAUSE_HOURS`` -- the
    generic ``PUT /api/settings/{key}`` route funnels through this same
    validator and has no bound of its own."""
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="exceed"):
        validate_pause_payload(
            {
                "paused": True,
                "paused_by": "ceo",
                "paused_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=MAX_PAUSE_HOURS + 1)).isoformat(),
            }
        )


def test_validate_pause_payload_accepts_duration_at_max_hours_boundary() -> None:
    now = datetime.now(UTC)
    validate_pause_payload(
        {
            "paused": True,
            "paused_by": "ceo",
            "paused_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=MAX_PAUSE_HOURS)).isoformat(),
        }
    )  # no raise -- boundary is inclusive
