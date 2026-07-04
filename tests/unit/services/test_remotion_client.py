"""RemotionRenderer coverage: tar/post/save happy path against a mocked httpx
transport, plus unconfigured/unreachable graceful failure (never a crash).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from roboco.config import settings as cfg
from roboco.services.remotion_client import (
    NullRemotionRenderer,
    RemotionRenderer,
    RemotionRendererError,
    get_remotion_renderer,
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
    renderer = RemotionRenderer(base_url="http://fake-remotion", client=http_client)

    path = await renderer.render(
        source_dir=str(source),
        composition_id="Intro",
        input_props={"title": "hello"},
        orientation="vertical",
        render_key="task-99",
    )
    await http_client.aclose()

    assert captured["url"] == "http://fake-remotion/render"
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
    renderer = RemotionRenderer(base_url="http://fake-remotion", client=http_client)

    with pytest.raises(RemotionRendererError, match="500"):
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
    renderer = RemotionRenderer(base_url="http://fake-remotion", client=http_client)

    with pytest.raises(RemotionRendererError, match="render request failed"):
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
    renderer = RemotionRenderer(base_url="")
    with pytest.raises(RemotionRendererError, match="not configured"):
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
    renderer = NullRemotionRenderer()
    with pytest.raises(RemotionRendererError, match="not configured"):
        await renderer.render(
            source_dir=str(source),
            composition_id="Intro",
            input_props={},
            orientation="vertical",
            render_key="t1",
        )


def test_get_remotion_renderer_returns_null_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "remotion_base_url", "")
    renderer = get_remotion_renderer()
    assert isinstance(renderer, NullRemotionRenderer)


def test_get_remotion_renderer_returns_real_client_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "remotion_base_url", "http://roboco-remotion:3001")
    renderer = get_remotion_renderer()
    assert isinstance(renderer, RemotionRenderer)
    assert not isinstance(renderer, NullRemotionRenderer)
