"""GitHub App installation-token minting — the drop-in replacement for a PAT.

``GitService``'s plumbing already builds ``Authorization: Basic
base64("x-access-token:<token>")`` per call, exactly GitHub's installation-
token convention, so a minted token needs no new transport — only a source.
Three operations: mint (and cache) an installation token, list the App's
installations, and list the repos one installation can access — all signed
with an RS256 App JWT built from the singleton ``github_app_credentials`` row.

The in-memory token cache is process-local (mirrors the sandbox-info cache's
known ceiling): an orchestrator restart forgets live tokens and the next call
re-mints. That's fine — minting is cheap and has no side effects on GitHub.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

import httpx
import jwt

from roboco.config import settings
from roboco.services.github_app_credentials import get_github_app_credentials_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# GitHub caps an App JWT at 10 minutes; stay comfortably under it. Backdate
# `iat` to tolerate clock skew between this host and GitHub's, same margin
# GitHub's own docs recommend.
_JWT_TTL_SECONDS = 9 * 60
_JWT_CLOCK_SKEW_SECONDS = 60
# Re-mint an installation token this long before its real expiry so a call
# never races a token that expires mid-request.
_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
_PER_PAGE = 100
# Safety cap on the repositories pagination loop — 5000 repos is far beyond
# any real installation; guards against an infinite loop on a malformed reply.
_MAX_REPO_PAGES = 50
_HTTP_ERROR_STATUS = 400


class GitHubAppError(Exception):
    """Base error for GitHub App operations."""


class GitHubAppNotConfiguredError(GitHubAppError):
    """No App credentials are stored. Callers map this to a 409/412 response."""


class GitHubAppAPIError(GitHubAppError):
    """A GitHub API call returned a non-2xx response."""


@dataclass(frozen=True)
class Installation:
    """One App installation (an org or user account that installed the App)."""

    id: int
    account_login: str


@dataclass(frozen=True)
class InstallationRepo:
    """One repository visible to an installation."""

    full_name: str
    clone_url: str
    private: bool


# installation_id -> (token, expires_at epoch seconds). Process-local; see
# the module docstring's known ceiling.
_token_cache: dict[int, tuple[str, float]] = {}


def _api_base() -> str:
    return settings.github_api_base_url.rstrip("/")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }


def _timeout() -> float:
    return settings.provisioning_timeout_seconds


def _parse_expiry(expires_at: str | None) -> float:
    """GitHub returns an ISO-8601 ``expires_at``; fall back to a conservative
    one-hour horizon if the response is ever missing it (shouldn't happen)."""
    if not expires_at:
        return time.time() + 3600
    return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()


async def _app_jwt(session: AsyncSession) -> str:
    """Mint a fresh RS256 App JWT from the stored credentials."""
    creds = await get_github_app_credentials_service(session).get_decrypted()
    if creds is None:
        raise GitHubAppNotConfiguredError("GitHub App credentials are not configured")
    now = int(time.time())
    payload = {
        "iat": now - _JWT_CLOCK_SKEW_SECONDS,
        "exp": now + _JWT_TTL_SECONDS,
        "iss": creds.app_id,
    }
    return jwt.encode(payload, creds.private_key, algorithm="RS256")


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.status_code >= _HTTP_ERROR_STATUS:
        raise GitHubAppAPIError(
            f"{action} failed ({resp.status_code}): {resp.text[:200]}"
        )


async def mint_installation_token(session: AsyncSession, installation_id: int) -> str:
    """Return a live installation token, minting (and caching) as needed."""
    cached = _token_cache.get(installation_id)
    if cached is not None:
        token, expires_at = cached
        if expires_at - _TOKEN_REFRESH_MARGIN_SECONDS > time.time():
            return token

    app_jwt = await _app_jwt(session)
    url = f"{_api_base()}/app/installations/{installation_id}/access_tokens"
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        resp = await client.post(url, headers=_headers(app_jwt))
    _raise_for_status(resp, "Minting installation token")

    data = resp.json()
    token = cast("str", data["token"])
    _token_cache[installation_id] = (token, _parse_expiry(data.get("expires_at")))
    return token


async def list_installations(session: AsyncSession) -> list[Installation]:
    """List every installation of the configured App (JWT-authenticated)."""
    app_jwt = await _app_jwt(session)
    url = f"{_api_base()}/app/installations"
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        resp = await client.get(
            url, headers=_headers(app_jwt), params={"per_page": _PER_PAGE}
        )
    _raise_for_status(resp, "Listing installations")

    return [
        Installation(
            id=item["id"], account_login=(item.get("account") or {}).get("login", "")
        )
        for item in resp.json()
    ]


async def list_installation_repositories(
    session: AsyncSession, installation_id: int
) -> list[InstallationRepo]:
    """List every repository one installation can access (paginated)."""
    token = await mint_installation_token(session, installation_id)
    url = f"{_api_base()}/installation/repositories"
    repos: list[InstallationRepo] = []
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        for page in range(1, _MAX_REPO_PAGES + 1):
            resp = await client.get(
                url,
                headers=_headers(token),
                params={"per_page": _PER_PAGE, "page": page},
            )
            _raise_for_status(resp, "Listing installation repositories")

            items = resp.json().get("repositories", [])
            repos.extend(
                InstallationRepo(
                    full_name=item["full_name"],
                    clone_url=item["clone_url"],
                    private=bool(item.get("private", False)),
                )
                for item in items
            )
            if len(items) < _PER_PAGE:
                break
    return repos
