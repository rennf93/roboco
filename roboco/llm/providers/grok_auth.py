"""Keep the SuperGrok credential live so headless grok agents never hit an
expired token.

The grok access token in ``~/.grok/auth.json`` has a fixed ~6h TTL set by xAI's
auth server (baked into the JWT ``exp``; the client cannot lengthen it). The
grok CLI exposes only ``login`` / ``logout`` — there is no refresh command — and
headless ``grok -p`` does NOT silently refresh: on an expired token it drops to
an interactive "Waiting for authorization..." prompt that hangs forever in a
container. The bundle does carry an ``offline_access`` refresh token, and xAI's
OIDC token endpoint supports the ``refresh_token`` grant, so we mint a fresh
access token ourselves before expiry and rewrite ``auth.json`` in place.

The orchestrator owns the refresh (the per-agent mount is read-only, so a
container can't write the credential back), calling :func:`refresh_if_stale` on
the host ``auth.json`` on a loop. The agent entrypoint calls ``--check`` as a
backstop: if the mounted token is missing/expired it exits non-zero immediately
rather than hanging at the login prompt.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# xAI OIDC issuer; the token endpoint is ``<issuer>/oauth2/token`` (verified via
# the issuer's ``.well-known/openid-configuration``). A per-entry ``oidc_issuer``
# overrides it.
_DEFAULT_ISSUER = "https://auth.x.ai"
# Refresh when the token expires within this window: a run that starts inside it
# could outlive the token, so refresh proactively rather than at the last second.
REFRESH_SKEW_SECONDS = int(os.environ.get("ROBOCO_GROK_AUTH_REFRESH_SKEW", "1800"))
# grok writes ``expires_at`` with nanosecond precision + ``Z``; datetime only
# parses up to microseconds, so trim the fractional part to 6 digits.
_FRACTIONAL = re.compile(r"^(?P<head>.*\.\d{6})\d*(?P<tz>[+-]\d{2}:\d{2})?$")


def default_auth_path() -> Path:
    """The grok ``auth.json`` for the current home (``GROK_HOME`` or ``~/.grok``)."""
    home = os.environ.get("GROK_HOME") or str(Path.home() / ".grok")
    return Path(home) / "auth.json"


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse grok's ISO-8601 ``expires_at`` (nanosecond ``Z`` form) to a datetime."""
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    match = _FRACTIONAL.match(text)
    if match:
        text = match.group("head") + (match.group("tz") or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _to_iso_z(moment: datetime) -> str:
    """Serialize back to grok's ``...Z`` form."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load(auth_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _credential_entry(bundle: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The single ``<issuer>::<client-id> -> creds`` entry holding a refresh token."""
    for entry_key, value in bundle.items():
        if isinstance(value, dict) and value.get("refresh_token"):
            return entry_key, value
    return None


def seconds_until_expiry(
    auth_path: Path, *, now: datetime | None = None
) -> float | None:
    """Seconds until the access token expires, or ``None`` if unreadable/absent."""
    bundle = _load(auth_path)
    if bundle is None:
        return None
    entry = _credential_entry(bundle)
    if entry is None:
        return None
    expires_at = _parse_timestamp(str(entry[1].get("expires_at", "")))
    if expires_at is None:
        return None
    return (expires_at - (now or datetime.now(UTC))).total_seconds()


def is_valid(
    auth_path: Path, *, skew_seconds: int = 0, now: datetime | None = None
) -> bool:
    """True when a token exists and has more than ``skew_seconds`` of life left."""
    remaining = seconds_until_expiry(auth_path, now=now)
    return remaining is not None and remaining > skew_seconds


def _post_token(url: str, form: dict[str, str]) -> dict[str, Any]:
    """POST the OAuth token request; return the parsed JSON body."""
    response = httpx.post(url, data=form, timeout=30.0)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


def _atomic_write(auth_path: Path, bundle: dict[str, Any]) -> None:
    """Rewrite ``auth.json`` atomically, preserving the original file mode."""
    tmp = auth_path.with_name(auth_path.name + ".refresh.tmp")
    tmp.write_text(json.dumps(bundle), encoding="utf-8")
    with contextlib.suppress(OSError):
        shutil.copymode(auth_path, tmp)
    tmp.replace(auth_path)


def _is_stale(creds: dict[str, Any], now: datetime, skew_seconds: int) -> bool:
    """True when the token is unparseable or within ``skew_seconds`` of expiry."""
    expires_at = _parse_timestamp(str(creds.get("expires_at", "")))
    return expires_at is None or (expires_at - now).total_seconds() <= skew_seconds


def _apply_refreshed_token(
    creds: dict[str, Any], token: dict[str, Any], now: datetime
) -> None:
    """Write the new access token (and rotated refresh token / expiry) into creds."""
    creds["key"] = token["access_token"]
    if token.get("refresh_token"):
        creds["refresh_token"] = token["refresh_token"]
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)):
        creds["expires_at"] = _to_iso_z(now + timedelta(seconds=float(expires_in)))
    creds["create_time"] = _to_iso_z(now)


