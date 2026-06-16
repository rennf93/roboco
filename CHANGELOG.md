# Changelog

All notable changes to RoboCo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-06-16

### Added

- **Acceptance-criteria & decomposition guardrails.** Every task's acceptance
  criteria now carry stable per-criterion ids, and each decomposed subtask
  records which parent criteria it is responsible for (`covers_parent_criteria`).
  Two gates build on that linkage: a PM can no longer go idle leaving a parent
  criterion with no subtask responsible for it (the decomposition floor), and a
  parent can no longer complete / submit up / escalate to the CEO unless every
  one of its criteria traces to a child that passed QA on it (the roll-up gate).
  PMs see live coverage in their briefings (`parent_ac_coverage`,
  `unclaimed_parent_acs`) after each `delegate`. Safe-by-construction: every gate
  stays inert until a PM starts declaring coverage, so existing decompositions
  are never blocked. (Migration 036.)
- **Per-dev sequenced code queues.** A cell PM now delegates each developer its
  full queue of code subtasks up front instead of one task at a time. Both cell
  developers build in parallel, and each works its own queue one task at a time,
  in order — enforced by a per-lane dispatch barrier, with leaf PRs still merged
  in sequence into the shared cell branch. The old "two code subtasks per parent"
  ceiling is removed; the 12-subtask hard cap and a same-title duplicate guard
  remain.
- **Unified Business page.** The Company Goals, Secretary, and Pitches pages are
  consolidated into one tabbed **Business** page (Goals / Secretary / Pitches),
  modeled on the Knowledge Base page with deep-linkable `?tab=` URLs. A single
  sidebar entry replaces four.

### Changed

- **Company Goals, Secretary, and Pitches brought to the panel's standards.**
  Skeleton loading and offline/error states, structured fields instead of raw
  JSON dumps, required-note confirmation dialogs for pitch and directive
  decisions, and markdown rendering in the Secretary chat.

### Removed

- **The standalone Cockpit page.** Its data duplicated the Dashboard and Metrics;
  its one unique element — the strategy-engine "needs your attention" signals —
  was relocated to the Dashboard, served by a new lightweight
  `GET /api/cockpit/signals` endpoint. The `/cockpit`, `/company-goals`,
  `/secretary`, and `/pitches` panel routes are all retired (404); the Goals,
  Secretary, and Pitches views now live under `/business?tab=…`.

### Fixed

- **Agent MCP/SDK servers no longer stall on spawn.** They launch with
  `uv run --no-sync`, so a workspace clone whose lockfile has drifted from the
  baked image no longer triggers a multi-minute dependency re-sync that left the
  gateway tools stuck "pending" and the developer respawning in a loop.
- **`open_pr` no longer fails on a missing base branch.** `create_pr`
  auto-creates and pushes the PR's base branch off the default branch when it is
  not yet on the remote, instead of returning a GitHub 422.
- **Admin status overrides restore task ownership.** Forcing a blocked task back
  to pending / in_progress now restores its pre-block assignee, so an escalated
  code task no longer re-enters the pool still owned by a PM and is dispatched to
  that PM as if it were a developer.
- **A developer can idle past its own queued work.** With per-dev queues, a dev
  whose current leaf has moved to QA now idles cleanly while its later queue
  items wait their turn (the orchestrator respawns it when the lane clears),
  instead of looping on the idle guard or claiming the next leaf out of order.
- **26 verified panel UI bugs** across the dashboard, kanban, task detail, and
  API layer: consistent priority labels and badge sizing, dark-mode coverage,
  kanban drag-and-drop that prompts for the required audit note, auto-scroll in
  the message and mentor-chat views, corrected WebSocket reconnect counting,
  `PATCH` (not `PUT`) for partial task updates, working "Activate Task" and
  "Start Revision" actions for backlog and needs-revision tasks (no more
  dead-end menus), the previously-dead "New / Generate Report" buttons, a
  duplicate agent id, a "0h ago" timestamp, and more.

### Internal

- Verb-table generation no longer emits tables for the driver-based roles
  (prompter, secretary), whose real tools live in their SDK drivers rather than
  the gateway verb surface; and the `_briefing_for` typed stub was aligned with
  its implementation so the composed choreographer type-checks under full mypy.

