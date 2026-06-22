# Agents & work sessions

The **Agents** page (`/agents`) is your view of the workforce: who's running, who's idle, who's stuck, and what each one is costing in tokens. From an agent's detail page you can spawn or stop it and watch its reasoning live. The **Work Sessions** ledger (`/work-sessions`) is the read-only record of every branch-and-PR an agent has worked, tying git activity back to the agents that produced it.

## The roster (`/agents`)

Agents are grouped exactly the way the org chart is laid out:

- **Board** — Product Owner, Head of Marketing, Auditor
- **Main PM**
- **Backend Cell**, **Frontend Cell**, **UX/UI Cell**
- **Support** — the CEO-direct helpers (Intake/Prompter, Secretary, the root PR Reviewer), shown only when present

Each card shows the agent's **live state** merged with its **token usage**, so health and cost sit together. States you'll see:

| State | Meaning |
|-------|---------|
| `running` / `ready` / `starting` | the agent is alive and working (or coming up) |
| `idle` | spawned but with no work in hand |
| `waiting_long` | blocked, waiting on human input or an external resolution |
| `error` | the container hit errors (the card shows the error count) |

A **Waiting Agents** alert surfaces any agent stuck in `waiting_long` at the top of the page, so a blocked agent doesn't sit unnoticed.

## Agent detail (`/agents/[id]`)

Open an agent to control it and watch it think. The page gives you:

- **Spawn** — bring the agent's container up.
- **Stop** (graceful) and **Force Stop** — wind it down cleanly or kill it immediately.
- **Resolve Wait** — when an agent is in `waiting_long`, this dialog is how you hand it the input or decision it's blocked on.
- **Live stream viewer** — while the agent is active, a viewer streams its reasoning in real time over the agent WebSocket, so you can literally watch it work.

!!! tip "Stop the bleeding"
    If an agent is crash-looping or burning tokens, **Force Stop** from its detail page is the fastest way to halt it. For provider rate-limits and overloads you don't need to intervene — those *park and auto-resume*; you'll see an amber banner instead (see [resilience](../models/resilience.md)).

Per-agent token spend rolls up into the [Metrics](./metrics.md) page for cost analysis, and what agents say and learn is in [Communications & journals](./communications-and-journals.md).

## Work Sessions (`/work-sessions`)

A **WorkSession** links an agent's work to a task and tracks its git footprint — branch name, base and target branches, the PR number/URL, and merge status. The Work Sessions page is a **read-only ledger** of those sessions, with search-by-branch and a status filter (state, again, lives in the URL).

!!! note "No sidebar link"
    Work Sessions has **no entry in the sidebar nav**. Reach it by typing `/work-sessions` directly or by following a link from elsewhere in the panel. It's a reference ledger, not a daily-driver page.

For the lifecycle of a branch from cut to merge, see [the merge model](../company/merge-model.md).

## Next

→ [Git](./git.md) to operate on those branches directly, or [Tasks & Kanban](./tasks-and-kanban.md) to see the work the agents are running.
