"""VideoRenderer coverage: tar/post/save happy path against a mocked httpx
transport, plus unconfigured/unreachable graceful failure (never a crash).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from roboco.config import settings as cfg
from roboco.services import minio_client
from roboco.services.video_renderer_client import (
    NullVideoRenderer,
    VideoRenderer,
    VideoRendererError,
    get_video_renderer,
)


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "motion"
    source.mkdir()
    (source / "Intro.tsx").write_text("export const Intro = () => null;")
    return source


@pytest.mark.asyncio
async def test_render_posts_tar_and_saves_mp4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(cfg, "video_output_dir", str(out_dir))
    monkeypatch.setattr(cfg, "video_request_timeout_seconds", 5.0)
    monkeypatch.setattr(cfg, "video_render_timeout_seconds", 30.0)
    captured: dict[str, bytes | str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, content=b"fake-mp4-bytes")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    path = await renderer.render(
        source_dir=str(source),
        composition_id="Intro",
        input_props={"title": "hello"},
        orientation="vertical",
        render_key="task-99",
    )
    await http_client.aclose()

    assert captured["url"] == "http://fake-video-renderer/render"
    assert str(captured["content_type"]).startswith("multipart/form-data")
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"composition_id" in body
    assert b"Intro" in body
    assert b"vertical" in body
    assert b"motion.tar.gz" in body
    assert b"\x1f\x8b" in body  # gzip magic bytes: the tar payload made it in

    saved = Path(path)
    assert saved.exists()
    assert saved.read_bytes() == b"fake-mp4-bytes"
    assert saved.parent == out_dir
    assert saved.name == "task-99-vertical.mp4"  # task-scoped, not composition-scoped


@pytest.mark.asyncio
async def test_render_non_success_response_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path / "out"))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="render crashed")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    with pytest.raises(VideoRendererError, match="500"):
        await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={},
            orientation="square",
            render_key="t1",
        )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_render_unreachable_sidecar_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path / "out"))

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    with pytest.raises(VideoRendererError, match="render request failed"):
        await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={},
            orientation="square",
            render_key="t1",
        )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_unconfigured_renderer_raises_without_network_call(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    renderer = VideoRenderer(base_url="")
    with pytest.raises(VideoRendererError, match="not configured"):
        await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={},
            orientation="vertical",
            render_key="t1",
        )


@pytest.mark.asyncio
async def test_null_renderer_raises_without_network_call(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    renderer = NullVideoRenderer()
    with pytest.raises(VideoRendererError, match="not configured"):
        await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={},
            orientation="vertical",
            render_key="t1",
        )


@pytest.mark.asyncio
async def test_render_frames_posts_frames_field_and_parses_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(cfg, "video_request_timeout_seconds", 5.0)
    monkeypatch.setattr(cfg, "video_render_timeout_seconds", 30.0)
    captured: dict[str, bytes] = {}

    expected_duration = 12.5

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200,
            content=b"fake-frames-tar-gz",
            headers={"X-Video-Duration": str(expected_duration)},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    tar_bytes, duration = await renderer.render_frames(
        str(source),
        composition_id="Intro",
        input_props={"title": "hello"},
        orientation="vertical",
        frame_count=8,
    )
    await http_client.aclose()

    assert tar_bytes == b"fake-frames-tar-gz"
    assert duration == expected_duration
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'name="frames"' in body
    assert b"8" in body


@pytest.mark.asyncio
async def test_render_frames_missing_duration_header_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(cfg, "video_request_timeout_seconds", 5.0)
    monkeypatch.setattr(cfg, "video_render_timeout_seconds", 30.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-frames-tar-gz")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    tar_bytes, duration = await renderer.render_frames(
        str(source),
        composition_id="Intro",
        input_props={},
        orientation="square",
        frame_count=4,
    )
    await http_client.aclose()

    assert tar_bytes == b"fake-frames-tar-gz"
    assert duration == 0.0


@pytest.mark.asyncio
async def test_render_frames_non_success_response_raises_clear_error(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="frames out of bounds")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    with pytest.raises(VideoRendererError, match="400"):
        await renderer.render_frames(
            str(source),
            composition_id="Intro",
            input_props={},
            orientation="square",
            frame_count=4,
        )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_render_frames_unconfigured_renderer_raises_without_network_call(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    renderer = VideoRenderer(base_url="")
    with pytest.raises(VideoRendererError, match="not configured"):
        await renderer.render_frames(
            str(source),
            composition_id="Intro",
            input_props={},
            orientation="vertical",
            frame_count=4,
        )


@pytest.mark.asyncio
async def test_render_frames_null_renderer_raises_without_network_call(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    renderer = NullVideoRenderer()
    with pytest.raises(VideoRendererError, match="not configured"):
        await renderer.render_frames(
            str(source),
            composition_id="Intro",
            input_props={},
            orientation="vertical",
            frame_count=4,
        )


def test_get_video_renderer_returns_null_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "video_renderer_base_url", "")
    renderer = get_video_renderer()
    assert isinstance(renderer, NullVideoRenderer)


def test_get_video_renderer_returns_real_client_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cfg, "video_renderer_base_url", "http://roboco-video-renderer:3001"
    )
    renderer = get_video_renderer()
    assert isinstance(renderer, VideoRenderer)
    assert not isinstance(renderer, NullVideoRenderer)


@pytest.mark.asyncio
async def test_save_puts_to_minio_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When MinIO is configured, _save PUTs the bytes under the basename key
    and still writes the local mp4 file. Mocks only — no real MinIO."""
    source = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(cfg, "video_output_dir", str(out_dir))
    monkeypatch.setattr(cfg, "video_request_timeout_seconds", 5.0)
    monkeypatch.setattr(cfg, "video_render_timeout_seconds", 30.0)

    minio_client._reset_client()
    # Sentinel client so get_client() returns a non-None (the guard passes).
    monkeypatch.setattr(minio_client, "get_client", object)
    put_calls: list[tuple[bytes, str]] = []

    def fake_put_object(data: bytes, key: str) -> None:
        put_calls.append((data, key))

    monkeypatch.setattr(minio_client, "put_object", fake_put_object)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-mp4-bytes")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    renderer = VideoRenderer(base_url="http://fake-video-renderer", client=http_client)

    try:
        path = await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={"title": "hello"},
            orientation="vertical",
            render_key="task-77",
        )
    finally:
        await http_client.aclose()
        minio_client._reset_client()

    # Local file still written (the poster publish path reads from disk).
    saved = Path(path)
    assert saved.exists()
    assert saved.read_bytes() == b"fake-mp4-bytes"
    assert saved.name == "task-77-vertical.mp4"

    # One PUT, key = the basename, body = the rendered bytes (reused, not re-read).
    assert len(put_calls) == 1
    data, key = put_calls[0]
    assert key == "task-77-vertical.mp4"
    assert data == b"fake-mp4-bytes"


