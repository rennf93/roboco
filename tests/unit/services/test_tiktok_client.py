"""LiveTikTokPoster coverage: the inbox-upload sequence (init -> chunked PUT
-> status poll), the asymmetric final chunk via `plan_chunk_ranges`, and the
OAuth2 refresh-token rotation + persistence path.

Uses the real `db_session` fixture (mirrors test_video_post_service.py) since
the refresh path genuinely persists to the `tiktok_credentials` row — a mock
session can't stand in for that.
"""

from __future__ import annotations

import json
from itertools import pairwise
from typing import TYPE_CHECKING

import httpx
import pytest
from roboco.services.tiktok_client import (
    LiveTikTokPoster,
    build_tiktok_poster,
    plan_chunk_ranges,
)
from roboco.services.tiktok_credentials import (
    TikTokCredentialsData,
    get_tiktok_credentials_service,
)
from roboco.services.video_post_service import NullTikTokPoster

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

TWO = 2
FOUR = 4

_CREDS = TikTokCredentialsData(
    client_key="ck-test",
    client_secret="cs-test",
    access_token="at-test",
    refresh_token="rt-test",
)


# ---- plan_chunk_ranges (pure) ---------------------------------------------- #


def test_plan_chunk_ranges_single_chunk_when_within_final_ceiling() -> None:
    assert plan_chunk_ranges(50, chunk_size=64, max_final_chunk=128) == [(0, 50)]


def test_plan_chunk_ranges_folds_the_remainder_into_a_larger_final_chunk() -> None:
    """Interior chunks stay a fixed size; the final one absorbs the
    remainder — larger than the interior size, the documented asymmetry."""
    ranges = plan_chunk_ranges(10, chunk_size=4, max_final_chunk=8)
    assert ranges == [(0, 4), (4, 10)]
    final_start, final_end = ranges[-1]
    assert (final_end - final_start) > FOUR  # bigger than the interior chunk size


def test_plan_chunk_ranges_multiple_interior_chunks() -> None:
    total = 300
    ranges = plan_chunk_ranges(total, chunk_size=64, max_final_chunk=128)
    assert ranges == [(0, 64), (64, 128), (128, 192), (192, 300)]
    assert sum(end - start for start, end in ranges) == total


def test_plan_chunk_ranges_is_contiguous_with_no_gaps_or_overlaps() -> None:
    total = 1000
    ranges = plan_chunk_ranges(total, chunk_size=97, max_final_chunk=150)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == total
    for (_, prev_end), (next_start, _) in pairwise(ranges):
        assert prev_end == next_start


def test_plan_chunk_ranges_empty_file() -> None:
    assert plan_chunk_ranges(0, chunk_size=64, max_final_chunk=128) == [(0, 0)]


# ---- build_tiktok_poster branching ----------------------------------------- #


@pytest.mark.asyncio
async def test_build_without_creds_returns_null(db_session: AsyncSession) -> None:
    poster = build_tiktok_poster(None, session=db_session, timeout=5.0)
    assert isinstance(poster, NullTikTokPoster)
    assert poster.configured is False


@pytest.mark.asyncio
async def test_build_with_creds_returns_live(db_session: AsyncSession) -> None:
    poster = build_tiktok_poster(_CREDS, session=db_session, timeout=5.0)
    assert isinstance(poster, LiveTikTokPoster)
    assert poster.configured is True


@pytest.mark.asyncio
async def test_null_poster_upload_is_a_noop() -> None:
    result = await NullTikTokPoster().upload_to_inbox(
        mp4_path="/tmp/x.mp4", caption="hi"
    )
    assert result.uploaded is False
    assert result.publish_id is None


def _write_clip(tmp_path: Path, data: bytes) -> str:
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(data)
    return str(mp4)


async def _no_sleep(_seconds: float) -> None:
    return None


# ---- upload_to_inbox: full sequence + asymmetric chunk over the wire ------- #


@pytest.mark.asyncio
async def test_upload_to_inbox_full_sequence_with_asymmetric_last_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    monkeypatch.setattr("roboco.services.tiktok_client._CHUNK_SIZE_BYTES", 4)
    monkeypatch.setattr("roboco.services.tiktok_client._MAX_FINAL_CHUNK_BYTES", 8)
    monkeypatch.setattr("roboco.services.tiktok_client.asyncio.sleep", _no_sleep)

    put_ranges: list[str] = []
    status_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/inbox/video/init/"):
            assert request.headers["Authorization"] == "Bearer at-test"
            body = json.loads(request.content)["source_info"]
            assert body == {
                "source": "FILE_UPLOAD",
                "video_size": 10,
                "chunk_size": 4,
                "total_chunk_count": TWO,
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "pub1",
                        "upload_url": "https://upload.tiktokapis.com/put/pub1",
                    }
                },
            )
        if request.method == "PUT":
            put_ranges.append(request.headers["Content-Range"])
            assert request.headers["Content-Type"] == "video/mp4"
            return httpx.Response(200)
        if request.method == "POST" and path.endswith("/status/fetch/"):
            status_calls["count"] += 1
            state = (
                "PROCESSING_UPLOAD"
                if status_calls["count"] == 1
                else "SEND_TO_USER_INBOX"
            )
            return httpx.Response(200, json={"data": {"status": state}})
        raise AssertionError(f"unexpected request {request.method} {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveTikTokPoster(
        _CREDS, session=db_session, timeout=5.0, client=http_client
    )
    result = await poster.upload_to_inbox(
        mp4_path=_write_clip(tmp_path, b"0123456789"), caption="ignored by inbox mode"
    )
    await http_client.aclose()

    assert result.uploaded is True
    assert result.publish_id == "pub1"
    # Interior chunk is 4 bytes; the final chunk absorbs the remainder (6
    # bytes) rather than being sent as its own small trailing chunk.
    assert put_ranges == ["bytes 0-3/10", "bytes 4-9/10"]
    assert status_calls["count"] == TWO  # PROCESSING_UPLOAD once, then terminal


