"""Keep the Codex CLI credential live so headless codex agents never hit an
expired token.

The Codex CLI (``codex``, OpenAI's official terminal coding agent) stores its
ChatGPT-subscription credential at ``~/.codex/auth.json``: ``{auth_mode,
OPENAI_API_KEY, tokens: {id_token, access_token, refresh_token, account_id},
last_refresh}``. ``access_token`` is a JWT whose ``exp`` claim is the
authoritative expiry — unlike grok's bundle, this file carries no separate
``expires_at`` field, so staleness is decided by decoding the JWT itself. The
CLI self-refreshes IN-PROCESS when it notices the token is within 5 minutes of
expiry, but that only helps a container that can write back to its own
``auth.json`` — our per-agent mount is read-only (the same inode-pinning
concern as grok's mount, see :mod:`roboco.llm.providers.codex`), so an
in-container refresh writes silently fail and the container falls back to the
now-stale in-memory token for the rest of that one run only. The orchestrator
owns the durable refresh: it holds the host file read-write and calls
:func:`refresh_if_stale` on a loop, exactly like ``grok_auth``.

The refresh-token grant posts to ``https://auth.openai.com/oauth/token``
(verified). The grant's ``client_id`` is NOT part of the auth.json struct we
were handed, so :data:`_DEFAULT_OAUTH_CLIENT_ID` is a best-effort default
(the Codex CLI's own public, non-secret OAuth client id) — override with
``ROBOCO_CODEX_OAUTH_CLIENT_ID`` if OpenAI rotates it; this is the one value
in this module not drawn from the verified build facts, flagged here and in
the build report for a human to confirm.

The agent entrypoint calls ``--check`` as a backstop: if the mounted token is
missing/expired it exits non-zero immediately instead of hanging at an
interactive login flow.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# Process-wide serialisation for the single-use refresh-token grant (parity
# with grok_auth's #94 fix): two concurrent refreshes would have the loser
# submit the now-invalidated old refresh_token and burn the credential. The
# lock + re-load-and-recheck inside it makes the loser find the winner's
# refreshed token and return "fresh" instead of re-rotating.
_refresh_lock = threading.Lock()

_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
# Not part of the verified auth.json struct — see module docstring.
_DEFAULT_OAUTH_CLIENT_ID = os.environ.get(
    "ROBOCO_CODEX_OAUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann"
)
# Refresh when the access token expires within this window: a run that starts
# inside it could outlive the token, so refresh proactively rather than at the
# last second. Mirrors grok_auth's REFRESH_SKEW_SECONDS.
REFRESH_SKEW_SECONDS = int(os.environ.get("ROBOCO_CODEX_AUTH_REFRESH_SKEW", "1800"))
# A JWT is header.payload.signature; fewer parts means it isn't one.
_MIN_JWT_PARTS = 2


def default_auth_path() -> Path:
    """The Codex ``auth.json`` for the current process HOME (``~/.codex``)."""
    return Path.home() / ".codex" / "auth.json"


def _load(auth_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tokens(bundle: dict[str, Any]) -> dict[str, Any]:
    tokens = bundle.get("tokens")
    return tokens if isinstance(tokens, dict) else {}


def _exp_from_jwt(token: str) -> datetime | None:
    """Decode the JWT ``exp`` claim (unix seconds) from an access token.

    The Codex access token is a JWT whose ``exp`` is the ONLY expiry signal —
    unlike grok's bundle there is no sibling ``expires_at`` field, so every
    staleness check in this module goes through this decode. Returns ``None``
    for an unparseable / non-JWT / claim-less token.
    """
    parts = token.split(".")
    if len(parts) < _MIN_JWT_PARTS:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(float(exp), tz=UTC)


def seconds_until_expiry(
    auth_path: Path, *, now: datetime | None = None
) -> float | None:
    """Seconds until the access token expires, or ``None`` if unreadable/absent."""
    bundle = _load(auth_path)
    if bundle is None:
        return None
    access_token = _tokens(bundle).get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    expires_at = _exp_from_jwt(access_token)
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
    """Rewrite ``auth.json`` atomically, preserving the original file mode.

    The rotated refresh_token is single-use (OpenAI invalidates the old one the
    instant it issues the new one), so losing this write loses the credential
    permanently — the same F006 concern grok_auth documents. Atomic tmp+replace
    first; a direct-write fallback if that fails, so the rotated token still
    lands on disk. Only if BOTH fail does the OSError propagate.
    """
    payload = json.dumps(bundle)
    tmp = auth_path.with_name(auth_path.name + ".refresh.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        with contextlib.suppress(OSError):
            shutil.copymode(auth_path, tmp)
        tmp.replace(auth_path)
        return
    except OSError as exc:
        logger.warning(
            "codex auth atomic write failed; trying direct write", error=str(exc)
        )
    auth_path.write_text(payload, encoding="utf-8")


def _is_stale(tokens: dict[str, Any], now: datetime, skew_seconds: int) -> bool:
    """True when the token is unparseable or within ``skew_seconds`` of expiry."""
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return True
    expires_at = _exp_from_jwt(access_token)
    return expires_at is None or (expires_at - now).total_seconds() <= skew_seconds


def _apply_refreshed_token(tokens: dict[str, Any], token: dict[str, Any]) -> None:
    """Write the new access/refresh/id token into the ``tokens`` sub-object."""
    tokens["access_token"] = token["access_token"]
    if token.get("refresh_token"):
        tokens["refresh_token"] = token["refresh_token"]
    if token.get("id_token"):
        tokens["id_token"] = token["id_token"]


def _do_refresh(
    auth_path: Path,
    bundle: dict[str, Any],
    now: datetime,
    post: Callable[[str, dict[str, str]], dict[str, Any]],
) -> str:
    """Run the refresh-token grant and persist the result; returns the status."""
    tokens = _tokens(bundle)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return "no_refresh_token"
    try:
        token = post(
            _TOKEN_ENDPOINT,
            {
                "grant_type": "refresh_token",
                "refresh_token": str(refresh_token),
                "client_id": _DEFAULT_OAUTH_CLIENT_ID,
            },
        )
    except Exception as exc:
        logger.warning("codex auth refresh request failed", error=str(exc))
        return "failed"
    if not token.get("access_token"):
        logger.warning("codex auth refresh returned no access_token")
        return "failed"
    _apply_refreshed_token(tokens, token)
    bundle["tokens"] = tokens
    bundle["last_refresh"] = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    try:
        _atomic_write(auth_path, bundle)
    except OSError as exc:
        logger.warning("codex auth refresh write failed", error=str(exc))
        return "failed"
    logger.info("codex auth refreshed")
    return "refreshed"


def _recheck_or_refresh(
    auth_path: Path,
    now: datetime,
    skew_seconds: int,
    post: Callable[[str, dict[str, str]], dict[str, Any]] | None,
) -> str:
    """Re-load + re-check staleness, then refresh — the locked body of refresh_if_stale.

    Run inside ``_refresh_lock`` so a concurrent caller that waited on the lock
    re-reads the bundle a single-writer just refreshed and returns ``fresh``
    instead of re-POSTing the single-use refresh grant.
    """
    bundle = _load(auth_path)
    if bundle is None:
        return "missing"
    tokens = _tokens(bundle)
    if not tokens.get("refresh_token"):
        return "no_refresh_token"
    if not _is_stale(tokens, now, skew_seconds):
        return "fresh"
    return _do_refresh(auth_path, bundle, now, post or _post_token)


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
    (an API-key-mode auth.json, or no usable credential), or ``failed`` (the
    refresh request errored). Best-effort: never raises.
    """
    bundle = _load(auth_path)
    if bundle is None:
        return "missing"
    tokens = _tokens(bundle)
    if not tokens.get("refresh_token"):
        return "no_refresh_token"
    now = now or datetime.now(UTC)
    if not _is_stale(tokens, now, skew_seconds):
        return "fresh"
    # Single-use refresh token: hold the lock and re-load + re-check inside it
    # so a concurrent caller that waited on the lock finds the refreshed token
    # and returns "fresh" instead of re-POSTing the grant (which would use the
    # already-invalidated old token and burn the credential).
    with _refresh_lock:
        return _recheck_or_refresh(auth_path, now, skew_seconds, post)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--check`` for the entrypoint backstop, else refresh-if-stale.

    ``--check`` exits non-zero when the mounted token is missing or expired (so
    the agent entrypoint can refuse to run instead of hanging at an interactive
    login flow). With no flag it refreshes the host token if stale.
    """
    args = argv if argv is not None else sys.argv[1:]
    auth_path = default_auth_path()
    if "--check" in args:
        return 0 if is_valid(auth_path) else 1
    status = refresh_if_stale(auth_path)
    return 0 if status in {"fresh", "refreshed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