def test_save_writes_temp_then_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_save writes a {path}.mp4.tmp sibling first, then atomically renames onto
    the final path — a re-render can't clobber the MP4 the panel streams mid-read."""
    out_dir = tmp_path / "out"
    monkeypatch.setattr(cfg, "video_output_dir", str(out_dir))
    minio_client._reset_client()
    monkeypatch.setattr(minio_client, "get_client", lambda: None)

    write_targets: list[Path] = []
    replace_calls: list[tuple[Path, Path]] = []
    real_write = Path.write_bytes
    real_replace = Path.replace

    def spy_write(self: Path, data: bytes) -> int:
        write_targets.append(self)
        return real_write(self, data)

    def spy_replace(self: Path, target: Path) -> Path:
        replace_calls.append((self, target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "write_bytes", spy_write)
    monkeypatch.setattr(Path, "replace", spy_replace)

    try:
        path = VideoRenderer._save(
            b"fake-mp4-bytes", render_key="task-atomic", orientation="vertical"
        )
    finally:
        minio_client._reset_client()

    final = Path(path)
    assert final.name == "task-atomic-vertical.mp4"
    assert write_targets, "write_bytes was never called"
    # Wrote to a .tmp sibling first, not the final path directly.
    assert str(write_targets[0]).endswith(".mp4.tmp")
    assert write_targets[0] != final
    # Then atomically renamed onto the final path.
    assert replace_calls, "Path.replace was never called"
    assert replace_calls[0][1] == final
    # Temp is gone; final has the bytes.
    assert not write_targets[0].exists()
    assert final.read_bytes() == b"fake-mp4-bytes"


@pytest.mark.asyncio
async def test_save_swallows_minio_put_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed MinIO PUT (MinIO down, transient 5xx) must not fail the render —
    local disk is the source of truth and the serve route falls back to
    FileResponse on S3Error. _save logs and returns the local path; the local
    file is written. Mocks only."""
    out_dir = tmp_path / "out"
    monkeypatch.setattr(cfg, "video_output_dir", str(out_dir))

    minio_client._reset_client()
    monkeypatch.setattr(minio_client, "get_client", object)  # guard passes

    def failing_put_object(_data: bytes, _key: str) -> None:
        raise RuntimeError("minio unreachable")

    monkeypatch.setattr(minio_client, "put_object", failing_put_object)

    try:
        # _save is a sync @staticmethod; call it directly (no httpx needed).
        path = VideoRenderer._save(
            b"fake-mp4-bytes", render_key="task-88", orientation="square"
        )
    finally:
        minio_client._reset_client()

    saved = Path(path)
    assert saved.exists()
    assert saved.read_bytes() == b"fake-mp4-bytes"
    assert saved.name == "task-88-square.mp4"
