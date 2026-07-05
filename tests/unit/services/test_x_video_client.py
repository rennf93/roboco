"""LiveXVideoPoster coverage: the v2 media-upload sequence (initialize ->
append -> finalize -> STATUS poll -> tweet) against a fake httpx transport,
plus the Null/Live build branching."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from roboco.services.video_post_service import NullXVideoPoster
from roboco.services.x_credentials import XCredentialsData
from roboco.services.x_video_client import (
    LiveXVideoPoster,
    build_x_video_poster,
)

if TYPE_CHECKING:
    from pathlib import Path

_CREDS = XCredentialsData(
    api_key="ak-test",
    api_secret="as-test",
    access_token="at-test",
    access_token_secret="ats-test",
)
THREE = 3


def test_build_without_creds_returns_null() -> None:
    poster = build_x_video_poster(None, timeout=5.0)
    assert isinstance(poster, NullXVideoPoster)
    assert poster.configured is False


def test_build_with_creds_returns_live() -> None:
    poster = build_x_video_poster(_CREDS, timeout=5.0)
    assert isinstance(poster, LiveXVideoPoster)
    assert poster.configured is True


@pytest.mark.asyncio
async def test_null_poster_post_video_is_a_noop() -> None:
    result = await NullXVideoPoster().post_video(mp4_path="/tmp/x.mp4", caption="hi")
    assert result.posted is False
    assert result.video_id is None


def _write_clip(tmp_path: Path, data: bytes = b"fake-mp4-bytes") -> str:
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(data)
    return str(mp4)


@pytest.mark.asyncio
async def test_post_video_full_sequence_with_processing_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole verified §11.2 sequence: initialize -> append -> finalize
    (pending) -> GET STATUS (succeeded) -> POST /2/tweets."""
    calls: list[tuple[str, str]] = []

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("roboco.services.x_video_client.asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"].startswith("OAuth ")
        path = request.url.path
        if path == "/2/media/upload/initialize":
            return httpx.Response(
                202, json={"data": {"id": "media123", "media_key": "3_123"}}
            )
        if path == "/2/media/upload/media123/append":
            return httpx.Response(204)
        if path == "/2/media/upload/media123/finalize":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "media123",
                        "processing_info": {"state": "pending", "check_after_secs": 1},
                    }
                },
            )
        if path == "/2/media/upload" and request.url.params.get("command") == "STATUS":
            assert request.url.params.get("media_id") == "media123"
            return httpx.Response(
                200, json={"data": {"processing_info": {"state": "succeeded"}}}
            )
        if path == "/2/tweets":
            return httpx.Response(201, json={"data": {"id": "tweet789"}})
        raise AssertionError(f"unexpected request {request.method} {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveXVideoPoster(_CREDS, timeout=5.0, client=http_client)
    result = await poster.post_video(
        mp4_path=_write_clip(tmp_path), caption="Check this out"
    )
    await http_client.aclose()

    assert result.posted is True
    assert result.video_id == "tweet789"
    paths = [p for _, p in calls]
    assert paths.count("/2/media/upload/initialize") == 1
    assert paths.count("/2/media/upload/media123/append") == 1
    assert paths.count("/2/media/upload/media123/finalize") == 1
    assert paths.count("/2/media/upload") == 1  # the STATUS poll
    assert paths.count("/2/tweets") == 1


@pytest.mark.asyncio
async def test_post_video_skips_poll_when_finalize_has_no_processing_info(
    tmp_path: Path,
) -> None:
    """A finalize response with no processing_info means already usable —
    no STATUS poll is issued at all."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        path = request.url.path
        if path == "/2/media/upload/initialize":
            return httpx.Response(202, json={"data": {"id": "media1"}})
        if path == "/2/media/upload/media1/append":
            return httpx.Response(204)
        if path == "/2/media/upload/media1/finalize":
            return httpx.Response(201, json={"data": {"id": "media1"}})
        if path == "/2/tweets":
            return httpx.Response(201, json={"data": {"id": "tweet1"}})
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveXVideoPoster(_CREDS, timeout=5.0, client=http_client)
    result = await poster.post_video(mp4_path=_write_clip(tmp_path), caption="hi")
    await http_client.aclose()

    assert result.posted is True
    assert "/2/media/upload" not in calls  # STATUS GET never fired


@pytest.mark.asyncio
async def test_post_video_appends_in_multiple_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file bigger than the chunk size is appended across several calls
    (shrink the module chunk size rather than allocating real megabytes)."""
    monkeypatch.setattr("roboco.services.x_video_client._CHUNK_SIZE_BYTES", 4)
    append_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/2/media/upload/initialize":
            return httpx.Response(202, json={"data": {"id": "media1"}})
        if path == "/2/media/upload/media1/append":
            append_calls.append(str(request.content))
            return httpx.Response(204)
        if path == "/2/media/upload/media1/finalize":
            return httpx.Response(201, json={"data": {"id": "media1"}})
        if path == "/2/tweets":
            return httpx.Response(201, json={"data": {"id": "tweet1"}})
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveXVideoPoster(_CREDS, timeout=5.0, client=http_client)
    result = await poster.post_video(
        mp4_path=_write_clip(tmp_path, b"0123456789"), caption="hi"
    )
    await http_client.aclose()

    assert result.posted is True
    assert len(append_calls) == THREE  # ceil(10 / 4) == 3 chunks


@pytest.mark.asyncio
async def test_post_video_initialize_failure_is_graceful(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveXVideoPoster(_CREDS, timeout=5.0, client=http_client)
    result = await poster.post_video(mp4_path=_write_clip(tmp_path), caption="hi")
    await http_client.aclose()

    assert result.posted is False
    assert result.video_id is None
    assert "401" in result.detail


@pytest.mark.asyncio
async def test_post_video_processing_failed_state_is_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("roboco.services.x_video_client.asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/2/media/upload/initialize":
            return httpx.Response(202, json={"data": {"id": "media1"}})
        if path == "/2/media/upload/media1/append":
            return httpx.Response(204)
        if path == "/2/media/upload/media1/finalize":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "processing_info": {
                            "state": "pending",
                            "check_after_secs": 0,
                        }
                    }
                },
            )
        if path == "/2/media/upload" and request.url.params.get("command") == "STATUS":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "processing_info": {
                            "state": "failed",
                            "error": {"message": "invalid video format"},
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected request {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    poster = LiveXVideoPoster(_CREDS, timeout=5.0, client=http_client)
    result = await poster.post_video(mp4_path=_write_clip(tmp_path), caption="hi")
    await http_client.aclose()

    assert result.posted is False
    assert "processing failed" in result.detail
