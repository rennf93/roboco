# X (Twitter) Engine

## What It Is

RoboCo can draft posts for the company's X (Twitter) account — release announcements and mention replies — implemented in `roboco/services/x_engine.py` (`XEngine`) and `roboco/services/x_post_service.py` (`XPostService`). Every draft is HELD for an explicit, per-post CEO approval; nothing is ever posted automatically. It mirrors the `ReleaseManagerEngine` "detect → originate a CEO-gated artifact → hold" shape.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_X_ENGINE_ENABLED` | `false` | Master switch. Off = no draft is originated and no X API call is ever made — the release-post hook and the mentions poll are both no-ops. Panel-toggleable (Settings → Feature Flags). |

Even when enabled, the engine only drafts once stored credentials are present AND acts only through the CEO's explicit per-post approval — two independent gates beyond the flag.

## Draft sources

Six X draft sources share one held-draft pipeline. Each sets a `source` string on the task and carries a source-specific reference marker on `orchestration_markers` so the panel can render the right context line and the redraft flow can preserve it across CEO rejections.

**Release posts** (`X_POST_SOURCE`, event-driven). `XEngine.draft_release_post(version, highlights)` is called from `ReleaseProposalService.approve()`'s publish-success branch — a release post is only ever drafted for a release that actually shipped. Dedup by version: a retry never drafts twice for the same `version`. Marker: `x_release_version`.

**Mention replies** (`X_REPLY_SOURCE`, periodic poll). `XEngine.run_cycle()` fetches mentions via the X API, filters for "meaningful" ones (not a bare retweet, real text, and an engagement floor — `like + reply + retweet counts >= ROBOCO_X_MENTIONS_MIN_ENGAGEMENT`), and dedups against `x_seen_mentions` (migration `059`) so a mention is never turned into a second held reply. Marker: `x_mention_ref` (`{id, text}`).

**Feature spotlights** (`X_FEATURE_SOURCE`, Board Program). `XEngine.materialize_feature_spotlight` is called from the Head-of-Marketing-only `propose_feature_spotlight` content verb after a one-shot exploration spawn investigates an under-publicized shipped feature. Marker: `x_feature_ref` (`{slug, title}`).

**Editorial posts** (`X_EDITORIAL_SOURCE`, Megaphone Board Program). `XEngine.materialize_editorial_post` is called from the `propose_editorial_post` content verb. The marker carries the editorial angle and rationale so the panel renders the angle-guidance line and the redraft prompt includes the editorial context. Marker: `x_editorial_ref` (`{angle, rationale}`).

**Campaign posts** (`X_CAMPAIGN_SOURCE`, War Room Board Program). `XEngine.materialize_campaign_post` is called once per post (in ascending `sequence` order) from the `propose_campaign` content verb. The marker carries the campaign/stage/schedule context. `publish_after` is guidance only — rendered in the panel, never auto-scheduled. Marker: `x_campaign_ref` (`{campaign_name, stage_label, publish_after, sequence}`).

**Barfly replies** (`X_BARFLY_SOURCE`, Barfly Board Program). `XEngine.materialize_barfly_reply` is called from the `propose_conversation_replies` content verb. Materialized as standalone link-posts (commentary + the conversation URL), never threaded replies. Marker: `barfly_reply_ref` (`{tweet_id, text}`).

All sources are bounded by `ROBOCO_X_MAX_OPEN_POSTS` (rolling cap on concurrently-open held drafts, all sources combined); the mentions poll additionally by `ROBOCO_X_MENTIONS_MAX_PER_CYCLE` (per-cycle origination cap).

## Drafting

Draft bodies are written by a **local-model** chat call (`_chat`, hitting `ROBOCO_LOCAL_LLM_BASE_URL` — never a cloud LLM in the hot path), in a full Head-of-Marketing voice prompt (the reasoning-backed VOICE GUIDE plus a banned-word/AI-slop list and style exemplars — no em dashes, no "game-changer"/"seamless"/etc., no exclamation pileups, aimed well under 240 characters so the 280 clamp never truncates mid-sentence), then hard-clamped to 280 characters (`_clamp_tweet`). A release-post local-model failure still falls back to a plain template body (a release announcement always has something real to say); a failed reply draft instead skips origination entirely rather than shipping a generic "Thanks for the mention!". Drafting is **not** an agent spawn — no agent (including Head of Marketing) is spawned to write these; see `docs/rag/roles/head-marketing.md` for why the HoM's tool surface doesn't change.

## IMPACT BAR

