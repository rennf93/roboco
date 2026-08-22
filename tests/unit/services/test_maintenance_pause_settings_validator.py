"""maintenance_pause.{scope} keys ride the same validated system_settings
store board_program.{key}.enabled uses: one JSON payload per scope."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from roboco.foundation.policy.maintenance_pause import MAX_PAUSE_HOURS
from roboco.services.settings import (
    SettingValidationError,
    get_settings_service,
    validate_setting,
)

_NOW = datetime.now(UTC)
_VALID_PAYLOAD = {
    "paused": True,
    "paused_by": "ceo",
    "paused_at": _NOW.isoformat(),
    "reason": "maintenance",
    "expires_at": (_NOW + timedelta(hours=1)).isoformat(),
}


def test_validate_setting_accepts_every_scope_key() -> None:
    for scope in ("dispatch", "board_programs", "engines"):
        validate_setting(f"maintenance_pause.{scope}", json.dumps(_VALID_PAYLOAD))
        validate_setting(f"maintenance_pause.{scope}", json.dumps({"paused": False}))


def test_validate_setting_rejects_non_json_value() -> None:
    with pytest.raises(SettingValidationError, match="valid JSON"):
        validate_setting("maintenance_pause.dispatch", "not json")


def test_validate_setting_rejects_structurally_invalid_payload() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("maintenance_pause.dispatch", json.dumps({"paused": True}))


def test_validate_setting_rejects_unregistered_scope() -> None:
    with pytest.raises(SettingValidationError, match="Unknown or read-only"):
        validate_setting(
            "maintenance_pause.not_a_real_scope", json.dumps({"paused": False})
        )


def test_validate_setting_rejects_expiry_beyond_max_pause_hours() -> None:
    """DEFECT 4: the 14-day rail must hold no matter which door the write
    comes through. This is the generic ``PUT /api/settings/{key}`` surface
    (``validate_setting``, not ``MaintenancePauseService.pause()``'s own
    Python-level bound) -- before the fix it wrote an effectively permanent
    pause straight past that bound."""
    now = datetime.now(UTC)
    payload = {
        "paused": True,
        "paused_by": "ceo",
        "paused_at": now.isoformat(),
        "reason": None,
        "expires_at": (now + timedelta(days=365)).isoformat(),
    }
    with pytest.raises(SettingValidationError, match="exceed"):
        validate_setting("maintenance_pause.dispatch", json.dumps(payload))


def test_validate_setting_accepts_expiry_within_max_pause_hours() -> None:
    now = datetime.now(UTC)
    payload = {
        "paused": True,
        "paused_by": "ceo",
        "paused_at": now.isoformat(),
        "reason": None,
        "expires_at": (now + timedelta(hours=MAX_PAUSE_HOURS)).isoformat(),
    }
    validate_setting("maintenance_pause.dispatch", json.dumps(payload))  # no raise


@pytest.mark.asyncio
async def test_settings_service_set_then_get_roundtrips(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    await svc.set("maintenance_pause.engines", json.dumps(_VALID_PAYLOAD))
    stored = await svc.get("maintenance_pause.engines")
    assert stored is not None
    assert json.loads(stored)["paused_by"] == "ceo"
