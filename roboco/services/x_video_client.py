"""X (Twitter) v2 media upload — the real video poster.

Chunked media upload (initialize/append/finalize), a processing-status poll,
then ``POST /2/tweets`` with the resulting ``media_ids``. All OAuth 1.0a
user-context signed, reusing ``x_client``'s signer — the same
``x_credentials`` secrets sign both a plain-text tweet and a video-post
tweet. Mirrors ``x_client.py``'s Null/Live/build shape; ``NullXVideoPoster``
itself lives in ``video_post_service`` (the Protocol's home) and is
re-exported here as the "no credentials" branch of ``build_x_video_poster``.
The v2 media-upload API documents no hard chunk ceiling, so this client
chunks conservatively.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from roboco.services.video_post_service import (
    NullXVideoPoster,
    XVideoPoster,
    XVideoPostResult,
)
from roboco.services.x_client import oauth1_authorization_header

if TYPE_CHECKING:
    from roboco.services.x_credentials import XCredentialsData

logger = logging.getLogger(__name__)

__all__ = [
    "LiveXVideoPoster",
    "NullXVideoPoster",
    "XVideoClientError",
    "build_x_video_poster",
]

_API_BASE = "https://api.x.com/2"
_MEDIA_UPLOAD_URL = f"{_API_BASE}/media/upload"
_TWEETS_URL = f"{_API_BASE}/tweets"
_MEDIA_CATEGORY = "tweet_video"
# Conservative chunk size — v2 documents no hard ceiling; 4 MB keeps
# each append well within any reasonable request-body limit.
_CHUNK_SIZE_BYTES = 4 * 1024 * 1024
_STATUS_POLL_MAX_ATTEMPTS = 60
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0


class XVideoClientError(Exception):
    """A live video-poster call failed (network error, non-2xx, bad shape)."""


class LiveXVideoPoster(XVideoPoster):
    """Real X v2 media upload + tweet-with-video, OAuth 1.0a signed."""

    def __init__(
        self,
        creds: XCredentialsData,
        *,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds = creds
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

    async def post_video(self, *, mp4_path: str, caption: str) -> XVideoPostResult:
        client = await self._http()
        try:
            media_id = await self._upload_video(client, mp4_path)
            tweet_id = await self._create_tweet(client, caption, media_id)
        except XVideoClientError as exc:
            return XVideoPostResult(posted=False, video_id=None, detail=str(exc))
        return XVideoPostResult(posted=True, video_id=tweet_id, detail="posted")

    # ---- media/upload: initialize -> append* -> finalize -> poll STATUS ----

    async def _upload_video(self, client: httpx.AsyncClient, mp4_path: str) -> str:
        data = Path(mp4_path).read_bytes()
        media_id = await self._initialize(client, len(data))
        await self._append_chunks(client, media_id, data)
        finalize_data = await self._finalize(client, media_id)
        await self._ensure_succeeded(client, media_id, finalize_data)
        return media_id

    async def _initialize(self, client: httpx.AsyncClient, total_bytes: int) -> str:
        url = f"{_MEDIA_UPLOAD_URL}/initialize"
        payload = await self._post_json(
            client,
            url,
            {
                "media_type": "video/mp4",
                "total_bytes": total_bytes,
                "media_category": _MEDIA_CATEGORY,
            },
        )
        media_id = (payload.get("data") or {}).get("id")
        if not media_id:
            raise XVideoClientError("media/upload/initialize: no media id in response")
        return str(media_id)

    async def _append_chunks(
        self, client: httpx.AsyncClient, media_id: str, data: bytes
    ) -> None:
        url = f"{_MEDIA_UPLOAD_URL}/{media_id}/append"
        total = len(data)
        for segment_index, offset in enumerate(range(0, total, _CHUNK_SIZE_BYTES)):
            chunk = data[offset : offset + _CHUNK_SIZE_BYTES]
            header = oauth1_authorization_header("POST", url, self._creds)
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": header},
                    data={"segment_index": str(segment_index)},
                    files={"media": ("chunk.mp4", chunk, "application/octet-stream")},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise XVideoClientError(
                    f"media/upload/append segment {segment_index} failed: {exc}"
                ) from exc
            if not resp.is_success:
                raise XVideoClientError(
                    f"media/upload/append segment {segment_index}: "
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )

    async def _finalize(
        self, client: httpx.AsyncClient, media_id: str
    ) -> dict[str, Any]:
        url = f"{_MEDIA_UPLOAD_URL}/{media_id}/finalize"
        payload = await self._post_json(client, url, {})
        data = payload.get("data")
        return dict(data) if isinstance(data, dict) else {}

    async def _ensure_succeeded(
        self, client: httpx.AsyncClient, media_id: str, finalize_data: dict[str, Any]
    ) -> None:
        info = finalize_data.get("processing_info")
        if not isinstance(info, dict):
            return  # no processing step reported — already usable
        await self._poll_until_succeeded(client, media_id, dict(info))

    async def _poll_until_succeeded(
        self, client: httpx.AsyncClient, media_id: str, info: dict[str, Any]
    ) -> None:
        for _ in range(_STATUS_POLL_MAX_ATTEMPTS):
            state = info.get("state")
            if state == "succeeded":
                return
            if state == "failed":
                raise XVideoClientError(
                    f"media processing failed: {info.get('error') or info}"
                )
            wait = float(info.get("check_after_secs") or _DEFAULT_POLL_INTERVAL_SECONDS)
            await asyncio.sleep(wait)
            info = await self._get_status(client, media_id)
        raise XVideoClientError(
            "media processing did not reach 'succeeded' within the poll budget"
        )

    async def _get_status(
        self, client: httpx.AsyncClient, media_id: str
    ) -> dict[str, Any]:
        params = {"media_id": media_id, "command": "STATUS"}
        header = oauth1_authorization_header(
            "GET", _MEDIA_UPLOAD_URL, self._creds, params
        )
        try:
            resp = await client.get(
                _MEDIA_UPLOAD_URL,
                headers={"Authorization": header},
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise XVideoClientError(f"media/upload STATUS failed: {exc}") from exc
        if not resp.is_success:
            raise XVideoClientError(
                f"media/upload STATUS: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = self._parse_json(resp, "media/upload STATUS")
        data = payload.get("data")
        info = (data or {}).get("processing_info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise XVideoClientError(
                "media/upload STATUS: no processing_info in response"
            )
        return dict(info)

    # ---- POST /2/tweets ------------------------------------------------- #

    async def _create_tweet(
        self, client: httpx.AsyncClient, caption: str, media_id: str
    ) -> str:
        payload = await self._post_json(
            client,
            _TWEETS_URL,
            {"text": caption, "media": {"media_ids": [media_id]}},
        )
        tweet_id = (payload.get("data") or {}).get("id")
        if not tweet_id:
            raise XVideoClientError("POST /2/tweets: no tweet id in response")
        return str(tweet_id)

    # ---- shared JSON POST helper ------------------------------------------ #

    async def _post_json(
        self, client: httpx.AsyncClient, url: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        header = oauth1_authorization_header("POST", url, self._creds)
        try:
            resp = await client.post(
                url,
                headers={"Authorization": header, "Content-Type": "application/json"},
                json=body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise XVideoClientError(f"POST {url} failed: {exc}") from exc
        if not resp.is_success:
            raise XVideoClientError(
                f"POST {url}: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return self._parse_json(resp, f"POST {url}")

    @staticmethod
    def _parse_json(resp: httpx.Response, context: str) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = resp.json()
        except (ValueError, TypeError) as exc:
            raise XVideoClientError(f"{context}: invalid JSON: {exc}") from exc
        return payload


def build_x_video_poster(
    creds: XCredentialsData | None,
    *,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> XVideoPoster:
    """Construct the poster — ``NullXVideoPoster`` when credentials are unset."""
    if creds is None:
        return NullXVideoPoster()
    return LiveXVideoPoster(creds, timeout=timeout, client=client)
