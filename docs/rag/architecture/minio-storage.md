# Object Storage (MinIO)

## What It Is

Rendered MP4s are written to a host bind mount (`ROBOCO_VIDEO_OUTPUT_DIR`, default `/data/video-renders`) and served back via `FileResponse` from the media route. MinIO adds decoupled, docker-managed durable object storage so renders outlive the orchestrator container without relying on the bind-mount sprawl, and gives a clean serve path that keeps app-level auth end-to-end. The client is `minio` (minio-py), sync, with every call site wrapped in `asyncio.to_thread` — same pattern as the existing `_save` in `roboco/services/remotion_client.py`.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_MINIO_ENDPOINT` | `` (empty) | MinIO endpoint, e.g. `http://roboco-minio:9000`. Empty = disabled (the media route falls back to `FileResponse` from the local video-renders dir). |
| `ROBOCO_MINIO_ACCESS_KEY` | `` | Access key. Required when endpoint is set. |
| `ROBOCO_MINIO_SECRET_KEY` | `` | Secret key. Required when endpoint is set. |
| `ROBOCO_MINIO_BUCKET` | `roboco-video-renders` | Bucket for rendered videos. Created idempotently by the `minio-init` one-shot service. |
| `ROBOCO_MINIO_REGION` | `us-east-1` | MinIO region. |

Armed in the NAS compose (`docker-compose.yml` / `docker-compose.yaml`); intentionally omitted from `docker-compose.registry.yml` (NAS default-on, registry default-off).

## Current state (0.19.0 chunk 1 — scaffolding)

Config fields, the `minio` dependency, and the compose services (`minio` + `minio-init`) are landed. With `minio_endpoint` empty, no code path consumes MinIO yet — the existing `FileResponse` media-serve path is byte-for-byte unchanged. The `minio` service runs on the `data` network only (off the agent mesh); the orchestrator reaches it via its `data` NIC. Host ports `19000:9000` / `19001:9001` are published for debugging only.

## Planned end state (later chunks)

- **Write path.** `remotion_client._save` keeps the local write (the poster publish path in `x_video_client.py` / `tiktok_client.py` still reads `Path(mp4_path).read_bytes()`) and adds a MinIO `put_object` after it, guarded by `settings.minio_endpoint`. Key = `Path(mp4_path).name` (already `{render_key}-{orientation}.mp4`) — no schema change, no new marker field.
- **Serve path.** The media route (`roboco/api/routes/video.py`) keeps `_require_ceo(agent)` and returns a `StreamingResponse` wrapping `minio_client.get_object_stream(key)` when configured, falling back to `FileResponse` when unconfigured OR on `S3Error` (old renders not yet in MinIO). The key is the basename, so the existing confinement check stays as defense-in-depth.

## Why not presigned URLs

A presigned URL is a TTL bearer token for the object; the browser's `<video>` element making a direct GET to MinIO cannot carry `X-Agent-ID`/`X-Agent-Role`, so once issued, MinIO cannot enforce the app's CEO-role check — anyone with the URL gets the bytes for the TTL. Proxying through the authenticated route keeps the app-level auth boundary end-to-end at the cost of one loopback streaming hop on a single-host docker deploy. Presigned URLs are deferred until CDN / direct-browser-to-MinIO becomes a goal and panel auth is reworked to mint short-lived tokens.

## Skipped (add when)

- Presigned URLs / `presign_ttl_seconds` — see above.
- Storage interface / factory — one implementation, no abstraction.
- Full MinIO-only switch (drop local disk) — when the publish path takes bytes instead of a path.
- Lifecycle policy / bucket versioning / replication — when there's a retention or multi-site requirement.