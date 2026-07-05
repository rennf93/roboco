"""RemotionRenderer — HTTP client for the remotion-renderer sidecar.

No cross-container shared volume: the orchestrator tars the merged motion/
source directory from its read-clone and POSTs it to the sidecar; the sidecar
renders the composition and returns the MP4 bytes in the HTTP response, which
this client writes to an orchestrator-local directory. The sidecar itself
stays credential-free and git-free — it only ever sees a tarball plus a JSON
side-channel of render parameters.

`NullRemotionRenderer` (an unconfigured `remotion_base_url`) fails the same
clean way an unreachable sidecar does — a `RemotionRendererError`, never a
raw transport crash — mirroring the `NullXClient` graceful-degradation shape.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import httpx
import structlog

from roboco.config import settings
from roboco.services import minio_client

log = structlog.get_logger(__name__)


class RemotionRendererError(Exception):
    """A render call failed: unconfigured sidecar, unreachable, or non-2xx."""


class RemotionRenderer:
    """Tar the composition source, POST it to the sidecar, save the MP4."""

    def __init__(
        self, *, base_url: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def render(
        self,
        *,
        source_dir: str,
        composition_id: str,
        input_props: dict[str, Any],
        orientation: str,
        render_key: str,
    ) -> str:
        """Render one composition/orientation cut; return the saved MP4 path.

        ``render_key`` (the source task id) scopes the output path — a
        composition is reused across videos (the compounding library), so
        keying the file by composition alone would let a later render clobber
        an earlier, not-yet-posted draft's clip.

        Fails fast (no tar, no network attempt) when unconfigured — the same
        guard covers both ``NullRemotionRenderer`` and a directly-constructed
        ``RemotionRenderer(base_url="")``.
        """
        if not self._base_url:
            raise RemotionRendererError(
                "remotion sidecar not configured (remotion_base_url unset)"
            )
        tar_bytes = await asyncio.to_thread(self._tar_source, source_dir)
        mp4_bytes = await self._post(
            tar_bytes,
            composition_id=composition_id,
            input_props=input_props,
            orientation=orientation,
        )
        return await asyncio.to_thread(
            self._save,
            mp4_bytes,
            render_key=render_key,
            orientation=orientation,
        )

    @staticmethod
    def _tar_source(source_dir: str) -> bytes:
        """Tar ``source_dir`` (the motion/ package) into an in-memory gzip archive."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(source_dir, arcname="motion")
        return buf.getvalue()

    async def _http(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    async def _post(
        self,
        tar_bytes: bytes,
        *,
        composition_id: str,
        input_props: dict[str, Any],
        orientation: str,
    ) -> bytes:
        """POST the tarball + render params; return the MP4 response body.

        Timeout is split: connect/write/pool use the short request timeout
        (sending the tar), while `read` gets the long render timeout (the
        sidecar renders before it writes the response body).
        """
        timeout = httpx.Timeout(
            settings.video_request_timeout_seconds,
            read=settings.video_render_timeout_seconds,
        )
        client = await self._http(timeout)
        try:
            response = await client.post(
                f"{self._base_url}/render",
                data={
                    "composition_id": composition_id,
                    "orientation": orientation,
                    "input_props": json.dumps(input_props),
                },
                files={"source": ("motion.tar.gz", tar_bytes, "application/gzip")},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise RemotionRendererError(f"render request failed: {exc}") from exc
        if not response.is_success:
            raise RemotionRendererError(
                f"render failed: HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.content

    @staticmethod
    def _save(mp4_bytes: bytes, *, render_key: str, orientation: str) -> str:
        """Write MP4 bytes under video_output_dir at a task-scoped path.

        Durable copy to MinIO when configured. Local disk stays the source of
        truth for the poster publish path (x_video_client/tiktok_client read
        mp4_path from disk), so this is an additive PUT, not a replacement.
        Key = basename, already ``{render_key}-{orientation}.mp4`` — no
        schema/marker change. ``_save`` is wrapped in ``asyncio.to_thread`` by
        ``render()``, so the sync ``put_object`` call runs in that thread.
        """
        out_dir = Path(settings.video_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{render_key}-{orientation}.mp4"
        path.write_bytes(mp4_bytes)
        if minio_client.get_client() is not None:
            # MinIO is a durable COPY, not the render's source of truth — local
            # disk is. The serve route falls back to FileResponse on S3Error, so
            # a failed PUT (MinIO down, full disk, transient 5xx) must never fail
            # the render or it'd retry-loop a task whose local file is already
            # fine. Log and continue; the next render re-attempts the PUT.
            try:
                minio_client.put_object(mp4_bytes, path.name)
            except Exception as exc:  # durable copy, never fatal to the render
                log.warning(
                    "minio put failed; render kept on local disk",
                    key=path.name,
                    error=str(exc),
                )
        return str(path)


class NullRemotionRenderer(RemotionRenderer):
    """No sidecar configured — inherits render()'s empty-base_url guard, so
    every call raises immediately: no tar, no network call."""

    def __init__(self) -> None:
        super().__init__(base_url="")


def get_remotion_renderer() -> RemotionRenderer:
    """RemotionRenderer bound to settings.remotion_base_url; Null when unset."""
    base_url = settings.remotion_base_url.strip()
    if not base_url:
        return NullRemotionRenderer()
    return RemotionRenderer(base_url=base_url)
