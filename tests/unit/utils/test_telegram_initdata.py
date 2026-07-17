"""``validate_init_data`` coverage — hand-computed HMAC vectors pin the exact
algorithm shape (HMAC key=b"WebAppData"/msg=bot_token for the secret, then
HMAC key=secret/msg=data_check_string for the hash), plus tamper/expiry/
missing-field cases. Pure function, no I/O — no DB/network fixtures needed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from roboco.utils.telegram_initdata import validate_init_data

_BOT_TOKEN = "123456:TEST-bot-token-for-unit-tests"


def _sign(fields: dict[str, str], bot_token: str = _BOT_TOKEN) -> str:
    """Reference HMAC computation, independent of the module under test."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _init_data(fields: dict[str, str], bot_token: str = _BOT_TOKEN) -> str:
    signed = dict(fields)
    signed["hash"] = _sign(fields, bot_token)
    return urlencode(signed)


def test_valid_init_data_returns_parsed_fields_with_user_decoded() -> None:
    user = {"id": 987654321, "first_name": "Renzo"}
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(user),
        "query_id": "AAH_abc123",
    }
    result = validate_init_data(_init_data(fields), _BOT_TOKEN, max_age_seconds=600)
    assert result is not None
    assert result["user"] == user
    assert result["query_id"] == "AAH_abc123"


def test_missing_hash_rejected() -> None:
    fields = {"auth_date": str(int(time.time()))}
    init_data = urlencode(fields)  # no hash field at all
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_tampered_field_after_signing_rejected() -> None:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 1})}
    signed_hash = _sign(fields)
    tampered = dict(fields)
    tampered["auth_date"] = str(int(time.time()) + 999)  # changed post-signing
    tampered["hash"] = signed_hash
    init_data = urlencode(tampered)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_wrong_bot_token_rejected() -> None:
    fields = {"auth_date": str(int(time.time()))}
    init_data = _init_data(fields, bot_token=_BOT_TOKEN)
    assert validate_init_data(init_data, "wrong-token", max_age_seconds=600) is None


def test_expired_auth_date_rejected() -> None:
    stale = int(time.time()) - 3600
    fields = {"auth_date": str(stale)}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_fresh_auth_date_within_window_accepted() -> None:
    fields = {"auth_date": str(int(time.time()) - 10)}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is not None


def test_far_future_auth_date_rejected() -> None:
    # A far-future auth_date is nonsense from a server-stamped field; without
    # an upper bound it would count as eternally fresh.
    fields = {"auth_date": str(int(time.time()) + 100_000)}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_slightly_future_auth_date_within_skew_tolerance_accepted() -> None:
    # Local clock lagging Telegram's by a few seconds must not break login.
    fields = {"auth_date": str(int(time.time()) + 30)}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is not None


def test_missing_auth_date_rejected() -> None:
    fields = {"query_id": "abc"}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_malformed_user_json_rejected() -> None:
    fields = {"auth_date": str(int(time.time())), "user": "not-json"}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, _BOT_TOKEN, max_age_seconds=600) is None


def test_empty_init_data_rejected() -> None:
    assert validate_init_data("", _BOT_TOKEN, max_age_seconds=600) is None


def test_empty_bot_token_rejected() -> None:
    fields = {"auth_date": str(int(time.time()))}
    init_data = _init_data(fields)
    assert validate_init_data(init_data, "", max_age_seconds=600) is None


if __name__ == "__main__":
    # ponytail: smallest runnable self-check; pytest is the real suite above.
    user = {"id": 42}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    assert validate_init_data(_init_data(fields), _BOT_TOKEN, 600)["user"] == user
    assert validate_init_data(_init_data(fields), "wrong", 600) is None
    print("telegram_initdata self-check OK")
