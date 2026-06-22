# Business

The Business page (`/business`) is the strategic layer above day-to-day delivery — the company's charter, its live scorecard against that charter, and the Board's pitches awaiting your decision. It's tabbed: **Goals**, **Secretary**, and **Pitches** (the active tab is in the URL as `?tab=`).

For the *story* of how this layer drives work — how a north star shapes what the Board proposes and what the engines watch — see [The business workflow](../how-to/05-the-business-workflow.md). This page documents the panel surface.

## Goals — the company charter

The Goals tab is the CEO-owned charter. It's injected into every agent's briefing, so the whole company stays goal-aware. You edit it directly:

| Field | What it is |
|-------|-----------|
| **North star** | The long-term vision in a sentence or two. |
| **Objectives** | A list of objective rows (metric / target / status, add and remove freely). |
| **Constraints** | Hard rules, one per line — e.g. "AGPL only", "No external data egress". |
| **Operating policy** | Free-form policy keys the company operates under. |

**Save charter** persists it; the card shows when it was last updated and by whom.

### The Company Scorecard

Below the charter sits the **Company Scorecard** — a live read of how the company is tracking against the charter, pulled from the cockpit summary. It groups into:

- **Delivery** — tasks in flight, blocked, awaiting CEO, and completed in the last 30 days.
- **Spend** — 30-day spend, projected monthly, and your **monthly budget cap** (`monthly_budget_cap_usd`). When a cap is set and spend exceeds it, the figure turns red with an "over budget" marker; with no cap it reads "No budget cap set."
- **Speed** — median lead time against a target of under 24 hours.
- **Objectives** — a placeholder section; per-objective tracking is not wired up yet.

!!! info "The scorecard is the Cockpit, surfaced"
    The scorecard reads the same company snapshot the **Cockpit** API exposes (`/api/cockpit/summary`): a read-only roll-up of delivery, spend against the budget cap, lead time, pending pitches, and strategy signals. There is no separate Cockpit page — its data shows up here on the scorecard and as the Strategy Signals panel on the [Command Center](./command-center.md).

## Secretary

The Secretary tab is your on-demand chief-of-staff chat. It reads company state and can run gated CEO directives. It is a human-only seat — the Secretary has no agent chat verbs and runs only when you talk to it. It's part of the company-in-a-box layer; see the [optional subsystems index](../optional/index.md).

## Pitches

The Pitches tab is where Board-originated proposals land for your decision. Each pitch is a card you can **Approve** or **Reject** (both prompt for a required note). Approving a pitch can, with pitch provisioning enabled, auto-provision a project from it.

!!! warning "Pitches need provisioning enabled to act"
    Pitch provisioning is a default-off subsystem. Flip `ROBOCO_PROVISIONING_ENABLED` on from [Settings → Feature Flags](./settings.md), and read [Pitch provisioning](../optional/pitch-provisioning.md) for what approving actually sets in motion. With it off, the tab still lists pitches but approval won't provision anything.

## Next

→ [The business workflow](../how-to/05-the-business-workflow.md) walks the whole strategic loop end to end · [Optional subsystems](../optional/index.md) covers the engines (strategy, research, provisioning) that this layer drives.