Voice alone (no em dashes, no slop words, no exclamation pileups) is necessary but not sufficient: a clean post can still be a topic announcement nobody acts on. The IMPACT BAR is a second, titled section appended beside the voice/slop rules, distilled from a real 5.05M-view X post (@XOpenSource's "Open-sourcing the For You timeline", https://x.com/XOpenSource/status/2087951962004230428), that requires the deliverable noun in the first sentence, one falsifiable specific, demonstration over claim, at most one canonical verifiable link near the close, naming any neutral verification, zero engagement-bait, closing on substance or an invitation, naming the cadence for a standing practice, and leading with the reader's payoff. It is authored once, then carried at two chokepoints that must stay in lockstep: `_HOM_VOICE_GUIDE` in `roboco/services/x_engine.py` (the local-model release/reply/revision prompts covered in [Drafting](#drafting) above) and the VOICE GUIDE section of `agents/prompts/identities/head-marketing.md` (every cloud-agent Board Program spawn that drafts a post: feature spotlight, Megaphone, War Room, Barfly). `tests/unit/services/test_x_engine.py`'s `test_impact_bar_parity_with_head_marketing_md` pins a shared set of sentinel phrases in both copies so a one-sided edit fails the test instead of silently drifting.

## Ownership and the CEO gate

Every held draft is a real task: `team=main_pm`, `assigned_to=secretary-1`, `source` one of `X_POST_SOURCE` / `X_REPLY_SOURCE` / `X_FEATURE_SOURCE` / `X_EDITORIAL_SOURCE` / `X_CAMPAIGN_SOURCE` / `X_BARFLY_SOURCE`, `confirmed_by_human=False` (HELD — skipped by every dispatcher, never delivered to an agent). The body lives on an `orchestration_markers` marker, editable up to the point of posting.

The CEO acts through panel-only REST, CEO-role-gated (`require_ceo_role`), never a gateway verb:

| Endpoint | Effect |
|----------|--------|
| `GET /api/x/posts` | List every held draft (both sources) awaiting decision. |
| `POST /api/x/posts/{task_id}/approve` | Post to X (optionally with an edited body, still 280-char clamped). Idempotent — approving an already-posted draft returns `already_posted` without a second API call. |
| `POST /api/x/posts/{task_id}/reject` | Cancel the draft with a reason. The cancelled draft itself is never posted — `approve` refuses a CANCELLED draft outright (returns `already_rejected` without calling the X API), so a stale approve can't resurrect it. A non-blank reason also schedules a redraft: the same source is revised by the local model with the CEO's feedback folded in as guidance, and one fresh held draft appears in the queue — the feedback loop closes immediately rather than waiting on the next cycle/release. A local-model failure or empty revision originates nothing (no degraded copy). See [Redraft from rejection](#redraft-from-rejection) below for how source-specific markers are preserved. |
| `GET /api/x/credentials` | Whether all four OAuth secrets are stored (`has_credentials` boolean — never the secrets). |
| `POST /api/x/credentials` | Set (or, passing all four empty, clear) the four secrets. All-or-nothing — a partial set raises a validation error. |

Approval runs under a Redis single-flight lock (`roboco:x_post:{task_id}`, plain `SET NX`, 60s TTL) so a double-click can't double-post; the task is marked `COMPLETED` under the same lock before it releases.

## Redraft from rejection

When the CEO rejects a draft with a non-blank reason, `XEngine.redraft_from_rejection` originates a fresh held draft of the **same source** with the rejected body revised by the local model (CEO's reason folded in as guidance). Three things must survive the rejection-to-redraft transition so the redraft renders identically in the panel and the revision prompt carries the right context:

1. **Identity** — the source-specific discriminator used for dedup and per-identity locking (e.g. release version for `x_post`, angle for `x_editorial`, `campaign_name:sequence` for `x_campaign`).
2. **Context** — a one-line factual grounding folded into the revision prompt so the redraft doesn't drift from what the post is about.
3. **Marker** — the source-specific reference marker copied onto the new task so the panel renders the right angle/campaign-guidance/feature line.

All three are **dict-dispatched** at module level rather than grown as if/elif chains in the methods, so a new X source registers by adding one function + one dict entry to each table (not another branch). The three registration tables in `x_engine.py`:

| Table | Purpose | Entries |
|-------|---------|---------|
| `_REDRAFT_IDENTITY_EXTRACTORS` | `(source, key)` tuple for dedup + Redis lock scoping | All six X sources |
| `_REDRAFT_CONTEXT_BUILDERS` | Source-specific context string for the revision prompt | All six X sources |
| `_REDRAFT_MARKER_CARRIERS` | Copy the rejected draft's marker onto the redraft | All six X sources |

`XEngine._carry_redraft_markers` looks up the carrier for the rejected task's `source` and calls it to copy the marker onto the new task. The editorial carrier copies `x_editorial_ref` (`{angle, rationale}`); the campaign carrier copies `x_campaign_ref` (`{campaign_name, stage_label, publish_after, sequence}`). The feature source's seen-slug bookkeeping is NOT repeated — the original draft already marked it seen at authoring time.

A redraft is deduped by identity: while one redraft is already open for the same underlying item (matched by identity tuple), a repeated reject doesn't stack drafts. Once that redraft is itself rejected (CANCELLED, excluded from `list_open_x_posts`), a further redraft is allowed. The check+originate runs under a per-identity Redis lock so two deferred closures racing for the same identity can't both originate.

## Credentials and signing

Credentials are entered in the **panel only** — never in `.env` or an agent-visible setting. The four OAuth 1.0a user-context secrets (`api_key`, `api_secret`, `access_token`, `access_token_secret`) are Fernet-encrypted (`ROBOCO_ENCRYPTION_KEY`) in the singleton `x_credentials` table (migration `059`); `get_decrypted()` is called only server-side, by `x_post_service` / `x_engine` — the API surface is write-only (`has_credentials` boolean, matching the `has_git_token` pattern for per-project git tokens).

Requests are signed with a **hand-rolled OAuth 1.0a HMAC-SHA1** signer (`roboco/services/x_client.py`, no new dependency) — no library does this signing for X's v2 API in the project's existing dependency set. Without credentials, `build_x_client` returns a `NullXClient` that never raises and never egresses, exactly like the research `NullProvider`.

## Related

- `docs/backend/api/x-post-response-schemas.md` — the `XPostResponse` / `XPostHistoryResponse` API schemas and their source-specific ref fields (mention, feature, campaign, editorial, barfly)
- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/board-programs.md` — Megaphone (editorial), War Room (campaign), Barfly, and Spotlight Board Programs that originate the non-release, non-mention draft sources
- `docs/rag/roles/head-marketing.md` — why the HoM's tool surface is unchanged
- `docs/rag/roles/ceo.md` — the CEO approval queues
