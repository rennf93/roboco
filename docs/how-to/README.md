# How RoboCo works

![Twelve-second looping preview of the RoboCo control panel — the org tree, a task in progress, and an approval queue.](../videos/panel-teaser.gif)

RoboCo is a virtual software company — 25 AI agents and one human: you. Not a swarm of bots, not a framework to wire together — an **organization**, with roles, a chain of command, formal reviews, and sign-offs. You don't micromanage it; you run it like a CEO. Drop work in at the top and the company carries it all the way through planning, building, review, and documentation, then brings it back to your desk for the final word. You act at the two ends; the organization fills in everything between.

And the proof is this guide. The screenshots throughout aren't a mock-up: they follow RoboCo building one of its *own* features — the **Prompter**, the task-authoring page now living in this very control panel. RoboCo's agents scoped it, built it across three cells, failed and re-ran its QA, documented it, and opened the real pull request you'll see at the end. RoboCo builds RoboCo — that is the whole proof of concept.

> The panel is your one window into the company. Every task, agent, message, journal, and pull request is live in front of you.

**Prefer video?** A [full screen-recording of the panel](../videos/panel-full-walkthrough.mp4) walks through every page and detail end-to-end — useful as a first tour before diving into the screenshots.

![The RoboCo Command Center: per-cell health, the CEO approval queue, live metrics, auditor alerts, and recent activity.](../images/overview_dashboard.png)

*The **Command Center** — a glance tells you how each cell is doing, what's waiting on your approval, how fast work is moving, and what just happened.*

---

## Read it in order

This guide follows one task — the Prompter's own build — from a rough idea to a merged pull request, then steps back to the strategic layer you steer the whole company with.

1. **[The shape of the company](01-the-company.md)** — the org, and how every piece of work is a task that nests into a tree.
2. **[It starts with you](02-it-starts-with-you.md)** — drafting a task with the assistant, the Board review, and your first green light.
3. **[The cells build it](03-the-cells-build-it.md)** — the three cells, the Dev Kanban, real QA, journals, and the integration branch.
4. **[The last call — and the loop](04-the-last-call-and-the-loop.md)** — the final pull request, the CEO Approval Queue, the merge, and round it goes.
5. **[The business workflow](05-the-business-workflow.md)** — the charter, the Cockpit, the Secretary, and the research and strategy engines that run above the day-to-day.

---

*RoboCo is early-stage, work-in-progress software (v0) — expect rough edges. The [Get Started guide](../get-started/index.md) covers setup, configuration, and the security model.*
