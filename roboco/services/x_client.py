"""X (Twitter) API v2 client — server-side only, agents never touch it.

Thin httpx wrapper for the two operations the engine needs: post a tweet and
fetch mentions, both OAuth 1.0a user-context signed. Mirrors the
`SearchProvider`/`NullProvider` shape in `services/research.py`: a
`NullXClient` is returned when credentials are unset, so the engine degrades
gracefully (empty results, no exception) exactly like an unconfigured research
provider — never raises into the caller, never makes a network call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from roboco.services.x_credentials import XCredentialsData

MAX_TWEET_CHARS = 280

_API_BASE = "https://api.twitter.com/2"
_TWEETS_URL = f"{_API_BASE}/tweets"
_ME_URL = f"{_API_BASE}/users/me"


class XClientError(Exception):
    """A live-client call failed (network error or non-2xx response)."""


@dataclass(frozen=True)
class XPostResult:
    """The outcome of a `post_tweet` call."""

    posted: bool
    tweet_id: str | None
    detail: str


@dataclass(frozen=True)
class XMention:
    """One normalised mention, enough to filter + draft a reply."""

    id: str
    author_id: str
    text: str
    like_count: int
    reply_count: int
    retweet_count: int


# --------------------------------------------------------------------------- #
# OAuth 1.0a user-context signing (hand-rolled — no new dependency).
# https://developer.x.com/en/docs/authentication/oauth-1-0a
# --------------------------------------------------------------------------- #


def _percent_encode(value: str) -> str:
    """RFC 3986 unreserved-safe percent-encoding (OAuth 1.0a section 3.6)."""
    return quote(str(value), safe="~")


def _signature_base_string(method: str, url: str, params: dict[str, str]) -> str:
    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(params.items())
    )
    return "&".join(
        [
            method.upper(),
            _percent_encode(url),
            _percent_encode(param_string),
        ]
    )


def _sign(base_string: str, consumer_secret: str, token_secret: str) -> str:
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def oauth1_authorization_header(
    method: str,
    url: str,
    creds: XCredentialsData,
    query_params: dict[str, str] | None = None,
) -> str:
    """Build the `Authorization: OAuth ...` header for one signed request.

    `query_params` are the request's own query-string params (GET) — they
    participate in the signature base string per spec but are NOT sent in the
    header. A JSON POST body is never signed (X's v2 API OAuth 1.0a only signs
    query params + oauth params, not body content). Tests pin the nonce/
    timestamp by monkeypatching `secrets.token_hex` / `time.time` rather than
    threading extra parameters through this signature.
    """
    oauth_params = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **(query_params or {})}
    base_string = _signature_base_string(method, url, all_params)
    oauth_params["oauth_signature"] = _sign(
        base_string, creds.api_secret, creds.access_token_secret
    )
    header_params = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_params}"


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class XClient(ABC):
    """Abstract X API surface. `NullXClient` is the graceful-degradation stub."""

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def post_tweet(self, text: str) -> XPostResult: ...

    @abstractmethod
    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]: ...


class NullXClient(XClient):
    """No credentials configured — every call is a no-op, never raises."""

    @property
    def configured(self) -> bool:
        return False

    async def post_tweet(self, text: str) -> XPostResult:
        _ = text
        return XPostResult(
            posted=False, tweet_id=None, detail="no credentials configured"
        )

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []


class LiveXClient(XClient):
    """Real OAuth 1.0a-signed calls against the X API v2."""

    def __init__(
        self,
        creds: XCredentialsData,
        *,
        account_user_id: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds = creds
        self._account_user_id = account_user_id
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return True

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _resolve_account_user_id(self, client: httpx.AsyncClient) -> str | None:
        if self._account_user_id:
            return self._account_user_id
        header = oauth1_authorization_header("GET", _ME_URL, self._creds)
        try:
            resp = await client.get(
                _ME_URL, headers={"Authorization": header}, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise XClientError(f"GET /users/me failed: {exc}") from exc
        if not resp.is_success:
            raise XClientError(
                f"GET /users/me: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data: dict[str, Any] = resp.json()
        user_id = (data.get("data") or {}).get("id")
        return str(user_id) if user_id else None

    async def post_tweet(self, text: str) -> XPostResult:
        client = await self._http()
        header = oauth1_authorization_header("POST", _TWEETS_URL, self._creds)
        try:
            resp = await client.post(
                _TWEETS_URL,
                headers={
                    "Authorization": header,
                    "Content-Type": "application/json",
                },
                json={"text": text},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return XPostResult(
                posted=False, tweet_id=None, detail=f"request failed: {exc}"
            )
        if not resp.is_success:
            detail = resp.text[:200] if resp.text else "no body"
            return XPostResult(
                posted=False, tweet_id=None, detail=f"HTTP {resp.status_code}: {detail}"
            )
        try:
            data: dict[str, Any] = resp.json()
        except (ValueError, TypeError) as exc:
            return XPostResult(
                posted=False, tweet_id=None, detail=f"invalid JSON: {exc}"
            )
        tweet_id = (data.get("data") or {}).get("id")
        if not tweet_id:
            return XPostResult(
                posted=False, tweet_id=None, detail="no tweet id in response"
            )
        return XPostResult(posted=True, tweet_id=str(tweet_id), detail="posted")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        client = await self._http()
        try:
            user_id = await self._resolve_account_user_id(client)
        except XClientError:
            return []
        if not user_id:
            return []
        url = f"{_API_BASE}/users/{user_id}/mentions"
        params: dict[str, str] = {
            "max_results": str(max(5, min(max_results, 100))),
            "tweet.fields": "public_metrics,author_id",
        }
        if since_id:
            params["since_id"] = since_id
        header = oauth1_authorization_header("GET", url, self._creds, params)
        try:
            resp = await client.get(
                url,
                headers={"Authorization": header},
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            return []
        if not resp.is_success:
            return []
        try:
            data: dict[str, Any] = resp.json()
        except (ValueError, TypeError):
            return []
        mentions: list[XMention] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            metrics = item.get("public_metrics") or {}
            mentions.append(
                XMention(
                    id=str(item.get("id", "")),
                    author_id=str(item.get("author_id", "")),
                    text=str(item.get("text", "")),
                    like_count=int(metrics.get("like_count", 0) or 0),
                    reply_count=int(metrics.get("reply_count", 0) or 0),
                    retweet_count=int(metrics.get("retweet_count", 0) or 0),
                )
            )
        return mentions


def build_x_client(
    creds: XCredentialsData | None,
    *,
    account_user_id: str,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> XClient:
    """Construct the client — `NullXClient` when credentials are unset."""
    if creds is None:
        return NullXClient()
    return LiveXClient(
        creds, account_user_id=account_user_id, timeout=timeout, client=client
    )
