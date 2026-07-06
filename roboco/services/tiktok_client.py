"""TikTok Content Posting API — inbox/upload draft mode (scope ``video.
upload``). The real poster: init -> chunked PUT -> status poll, landing the
clip as a DRAFT in the creator's TikTok inbox (the creator finishes + posts
manually in-app — this call never publishes anything). OAuth2 with
refresh-token rotation. Mirrors ``x_client.py``'s Null/Live/build shape;
``NullTikTokPoster`` lives in ``video_post_service`` (the Protocol's home)
and is re-exported here as the "no credentials" branch of
``build_tiktok_poster``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from roboco.services.tiktok_credentials import (
    TikTokCredentialsData,
    get_tiktok_credentials_service,
)
from roboco.services.video_post_service import (
    NullTikTokPoster,
    TikTokPoster,
    TikTokUploadResult,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

__all__ = [
    "LiveTikTokPoster",
    "NullTikTokPoster",
    "TikTokClientError",
    "build_tiktok_poster",
    "plan_chunk_ranges",
]

_API_BASE = "https://open.tiktokapis.com/v2"
_INIT_URL = f"{_API_BASE}/post/publish/inbox/video/init/"
_STATUS_URL = f"{_API_BASE}/post/publish/status/fetch/"
_TOKEN_URL = f"{_API_BASE}/oauth/token/"

# Asymmetric chunking: every chunk 5-64 MB except the final one, which
# absorbs the remainder up to 128 MB — a small dangling tail is folded into
# one larger final PUT instead of being sent as its own tiny chunk.
_CHUNK_SIZE_BYTES = 64 * 1024 * 1024
_MAX_FINAL_CHUNK_BYTES = 128 * 1024 * 1024

_SUCCESS_STATUS = "SEND_TO_USER_INBOX"
_FAILURE_STATUSES = frozenset({"FAILED"})
_STATUS_POLL_MAX_ATTEMPTS = 60
_STATUS_POLL_INTERVAL_SECONDS = 3.0
_HTTP_UNAUTHORIZED = 401


class TikTokClientError(Exception):
    """A live poster call failed (network error, non-2xx, bad shape)."""


def plan_chunk_ranges(
    total_bytes: int, *, chunk_size: int, max_final_chunk: int
) -> list[tuple[int, int]]:
    """(start, end) byte ranges (end exclusive) covering ``[0, total_bytes)``.

    Every range is ``chunk_size`` bytes except the last, which absorbs the
    remainder — up to ``max_final_chunk`` — rather than being split off into
    its own small trailing chunk. Requires ``max_final_chunk >= chunk_size``
    (true for the production constants: 128 MB >= 64 MB).
    """
    if total_bytes <= 0:
        return [(0, 0)]
    if total_bytes <= max_final_chunk:
        return [(0, total_bytes)]
    remaining = total_bytes
    offset = 0
    ranges: list[tuple[int, int]] = []
    while remaining > max_final_chunk:
        ranges.append((offset, offset + chunk_size))
        offset += chunk_size
        remaining -= chunk_size
    ranges.append((offset, total_bytes))
    return ranges


class LiveTikTokPoster(TikTokPoster):
    """Real inbox-upload poster: init -> chunked PUT -> status poll.

    Holds ``session`` because the OAuth2 refresh grant may rotate the
    refresh_token — the new pair must be persisted (``TikTokCredentialsService.
    update_tokens``) before the caller's session commits, or a retry after
    a restart would replay an already-invalidated refresh_token.
    """

    def __init__(
        self,
        creds: TikTokCredentialsData,
        *,
        session: AsyncSession,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds = creds
        self._session = session
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

    async def upload_to_inbox(
        self, *, mp4_path: str, caption: str
    ) -> TikTokUploadResult:
        # Inbox-upload's init request carries only source_info — no
        # title/caption field exists on this endpoint; the creator composes
        # the post manually in-app. Kept in the signature only because the
        # TikTokPoster Protocol is shared with future direct-post modes.
        _ = caption
        client = await self._http()
        data = Path(mp4_path).read_bytes()
        try:
            publish_id, upload_url, ranges = await self._init(client, len(data))
            await self._put_chunks(client, upload_url, data, ranges)
            status = await self._poll_until_done(client, publish_id)
        except TikTokClientError as exc:
            return TikTokUploadResult(uploaded=False, publish_id=None, detail=str(exc))
        return TikTokUploadResult(
            uploaded=True, publish_id=publish_id, detail=f"status={status}"
        )

    # ---- inbox/video/init -> chunked PUT ---------------------------------- #

    async def _init(
        self, client: httpx.AsyncClient, total_bytes: int
    ) -> tuple[str, str, list[tuple[int, int]]]:
        ranges = plan_chunk_ranges(
            total_bytes,
            chunk_size=_CHUNK_SIZE_BYTES,
            max_final_chunk=_MAX_FINAL_CHUNK_BYTES,
        )
        chunk_size = ranges[0][1] - ranges[0][0]
        body = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": total_bytes,
                "chunk_size": chunk_size,
                "total_chunk_count": len(ranges),
            }
        }
        payload = await self._post_authed(client, _INIT_URL, body)
        data = payload.get("data") or {}
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise TikTokClientError(
                "inbox/video/init: missing publish_id/upload_url in response"
            )
        return str(publish_id), str(upload_url), ranges

    async def _put_chunks(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        data: bytes,
        ranges: list[tuple[int, int]],
    ) -> None:
        total = len(data)
        for start, end in ranges:
            headers = {
                "Content-Range": f"bytes {start}-{end - 1}/{total}",
                "Content-Type": "video/mp4",
            }
            try:
                resp = await client.put(
                    upload_url,
                    content=data[start:end],
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise TikTokClientError(
                    f"chunk upload [{start}-{end}) failed: {exc}"
                ) from exc
            if not resp.is_success:
                raise TikTokClientError(
                    f"chunk upload [{start}-{end}): HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )

    # ---- status/fetch poll ------------------------------------------------ #

    async def _poll_until_done(self, client: httpx.AsyncClient, publish_id: str) -> str:
        for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
            status = await self._fetch_status(client, publish_id)
            if status == _SUCCESS_STATUS:
                return status
            if status in _FAILURE_STATUSES:
                raise TikTokClientError(f"publish failed: status={status}")
            await asyncio.sleep(_STATUS_POLL_INTERVAL_SECONDS)
        raise TikTokClientError(
            "status/fetch did not reach a terminal state within the poll budget"
        )

    async def _fetch_status(self, client: httpx.AsyncClient, publish_id: str) -> str:
        payload = await self._post_authed(
            client, _STATUS_URL, {"publish_id": publish_id}
        )
        data = payload.get("data") or {}
        status = data.get("status")
        if not status:
            raise TikTokClientError("status/fetch: no status in response")
        return str(status)

    # ---- Bearer-authed POST, with one 401 -> refresh -> retry -------------- #

    async def _post_authed(
        self, client: httpx.AsyncClient, url: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        resp = await self._raw_post(client, url, body)
        if resp.status_code == _HTTP_UNAUTHORIZED:
            await self._refresh(client)
            resp = await self._raw_post(client, url, body)
        if not resp.is_success:
            raise TikTokClientError(
                f"POST {url}: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return self._parse_json(resp, f"POST {url}")

    async def _raw_post(
        self, client: httpx.AsyncClient, url: str, body: dict[str, Any]
    ) -> httpx.Response:
        try:
            return await client.post(
                url, headers=self._auth_header(), json=body, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise TikTokClientError(f"POST {url} failed: {exc}") from exc

    def _auth_header(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._creds.access_token}",
            "Content-Type": "application/json",
        }

    # ---- OAuth2 refresh (rotates + persists the refresh_token) ------------ #

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        """POST /v2/oauth/token/ grant_type=refresh_token.

        TikTok may rotate the refresh_token on each use — persist whichever
        comes back (falling back to the current one if the response omits
        it) so the NEXT refresh doesn't replay an already-invalidated token.
        """
        try:
            resp = await client.post(
                _TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": self._creds.client_key,
                    "client_secret": self._creds.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._creds.refresh_token,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise TikTokClientError(f"oauth/token refresh failed: {exc}") from exc
        if not resp.is_success:
            raise TikTokClientError(
                f"oauth/token refresh: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = self._parse_json(resp, "oauth/token refresh")
        access_token = payload.get("access_token")
        if not access_token:
            raise TikTokClientError("oauth/token refresh: no access_token in response")
        refresh_token = payload.get("refresh_token") or self._creds.refresh_token
        self._creds = TikTokCredentialsData(
            client_key=self._creds.client_key,
            client_secret=self._creds.client_secret,
            access_token=str(access_token),
            refresh_token=str(refresh_token),
        )
        await get_tiktok_credentials_service(self._session).update_tokens(
            access_token=self._creds.access_token,
            refresh_token=self._creds.refresh_token,
        )

    @staticmethod
    def _parse_json(resp: httpx.Response, context: str) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = resp.json()
        except (ValueError, TypeError) as exc:
            raise TikTokClientError(f"{context}: invalid JSON: {exc}") from exc
        return payload


def build_tiktok_poster(
    creds: TikTokCredentialsData | None,
    *,
    session: AsyncSession,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> TikTokPoster:
    """Construct the poster — ``NullTikTokPoster`` when credentials are unset."""
    if creds is None:
        return NullTikTokPoster()
    return LiveTikTokPoster(creds, session=session, timeout=timeout, client=client)