def _do_refresh(
    auth_path: Path,
    bundle: dict[str, Any],
    entry_key: str,
    now: datetime,
    post: Callable[[str, dict[str, str]], dict[str, Any]],
) -> str:
    """Run the refresh-token grant and persist the result; returns the status."""
    creds = bundle[entry_key]
    client_id = creds.get("oidc_client_id")
    refresh_token = creds.get("refresh_token")
    if not (client_id and refresh_token):
        return "no_refresh_token"
    issuer = str(creds.get("oidc_issuer") or _DEFAULT_ISSUER).rstrip("/")
    try:
        token = post(
            f"{issuer}/oauth2/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": str(refresh_token),
                "client_id": str(client_id),
            },
        )
    except Exception as exc:
        logger.warning("grok auth refresh request failed", error=str(exc))
        return "failed"
    if not token.get("access_token"):
        logger.warning("grok auth refresh returned no access_token")
        return "failed"
    _apply_refreshed_token(creds, token, now)
    bundle[entry_key] = creds
    try:
        _atomic_write(auth_path, bundle)
    except OSError as exc:
        logger.warning("grok auth refresh write failed", error=str(exc))
        return "failed"
    logger.info("grok auth refreshed", expires_in=token.get("expires_in"))
    return "refreshed"


def refresh_if_stale(
    auth_path: Path,
    *,
    skew_seconds: int = REFRESH_SKEW_SECONDS,
    now: datetime | None = None,
    post: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
) -> str:
    """Mint a fresh access token from the refresh token if expiry is near.

    Returns a status string: ``fresh`` (still valid, nothing done), ``refreshed``
    (a new token was written), ``missing`` (no auth.json), ``no_refresh_token``
    (no usable credential entry), or ``failed`` (the refresh request errored).
    Best-effort: never raises.
    """
    bundle = _load(auth_path)
    if bundle is None:
        return "missing"
    entry = _credential_entry(bundle)
    if entry is None:
        return "no_refresh_token"
    entry_key, creds = entry
    now = now or datetime.now(UTC)
    if not _is_stale(creds, now, skew_seconds):
        return "fresh"
    return _do_refresh(auth_path, bundle, entry_key, now, post or _post_token)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--check`` for the entrypoint backstop, else refresh-if-stale.

    ``--check`` exits non-zero when the mounted token is missing or expired (so
    the agent entrypoint can refuse to run instead of hanging at grok's login
    prompt). With no flag it refreshes the host token if stale.
    """
    args = argv if argv is not None else sys.argv[1:]
    auth_path = default_auth_path()
    if "--check" in args:
        return 0 if is_valid(auth_path) else 1
    status = refresh_if_stale(auth_path)
    return 0 if status in {"fresh", "refreshed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