## [0.4.0] - 2026-06-15

### Added

- **Business Goals — the company charter.** A single CEO-owned charter (north
  star, prioritized objectives, constraints, operating policy) injected
  compactly into every agent's briefing so all work is goal-aware.
  `GET /api/company-goals` (any agent) / `PUT` (CEO-only), with a panel editor.
- **Web research for the Board and PMs.** Pluggable `web_search` / `web_fetch`
  exposed through a `roboco-search` MCP server backed by `/api/research/*`, with
  Tavily / Brave / Exa adapters and a graceful no-op when no provider is
  configured. The provider key stays server-side — agent containers never make
  the external request themselves — and a per-agent daily quota (Redis,
  fail-open) bounds cost.
- **Pitch → approve → provision.** The Board proposes a product (a "pitch");
  on CEO approval the system provisions a GitHub repo per target cell, registers
  a Project for each (and a Product when multi-cell), and seeds one Main-PM
  delivery task — reusing the existing Product / coordination-task machinery.
  Default-off: with no provisioning token configured, approval is refused and
  nothing is created.
- **Autonomous strategy engine (dormant).** An optional second engine that
  watches the company against its standing goals and surfaces drift, idle, and
  long-stranded blocked work to the CEO (notify-only — it never spends, builds,
  or auto-approves). Off by default; the delivery lifecycle is unchanged.
- **The Secretary — the CEO's chief-of-staff.** A live conversational agent (its
  own role, distinct from the Prompter) the CEO chats with in the panel. It acts
  only under the CEO's command: it reads company state and relays dictated
  messages directly, but high-impact actions — editing the charter, starting /
  cancelling / overriding tasks, approving a pitch, announcements — are queued
  and run only after the CEO's explicit confirmation (the gate list). Its
  authority is HMAC-scoped to the secretary role and routed through the existing
  enforcement, never a parallel permission model.
- **The Cockpit.** A read-only `/cockpit` view answering "is the business
  winning, what's happening, what needs me" — the charter, delivery counts,
  30-day spend vs the budget cap, pending pitches, and the strategy engine's
  signals. Honestly stamped `basis: proxy` (a proxy until real launches).

All of these are additive and opt-in or default-off — an unconfigured deployment
behaves exactly as before.

## [0.3.0] - 2026-06-15

### Added

- **In-house RAG engine.** Replaced the piragi/torch retrieval stack with an
  in-house pgvector engine (asyncpg), then added **hybrid retrieval** —
  pgvector cosine fused with Postgres full-text ranking — retiring HyDE, plus
  an embed-once / concurrent-search pass that cut multi-index query latency.
- **Self-hosted LLM provider** with dynamic model discovery, so agents can run
  against a local or self-hosted model endpoint.
- **Quality gates at the source.** Developers run a fast quality gate at
  `i_am_done` and the full fast gate (including complexity) at their desk; QA
  requires a per-acceptance-criterion verdict before passing; cells run two
  developers in parallel with split-before-claim sizing.
- **Board redraft loop** — the Board can send a drafted task back to intake for
  an in-context re-draft before it starts.
- **Transcript retention** — a background sweep prunes old agent transcripts,
  with a panel-tunable retention window.
- **`tests/` type-gated under mypy** — the whole test suite now type-checks in CI.

### Fixed

- **PR-divergence respawn-loop meltdown.** Capped the PM respawn loop-gate,
  added CEO god-mode status override, a PR-conflict auto-resolver (rebase →
  close-superseded / re-merge / escalate), and sequence-ordered sibling merge;
  the dispatcher can now claim an ownerless `awaiting_pm_review` task without
  transitioning it.
- **Git robustness.** Fall back to a permitted merge method when the repo
  refuses the requested one, and retarget a PR's base to the default branch
  when the resolved base is missing on the remote.
- **RAG outage.** Migrated the live `chunks_*` tables to the in-house schema
  (offline-renderable migration), closed engine audit gaps, decoded jsonb
  metadata returned as a string by asyncpg, and kept the embedding model
  resident to stop ingest timeouts.
