# The shape of the company

What keeps twenty-two agents from dissolving into noise is that RoboCo is relentlessly opinionated about *how* work happens: everything is a task, no task moves without acceptance criteria, and every task walks the same strict lifecycle — built, QA'd, documented, PM-reviewed, approved — each step gated by role. The structure is the point. It is what turns a roster of models into a company that actually ships.

Work in RoboCo is always a **task**, and tasks nest into a tree that mirrors the org itself:

```text
CEO (you, the human)
 ├── Intake (on-demand interviewer — drafts a task with you)
 ├── Secretary (your chief-of-staff — acts only on your command)
 └── Board ── Product Owner · Head of Marketing · Auditor (silent) · PR Reviewer (inbound external PRs)
      └── Main PM (coordinates the cells)
           ├── UX/UI cell    ── PM · 2 Devs · QA · Documenter
           ├── Frontend cell ── PM · 2 Devs · QA · Documenter
           └── Backend cell  ── PM · 2 Devs · QA · Documenter
```

The walkthrough that follows traces the delivery path — Intake, the Board, the Main PM, the cells. The Secretary and the research and strategy engines sit one layer up, steering the company as a whole; they get their own chapter at the end.

In practice, one feature becomes a small tree of work — a parent task at the top, a branch for each cell underneath, every node carrying its own status, git branch, and pull request:

![The task tree for a feature: a Main PM parent task fanned out to UX/UI, Frontend, and Backend cell tasks, each with a live status and a GitHub branch.](../images/run.png)

*A feature in motion. The parent task fans out to the UX/UI, Frontend, and Backend cells, and each child moves through its own lifecycle — in progress, awaiting review, completed — on its own branch.*

---

Next: **[It starts with you →](02-it-starts-with-you.md)**
