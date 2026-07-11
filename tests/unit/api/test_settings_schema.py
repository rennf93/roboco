"""SettingUpdate accepts the JSON scalars the panel naturally sends.

The feature-flags card sends booleans (a live PUT
/settings/notifications_enabled 422'd on type alone) and numeric settings
send numbers; all values persist as text. Constructed via model_validate —
the wire shape the route actually receives.
"""

from __future__ import annotations

from roboco.api.schemas.settings import SettingUpdate


def test_bool_true_coerces_to_stored_form() -> None:
    assert SettingUpdate.model_validate({"value": True}).value == "true"


def test_bool_false_coerces_to_stored_form() -> None:
    assert SettingUpdate.model_validate({"value": False}).value == "false"


def test_int_coerces_to_text() -> None:
    assert SettingUpdate.model_validate({"value": 30}).value == "30"


def test_plain_string_unchanged() -> None:
    assert SettingUpdate.model_validate({"value": "qwen3"}).value == "qwen3"
