# CEO Role

## Identity

- **Agent**: ceo (Renzo - Human)
- **Role**: `ceo`
- **Team**: board
- **Reports to**: N/A (top of hierarchy)

## Core Responsibilities

1. Final authority on major decisions
2. Approve major task completions
3. Set strategic direction
4. Oversee entire organization

## How the CEO Acts

The CEO is a **human** and acts through the **panel/UI**, not through the
agent gateway. There are no `roboco_*` MCP tools for the CEO — the
lifecycle actions below (`ceo_approve`, `ceo_reject`) are buttons in the
panel, backed by the HTTP API, not verbs an agent calls.

## What the CEO CAN Do

- View ALL tasks organization-wide
- Approve or reject tasks in `awaiting_ceo_approval`
- Cancel tasks (CEO is one of the cancel-authorized roles)
- Set strategic direction
- Read all channels

## CEO Approval Workflow

When a Main PM or Board member escalates a major task via
`escalate_to_ceo`, it lands in `awaiting_ceo_approval`. The CEO reviews
in the panel and either:

- **Approve** — merges the PR, task → `completed` (lifecycle `ceo_approve`)
- **Request changes** — task → `needs_revision` (lifecycle `ceo_reject`)

Both are panel actions; the agent that escalated simply idles until the
CEO decides.

## Escalation

The CEO is the final escalation target:

```
Developer → Cell PM → Main PM → Product Owner → CEO
```

Only `main_pm`, `product_owner`, and `head_marketing` can escalate a task
to the CEO (via `escalate_to_ceo`).

## Communication

The CEO has read access to all channels, including:
- #board-private
- #announcements
- All cell and cross-cell channels

The CEO communicates and decides through the panel/UI rather than the
agent content tools (`say` / `dm` / `notify`).
