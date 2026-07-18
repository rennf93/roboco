"""Telegram Mini App ``initData`` validation.

Implements Telegram's documented WebApp signing algorithm — pure, no I/O:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    data_check_string = "\\n".join(sorted("key=value" pairs, hash excluded))
    secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token)
    expected_hash = HMAC_SHA256(key=secret_key, msg=data_check_string).hexdigest()
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

_WEBAPP_DATA_KEY = b"WebAppData"


def _hash_matches(fields: dict[str, str], received_hash: str, bot_token: str) -> bool:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(_WEBAPP_DATA_KEY, bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hash, received_hash)


# Telegram stamps auth_date server-side; allow a small negative delta for
# local clock skew, but a far-future auth_date is nonsense — reject it
# rather than treating it as eternally fresh.
_CLOCK_SKEW_TOLERANCE_SECONDS = 60


def _is_fresh(auth_date_raw: str | None, max_age_seconds: int) -> bool:
    if auth_date_raw is None:
        return False
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        return False
    delta = time.time() - auth_date
    return -_CLOCK_SKEW_TOLERANCE_SECONDS <= delta <= max_age_seconds


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int
) -> dict[str, object] | None:
    """Validate a Telegram Mini App ``initData`` query string.

    Returns the parsed fields (``user`` JSON-decoded) on a valid, correctly
    signed, still-fresh payload. Returns ``None`` on any failure: missing/bad
    hash, an unparsable ``user`` field, or ``auth_date`` older than
    ``max_age_seconds`` — the caller can't distinguish the reason, which is
    the point (no oracle for an attacker to iterate against).
    """
    if not init_data or not bot_token:
        return None

    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = fields.pop("hash", None)
    if not received_hash or not _hash_matches(fields, received_hash, bot_token):
        return None
    if not _is_fresh(fields.get("auth_date"), max_age_seconds):
        return None

    result: dict[str, object] = dict(fields)
    user_raw = result.get("user")
    if user_raw is not None:
        try:
            result["user"] = json.loads(str(user_raw))
        except json.JSONDecodeError:
            return None
    return result
