# Strategy Engine

The strategy engine is a background loop that watches the company against its charter and tells you when something has drifted — and that is all it does. It never spends, never builds, never approves, never merges. It only sends you a notification. Think of it as a quiet second pair of eyes on the whole org while you are not looking at the panel.

## Default state

The strategy engine is **off by default**, gated by `ROBOCO_STRATEGY_ENGINE_ENABLED` (config default `false`). When off, the background loop does not start at all — there is no cost, no assessment pass, and the delivery lifecycle is untouched. This is a genuinely dormant subsystem until you opt in.

## Enable it

1. Set `ROBOCO_STRATEGY_ENGINE_ENABLED=true` in your environment, or flip **Settings → Feature Flags → "Generate and maintain company strategy artifacts"** on.
2. **Restart the backend** — the change takes effect on the next restart.

That's the whole setup. There is no provider key, no migration, no extra service to wire up.

## What changes when it's on

A background loop wakes on an interval and runs an assessment pass against your charter (the north star, objectives, and operating policy you set in **Business → Goals**). It emits two kinds of observation and notifies the CEO when it sees one:

| Signal | What it means |
|--------|---------------|
| `idle` | There is no in-flight work, but a charter exists — the company is sitting still while it has stated objectives. |
| `stranded_blocked` | A task has been blocked longer than your threshold and needs a human decision to move. |

Notifications are bounded and deduped: you get one notification per observation kind, and it is suppressed until you acknowledge it, so the engine can't spam you about the same standing condition every cycle. The signals also surface on the dashboard's strategy-signals panel and feed the Company Scorecard.

!!! note "Notify-only by design"
    The engine has no authority. It cannot start a task, unblock work, approve a pitch, or spend a token. Every action it might prompt is still yours to take. That is the deliberate contract — it is a steering aid, not an autonomous operator.

## Tuning the cadence and threshold

| Env var | Default | Purpose |
|---------|---------|---------|
| `ROBOCO_STRATEGY_ENGINE_ENABLED` | `false` | Master switch. Off → the loop never runs. |
| `ROBOCO_STRATEGY_ENGINE_INTERVAL_SECONDS` | `1800` | Seconds between assessment passes (minimum 60). |
| `ROBOCO_STRATEGY_STRANDED_BLOCKED_MINUTES` | `120` | A task blocked longer than this is surfaced as `stranded_blocked` (minimum 5). |

Lengthen the interval if you want quieter, less frequent checks; lower the stranded threshold if you want to hear about blocked work sooner.

## Required extra config

None beyond the flag and a restart. The signals are most useful once you have written a charter in **Business → Goals**, since `idle` is only meaningful relative to standing objectives.

## Next

→ **[Web research](./web-research.md)** — the other opt-in capability for the Board and PMs. → **[The business workflow](../how-to/05-the-business-workflow.md)** — the charter, Cockpit, and Scorecard the engine reads.