- **Panel.** Fixed task lifecycle (updates, merge, reassignment, copy),
  responsive grids + mobile overflow, the status dropdown duplicating the
  current status, the orchestrator-status reachability signal, and surfaced
  the CEO "Approve & Start" gate so it can't be missed.
- **Usage attribution.** Agent transcripts are attributed by an
  orchestrator-assigned session id, fixing zeroed token/cost capture for
  review-role agents.
- Composed the prompter role layer for the intake agent; aligned auditor
  channel permissions; made the app route-registration test robust to FastAPI
  0.137; cleared an xenon complexity failure and fixable test warnings.

### Security

- Documented that WebSocket authentication is REST-only and `/ws/system` is
  unauthenticated.

## [0.2.0] - 2026-06-11

### Added

- **Provider rate-limit handling.** End-to-end backpressure for LLM-provider
  429s: a Redis-backed `RateLimitStateTracker`, a spawn gate that **queues**
  (never drops) work while a provider is rate-limited, agent parking via
  `i_am_blocked(reason="rate_limited")`, and a background probe-and-resume loop
  that auto-revives parked agents when the limit lifts — escalating to the CEO
  after repeated failed probes. Surfaced live in the panel via a rate-limit
  banner.
- **Token usage & cost analytics.** Per-agent-session token capture read from
  the Claude Code transcript (`/usage/sync`), persisted to spawn-session rows
  and daily rollups, with provider-aware pricing (Anthropic models priced;
  local/Ollama models intentionally $0). Visible on the usage dashboard.
- **`/ws/system` operator WebSocket stream** with a `websocket_bridge` that
  forwards system events from the event bus to panel clients in real time — the
  rate-limit lifecycle and live token/cost usage (`USAGE_UPDATE` /
  `USAGE_SNAPSHOT`), so the dashboard's "Token Usage & Cost" panel updates over
  the socket and falls back to HTTP polling when it drops.

### Fixed

- Agent workspaces now install the project's `dev` extra (`uv sync --extra dev`)
  so spawned agents have the full `make quality` toolchain (ruff/mypy/xenon) and
  can gate their own work — closing the gap that let lint/type/complexity debt
  merge unchecked.
- Token-usage capture: the dashboard previously recorded zeros because nothing
  populated the per-session counters.
- Panel rate-limit endpoint shape (`/api/system/rate-limits` returns the
  `{ entries: [...] }` envelope the dashboard expects) and the doubled
  `/ws/ws/system` WebSocket path.
- Control-panel logo and all `/public` assets returning 500 — the panel image
  copied them without chowning to the non-root runtime user.
- Provider-aware pricing (Opus corrected to $5/$25 per 1M; non-Anthropic models
  no longer warn or mis-price).

## [0.1.0] - 2026-06-09

### Added

- Initial public release of **RoboCo** — an open-source AI agent "company": a
  virtual organization of 20 AI agents and 1 human CEO that plans, builds,
  reviews, documents, and ships software.
- Organizational hierarchy: on-demand Intake, Board (Product Owner, Head of
  Marketing, Auditor), Main PM, and Backend / Frontend / UX-UI cells.
- **Task Assistant** (the intake Prompter): a live, codebase-aware chat that
  interviews the CEO and drafts a well-formed, board-ready task — objective,
  per-cell breakdown, and acceptance criteria — then launches it into the
  lifecycle (Board review, or straight to the Main PM).
- Agent gateway (`roboco-flow`, `roboco-do`) backed by the server-side
  Choreographer; intent-verb tool surface per role.
- Task lifecycle state machine with role-based transitions and git workflow
  (PR-before-QA, CEO approval for major work).
- A2A protocol, journals, channels/notifications, kanban, and RAG (piragi +
  pgvector) knowledge base.
- Next.js control panel (`panel/`) behind a single nginx entry point.
- Multi-agent workspace management with per-project encrypted git tokens.

[0.5.0]: https://github.com/rennf93/roboco/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rennf93/roboco/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/rennf93/roboco/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rennf93/roboco/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rennf93/roboco/releases/tag/v0.1.0
