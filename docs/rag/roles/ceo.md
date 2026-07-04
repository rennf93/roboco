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

The CEO is a **human** and acts through the **panel/UI**, not through the agent gateway. There are no `roboco_*` MCP tools for the CEO — the lifecycle actions below (`ceo_approve`, `ceo_reject`) are buttons in the panel, backed by the HTTP API, not verbs an agent calls.

## What the CEO CAN Do

- View ALL tasks organization-wide
- Approve or reject tasks in `awaiting_ceo_approval`
- Cancel tasks (CEO is one of the cancel-authorized roles)
- Set strategic direction
- Message any agent directly via A2A (`dm`), unrestricted on the CEO's side

## CEO Approval Workflow

When a Main PM or Board member escalates a major task via `escalate_to_ceo`, it lands in `awaiting_ceo_approval`. The CEO reviews in the panel and either:

- **Approve** — merges the PR, task → `completed` (lifecycle `ceo_approve`)
- **Request changes** — task → `needs_revision` (lifecycle `ceo_reject`)

Both are panel actions; the agent that escalated simply idles until the CEO decides.

### X posts and roadmap items (panel-only, not gateway verbs)

Two more CEO-only approval queues, both plain REST endpoints on the orchestrator (not lifecycle transitions, not agent-callable verbs):

- **X (Twitter) posts** — `GET/POST /api/x/posts{,/{id}/approve,/reject}`. Every held release-announcement or mention-reply draft the X engine originates (`ROBOCO_X_ENGINE_ENABLED`) sits here; approve posts it to X (optionally with an edited body, up to 280 chars), reject cancels it with a reason. Credentials are set separately via `GET/POST /api/x/credentials` (write-only — the API only ever returns `has_credentials`).
- **Roadmap items** — `GET /api/roadmap/cycles`, `POST /api/roadmap/cycles/{task_id}/items/{item_id}/{approve,reject}`. Each weekly roadmap-engine cycle (`ROBOCO_ROADMAP_ENGINE_ENABLED`) the Product Owner authors 3-7 item drafts; approve materializes one as a BACKLOG task (`source=roadmap`), reject records your reason. Approval is per-item, not per-cycle — you can approve some and reject others from the same cycle.

Both are idempotent (re-approving an already-posted/already-materialized item is a no-op) and both are held artifacts — nothing here was ever dispatched to an agent before your decision.

## Escalation

The CEO is the final escalation target:

```
Developer → Cell PM → Main PM → Product Owner → CEO
```

Only `main_pm`, `product_owner`, and `head_marketing` can escalate a task to the CEO (via `escalate_to_ceo`).

## Communication

The CEO has no channels to monitor. Oversight is via task state (all tasks are visible in the panel), notifications, and direct A2A messages — the CEO can `dm` any agent at any time, unrestricted, while an agent may only reply inside a conversation the CEO opened.

The CEO communicates and decides through the panel/UI rather than calling the agent content tools (`dm` / `notify`) directly.
