"""MinIO storage client for rendered MP4s.

A thin wrapper around a singleton `minio.Minio` built from settings. The client
is sync (minio-py is sync); every call site wraps the call in
`asyncio.to_thread` — same pattern as `video_renderer_client._save`.

Unconfigured guard: when `settings.minio_endpoint` is empty, `get_client()`
returns `None`. The write/serve paths (chunks 3/4) check `get_client()` and
fall back to the local-disk path, so MinIO is opt-in via a single env var.
`put_object` is defensive on the same guard (no-ops when unconfigured) so a
caller that forgets the guard cannot crash; `get_object_stream` assumes the
caller checked (the route's fallback catches `S3Error` for the not-found case).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error  # re-exported for callers (the serve route)

from roboco.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_cache: dict[str, Minio | None] = {}


def _reset_client() -> None:
    """Test-only: drop the cached singleton so the next `get_client()` rebuilds."""
    _cache.clear()


def get_client() -> Minio | None:
    """Return the singleton `Minio`, or `None` when MinIO is unconfigured.

    `None` is the disabled path: an empty `settings.minio_endpoint` means the
    write/serve paths fall back to local disk. Settings are load-time, so a
    plain module-level singleton is fine (no runtime-config drift to guard).
    """
    if "c" in _cache:
        return _cache["c"]
    endpoint = settings.minio_endpoint.strip()
    if not endpoint:
        _cache["c"] = None
        return None
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    secure = parsed.scheme == "https"
    host = parsed.netloc or endpoint  # no scheme → use as-is
    client = Minio(
        endpoint=host,
        access_key=settings.minio_access_key or None,
        secret_key=settings.minio_secret_key or None,
        secure=secure,
        region=settings.minio_region or None,
    )
    _cache["c"] = client
    return client


def put_object(data: bytes, key: str) -> None:
    """PUT `data` to `settings.minio_bucket` under `key` as video/mp4.

    No-ops when MinIO is unconfigured (`get_client()` is `None`). The write path
    is guarded by `settings.minio_endpoint` upstream anyway; this is defensive
    so a caller that forgets the guard cannot crash.
    """
    client = get_client()
    if client is None:
        return
    client.put_object(
        bucket_name=settings.minio_bucket,
        object_name=key,
        data=io.BytesIO(data),
        length=len(data),
        content_type="video/mp4",
    )


def stat_object(key: str) -> None:
    """Eager existence/readiness probe — raises if the object is missing or
    MinIO is down, so the serve route can fall back to ``FileResponse`` BEFORE
    starting a ``StreamingResponse`` it can no longer take back.

    ``get_object_stream`` is a lazy generator: its ``client.get_object`` call
    runs on the first ``next()``, i.e. after the route has returned and
    Starlette has started streaming — an ``S3Error`` there is uncatchable. This
    probe runs eagerly inside the route's ``try/except`` so the fallback
    actually fires. No-op when unconfigured (the route checks ``get_client()``
    first; this is defensive).

    ponytail: stat-then-get is two round trips; a mid-stream failure after a
    successful stat is a rare race (object deleted / MinIO blips between the
    two calls) the CEO can retry — accept, or merge into one eager get_object
    returning the open response for a single round trip if preview latency
    ever matters.
    """
    client = get_client()
    if client is None:
        return
    client.stat_object(bucket_name=settings.minio_bucket, object_name=key)


def get_object_stream(key: str) -> Iterator[bytes]:
    """Yield object bytes from `settings.minio_bucket`/`key` for `StreamingResponse`.

    Assumes `get_client()` is not `None` (the route checks first). Lets
    `S3Error` propagate for the not-found / connection-refused case so the
    serve route's `try/except` can fall back to `FileResponse` from disk.
    """
    client = get_client()
    if client is None:  # defensive: caller should have checked
        raise RuntimeError("MinIO client is unconfigured (minio_endpoint empty)")
    response = client.get_object(bucket_name=settings.minio_bucket, object_name=key)
    try:
        yield from response.stream(amt=2**16)
    finally:
        response.close()
        response.release_conn()


__all__ = ["S3Error", "get_client", "get_object_stream", "put_object", "stat_object"]
