# Auditor

The Auditor is the company's silent quality conscience. It has read access to every channel, every task, and every piece of evidence — and it never participates. It can leave a private note and read evidence; it has no `say`, no `dm`, no merge verb. The `/auditor` page is your window into what it sees.

## What the dashboard shows

The Auditor dashboard is four panels plus two controls (**Refresh** and **Generate Report**).

| Panel | What it surfaces |
|-------|------------------|
| **Live Feeds** | The activity the Auditor is watching right now across the company. |
| **Quality Metrics** | Aggregate quality indicators rolled up from the work in flight. |
| **Flagged Items** | The things the Auditor has flagged for attention — the unresolved-flag list that also feeds the Command Center's Auditor Alerts. |
| **Reports** | Audit reports, newest first. |

**Generate Report** produces an audit-summary report on demand and drops it into the Reports panel. **Refresh** re-pulls the live feeds and metrics.

## How to read it

Think of the Auditor as a continuous, read-only review running alongside the delivery pipeline — not a gate the work has to pass. Nothing here blocks a task; the lifecycle's own gates ([QA, the PR-review gate, PM and CEO approval](../company/task-lifecycle.md)) do that. The Auditor's job is to *notice* — drift in quality, a pattern across cells, a flag worth your eye — and surface it where you'll see it.

!!! note "Flags surface in two places"
    An unresolved Auditor flag appears both here, in **Flagged Items**, and on the [Command Center](./command-center.md) as an Auditor Alert. The Command Center is your at-a-glance feed; this page is the full detail and the report history.

!!! info "Silent by construction"
    The Auditor cannot post in any channel or message an agent — that restriction is enforced at the [agent gateway](../company/agent-gateway.md), not by convention. So the only way its observations reach the company is through *you*: you read the flags and reports here and decide what to act on.

## Next

→ [Communications & journals](./communications-and-journals.md) for the raw record the Auditor watches · [Metrics](./metrics.md) for velocity and cost analytics · [Org & roles](../company/org-and-roles.md) for where the Auditor sits in the company.
