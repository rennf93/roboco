# Video Engine (Remotion)

## What It Is

RoboCo can author and post short marketing videos for the company's X (Twitter) and TikTok accounts — a release announcement, a feature spotlight, or an on-demand CEO request — implemented in `roboco/services/video_engine.py` (`VideoEngine`), `roboco/services/video_post_service.py` (`VideoPostService`), and the `motion/` composition package, rendered by a credential-free `remotion-renderer` sidecar. Every clip is HELD for an explicit, per-clip CEO approval; nothing is ever posted automatically. It mirrors the `XEngine` "detect → originate a CEO-gated artifact → hold" shape, extended with a render pass between authoring and the held draft.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_VIDEO_ENGINE_ENABLED` | `false` | Master switch. Off = no video-authoring task is ever opened and no render/post happens. Panel-toggleable (Settings → Feature Flags). |
| `ROBOCO_VIDEO_ON_RELEASE` | `false` | Sub-switch: open an authoring task when a release publishes. Off even with the master switch on. |
| `ROBOCO_VIDEO_ON_SPOTLIGHT` | `false` | Sub-switch: open an authoring task when the CEO approves a feature-spotlight draft that requests one. Off even with the master switch on. |

Even when enabled, distribution requires an explicit per-clip CEO approval — a second independent gate beyond the flag.

## Three trigger sources

**Release videos** (event-driven). A release publish fires the video-on-release trigger — gated by `ROBOCO_VIDEO_ON_RELEASE` independently of the master switch.

**Feature-spotlight videos** (event-driven). The CEO's approval of a feature-spotlight draft that requests a video fires the video-on-spotlight trigger — gated by `ROBOCO_VIDEO_ON_SPOTLIGHT`.

**On-demand CEO request** (panel-triggered). `POST /api/video/request` (CEO-role-gated) opens an authoring task directly — the CEO's escape hatch independent of the two automatic triggers.

All three open a normal, **assigned** UX/UI authoring task (balanced across the two ux-devs) rather than a held draft — the dev builds a Remotion composition under `motion/` and proposes its composition id + per-platform captions via the team-gated `propose_video` do-tool, then ships it through the standard commit/PR/QA/doc/review lifecycle.

## Render loop and the sidecar

Once the authoring task completes, an orchestrator render loop (`_video_render_loop`, interval `ROBOCO_VIDEO_RENDER_INTERVAL_SECONDS`) tars the merged `motion/` source and POSTs it to the `remotion-renderer` sidecar (`ROBOCO_REMOTION_BASE_URL`). The sidecar is credential-free and git-free — it reads only what it's POSTed, bundles the composition, renders both the 9:16 and 1:1 MP4 cuts (`ROBOCO_VIDEO_RENDER_TIMEOUT_SECONDS` per render), and streams the bytes back. The orchestrator writes the MP4s to `ROBOCO_VIDEO_OUTPUT_DIR` (bind-mounted in all three compose files so renders survive container recreation) — the sidecar never writes to disk directly.

## Ownership and the CEO gate

The render pass materializes a held `video_post` draft — mirroring the X-post/release-proposal shape: `team=main_pm`, `assigned_to=secretary-1`, `confirmed_by_human=False` (HELD — skipped by every dispatcher, never delivered to an agent). The CEO previews (an axios-blob fetch that carries `X-Agent-ID`/`X-Agent-Role`), edits, approves, or rejects each draft in a new panel video queue.

| Endpoint | Effect |
|----------|--------|
| `GET /api/video/posts` | List every held video draft awaiting decision. |
| `POST /api/video/posts/{task_id}/approve` | Post the rendered clip to X (native video, v2 media upload) and/or TikTok (inbox upload). Idempotent — approving an already-posted draft is a no-op. |
| `POST /api/video/posts/{task_id}/reject` | Cancel the draft with a reason. Terminal. |
| `POST /api/video/request` | Open an on-demand authoring task (CEO-only). |

Approval runs under a Redis heartbeat-renewed lock so a double-click can't double-post; the task is marked `COMPLETED` under the same lock before it releases.

## Credentials

TikTok's OAuth2 secrets are Fernet-encrypted (`ROBOCO_ENCRYPTION_KEY`) alongside the existing X credentials, entered in the panel only — never in `.env` or an agent-visible setting. Every unconfigured leg (renderer, X, TikTok) degrades to a graceful no-op rather than a crash — the same graceful-null pattern as the X `NullXClient`.

## Media route confinement

The panel's media fetch for video previews is confined to a single media route that carries the agent-identity headers and streams bytes from `ROBOCO_VIDEO_OUTPUT_DIR` — it cannot traverse the filesystem.

## Related

- `docs/rag/architecture/x-engine.md` — the held-draft marketing engine this mirrors and extends
- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/roles/ceo.md` — the CEO approval queues