# Settings

The Settings page (`/settings`) holds two things that genuinely change how the company runs — the **Feature Flags** card and **Transcript Retention** — alongside a handful of cosmetic preferences and read-only connection info. Read the warning below before you trust a switch on this page.

## Feature Flags

The Feature Flags card is the operator's master switchboard for the optional, default-off subsystems. Instead of hand-editing environment variables, you flip a switch here. Each row shows the subsystem's label, a one-line description of what it gates, and a toggle.

| Flag | Gates |
|------|-------|
| External PR review | Discovering and reviewing inbound external/fork PRs. |
| Internal PR review | The read-only safety reviewer on internal branch PRs. |
| Web research | Letting the Board and PMs run web research. |
| Strategy engine | Generating and maintaining strategy artifacts (drives the Command Center's Strategy Signals). |
| Self-healing | Watching RoboCo's own CI and notifying you on a regression. |
| Self-heal originate | Also opening a *pending* fix task for a regression — needs self-healing on, and the task waits for your approval. |
| Pitch provisioning | Auto-provisioning projects from approved [pitches](./business.md). |
| Toolchain match | Provisioning each agent workspace with the target project's own Python and blocking gates when its tests can't run. |
| Conventions | Enforcing the per-project architectural standard (`.roboco/conventions.yml`). |
| RAG auto-update | Keeping the [knowledge base](./knowledge-base.md) index refreshed automatically. |
| Transcript prune | Running the background sweep that prunes old transcripts. |

Each subsystem has a full page in the [optional subsystems section](../optional/index.md) — what it does, the exact `ROBOCO_*` env var behind it, and what turning it on changes.

!!! warning "Flags take effect on the next backend restart"
    Toggling a flag persists the choice server-side, but it does **not** hot-reload — the backend reads it at startup. The toast says as much: "takes effect on next restart." A flag you've never set falls back to its environment / config default. So: flip it here, then restart the orchestrator for it to take hold.

## Transcript Retention

The Transcript Retention card sets how many days agent transcripts are kept before the prune sweep removes them (default 14). This is a real, server-persisted setting. It only ever prunes *agent-owned* transcripts — never your own Claude sessions. The sweep itself is gated by the **Transcript prune** feature flag above.

## Connection Info (read-only)

This card displays the API and WebSocket base URLs the panel is using (`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`) — relative by default so nginx dispatches them on a single origin. It's informational; you can't edit it here.

## The cosmetic and mock-state controls

!!! danger "Some switches on this page do nothing"
    The page also carries **Appearance** (theme, collapsed sidebar — these work and persist locally), a **User Info** card, and a **Data & Refresh** + **Notifications** block. The Auto Refresh, Refresh Interval, Enable Notifications, and Sound Alerts controls — and the page-level **Save Settings** button — are **local component state only.** They are *not* persisted or wired to anything; Save just shows a success toast. Don't rely on them to change runtime behavior. Only **Feature Flags**, **Transcript Retention**, and the **Appearance** preferences actually do something.

## AI provider configuration lives elsewhere

Choosing which model and provider backs each agent is **not** on this page — it's the **AI Providers** page (`/settings/ai-providers`), linked from the sidebar footer. There you set the global routing mode (Anthropic / Grok / Ollama / self-hosted), or pin individual agents in Mix mode, and store the encrypted provider keys. See [Provider routing](../models/provider-routing.md).

## Next

→ [Optional subsystems](../optional/index.md) for every feature flag in detail · [Provider routing](../models/provider-routing.md) for the AI Providers page · [Environment reference](../deploy/env-reference.md) for the `ROBOCO_*` variables behind the flags.
