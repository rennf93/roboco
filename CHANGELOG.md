# Changelog

All notable changes to RoboCo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Released]

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
  forwards system events (the rate-limit lifecycle) from the event bus to panel
  clients in real time.

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

[Released]: https://github.com/rennf93/roboco/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rennf93/roboco/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rennf93/roboco/releases/tag/v0.1.0
