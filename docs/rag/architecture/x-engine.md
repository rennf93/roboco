# X (Twitter) Engine

## What It Is

RoboCo can draft posts for the company's X (Twitter) account — release announcements and mention replies — implemented in `roboco/services/x_engine.py` (`XEngine`) and `roboco/services/x_post_service.py` (`XPostService`). Every draft is HELD for an explicit, per-post CEO approval; nothing is ever posted automatically. It mirrors the `ReleaseManagerEngine` "detect → originate a CEO-gated artifact → hold" shape.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_X_ENGINE_ENABLED` | `false` | Master switch. Off = no draft is originated and no X API call is ever made — the release-post hook and the mentions poll are both no-ops. Panel-toggleable (Settings → Feature Flags). |

Even when enabled, the engine only drafts once stored credentials are present AND acts only through the CEO's explicit per-post approval — two independent gates beyond the flag.

## Two draft sources

**Release posts** (event-driven). `XEngine.draft_release_post(version, highlights)` is called from `ReleaseProposalService.approve()`'s publish-success branch — a release post is only ever drafted for a release that actually shipped. Dedup by version: a retry never drafts twice for the same `version`.

**Mention replies** (periodic poll). `XEngine.run_cycle()` fetches mentions via the X API, filters for "meaningful" ones (not a bare retweet, real text, and an engagement floor — `like + reply + retweet counts >= ROBOCO_X_MENTIONS_MIN_ENGAGEMENT`), and dedups against `x_seen_mentions` (migration `059`) so a mention is never turned into a second held reply.

Both are bounded by `ROBOCO_X_MAX_OPEN_POSTS` (rolling cap on concurrently-open held drafts, both sources combined) and the mentions poll additionally by `ROBOCO_X_MENTIONS_MAX_PER_CYCLE` (per-cycle origination cap).

## Drafting

Draft bodies are written by a **local-model** chat call (`_chat`, hitting `ROBOCO_LOCAL_LLM_BASE_URL` — never a cloud LLM in the hot path), in a full Head-of-Marketing voice prompt (the reasoning-backed VOICE GUIDE plus a banned-word/AI-slop list and style exemplars — no em dashes, no "game-changer"/"seamless"/etc., no exclamation pileups, aimed well under 240 characters so the 280 clamp never truncates mid-sentence), then hard-clamped to 280 characters (`_clamp_tweet`). A release-post local-model failure still falls back to a plain template body (a release announcement always has something real to say); a failed reply draft instead skips origination entirely rather than shipping a generic "Thanks for the mention!". Drafting is **not** an agent spawn — no agent (including Head of Marketing) is spawned to write these; see `docs/rag/roles/head-marketing.md` for why the HoM's tool surface doesn't change.

## Ownership and the CEO gate

Every held draft is a real task: `team=main_pm`, `assigned_to=secretary-1`, `source` one of `X_POST_SOURCE` / `X_REPLY_SOURCE`, `confirmed_by_human=False` (HELD — skipped by every dispatcher, never delivered to an agent). The body lives on an `orchestration_markers` marker, editable up to the point of posting.

The CEO acts through panel-only REST, CEO-role-gated (`require_ceo_role`), never a gateway verb:

| Endpoint | Effect |
|----------|--------|
| `GET /api/x/posts` | List every held draft (both sources) awaiting decision. |
| `POST /api/x/posts/{task_id}/approve` | Post to X (optionally with an edited body, still 280-char clamped). Idempotent — approving an already-posted draft returns `already_posted` without a second API call. |
| `POST /api/x/posts/{task_id}/reject` | Cancel the draft with a reason. The cancelled draft itself is never posted — `approve` refuses a CANCELLED draft outright (returns `already_rejected` without calling the X API), so a stale approve can't resurrect it. A non-blank reason also schedules a redraft: the same source (release/reply/spotlight) is revised by the local model with the CEO's feedback folded in as guidance, and one fresh held draft appears in the queue — the feedback loop closes immediately rather than waiting on the next cycle/release. A local-model failure or empty revision originates nothing (no degraded copy). |
| `GET /api/x/credentials` | Whether all four OAuth secrets are stored (`has_credentials` boolean — never the secrets). |
| `POST /api/x/credentials` | Set (or, passing all four empty, clear) the four secrets. All-or-nothing — a partial set raises a validation error. |

Approval runs under a Redis single-flight lock (`roboco:x_post:{task_id}`, plain `SET NX`, 60s TTL) so a double-click can't double-post; the task is marked `COMPLETED` under the same lock before it releases.

## Credentials and signing

Credentials are entered in the **panel only** — never in `.env` or an agent-visible setting. The four OAuth 1.0a user-context secrets (`api_key`, `api_secret`, `access_token`, `access_token_secret`) are Fernet-encrypted (`ROBOCO_ENCRYPTION_KEY`) in the singleton `x_credentials` table (migration `059`); `get_decrypted()` is called only server-side, by `x_post_service` / `x_engine` — the API surface is write-only (`has_credentials` boolean, matching the `has_git_token` pattern for per-project git tokens).

Requests are signed with a **hand-rolled OAuth 1.0a HMAC-SHA1** signer (`roboco/services/x_client.py`, no new dependency) — no library does this signing for X's v2 API in the project's existing dependency set. Without credentials, `build_x_client` returns a `NullXClient` that never raises and never egresses, exactly like the research `NullProvider`.

## Related

- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/roles/head-marketing.md` — why the HoM's tool surface is unchanged
- `docs/rag/roles/ceo.md` — the CEO approval queues