@pytest.mark.asyncio
async def test_upload_publish_failed_status_is_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    monkeypatch.setattr("roboco.services.tiktok_client.asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/inbox/video/init/"):
            return httpx.Response(
                200, json={"data": {"publish_id": "pub1", "upload_url": "https://u/x"}}
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "FAILED"}})
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveTikTokPoster(
        _CREDS, session=db_session, timeout=5.0, client=http_client
    )
    result = await poster.upload_to_inbox(
        mp4_path=_write_clip(tmp_path, b"short"), caption="x"
    )
    await http_client.aclose()

    assert result.uploaded is False
    assert result.publish_id is None
    assert "publish failed" in result.detail


# ---- OAuth2 refresh: rotate + persist -------------------------------------- #


@pytest.mark.asyncio
async def test_upload_refreshes_and_persists_rotated_token_on_401(
    tmp_path: Path, db_session: AsyncSession
) -> None:
    """A 401 on the Bearer-authed init call triggers one refresh + retry; the
    rotated access/refresh token pair must land in the DB row — not just be
    held in the poster's in-memory creds."""
    await get_tiktok_credentials_service(db_session).set_credentials(
        client_key=_CREDS.client_key,
        client_secret=_CREDS.client_secret,
        access_token=_CREDS.access_token,
        refresh_token=_CREDS.refresh_token,
    )
    init_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/token/"):
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "refresh_token"
            assert form["refresh_token"] == "rt-test"
            return httpx.Response(
                200, json={"access_token": "at-rotated", "refresh_token": "rt-rotated"}
            )
        if path.endswith("/inbox/video/init/"):
            init_auth_headers.append(request.headers["Authorization"])
            if request.headers["Authorization"] == "Bearer at-test":
                return httpx.Response(401, json={"error": "access_token_invalid"})
            return httpx.Response(
                200, json={"data": {"publish_id": "pub2", "upload_url": "https://u/x"}}
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveTikTokPoster(
        _CREDS, session=db_session, timeout=5.0, client=http_client
    )
    result = await poster.upload_to_inbox(
        mp4_path=_write_clip(tmp_path, b"short"), caption="x"
    )
    await http_client.aclose()

    assert result.uploaded is True
    assert init_auth_headers == ["Bearer at-test", "Bearer at-rotated"]

    stored = await get_tiktok_credentials_service(db_session).get_decrypted()
    assert stored is not None
    assert stored.access_token == "at-rotated"
    assert stored.refresh_token == "rt-rotated"
    assert stored.client_key == _CREDS.client_key  # untouched by the refresh


@pytest.mark.asyncio
async def test_refresh_falls_back_to_current_refresh_token_when_response_omits_it(
    tmp_path: Path, db_session: AsyncSession
) -> None:
    """TikTok doesn't always rotate the refresh_token — when the grant
    response omits it, the previous one must be kept, not nulled out."""
    await get_tiktok_credentials_service(db_session).set_credentials(
        client_key=_CREDS.client_key,
        client_secret=_CREDS.client_secret,
        access_token=_CREDS.access_token,
        refresh_token=_CREDS.refresh_token,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/token/"):
            return httpx.Response(200, json={"access_token": "at-rotated"})
        if path.endswith("/inbox/video/init/"):
            if request.headers["Authorization"] == "Bearer at-test":
                return httpx.Response(401, json={"error": "access_token_invalid"})
            return httpx.Response(
                200, json={"data": {"publish_id": "pub3", "upload_url": "https://u/x"}}
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveTikTokPoster(
        _CREDS, session=db_session, timeout=5.0, client=http_client
    )
    result = await poster.upload_to_inbox(
        mp4_path=_write_clip(tmp_path, b"x"), caption="x"
    )
    await http_client.aclose()

    assert result.uploaded is True
    stored = await get_tiktok_credentials_service(db_session).get_decrypted()
    assert stored is not None
    assert stored.access_token == "at-rotated"
    assert stored.refresh_token == _CREDS.refresh_token  # unchanged, fallback
