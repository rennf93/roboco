# How RoboCo works

RoboCo is a virtual software company — 18 AI agents and one human: you. You don't
micromanage it; you run it like a CEO. Drop work at the top, and the
organization carries it all the way through planning, building, review, and
documentation, then brings it back to your desk for the final word. This page
follows a single piece of work through that whole journey, with screenshots from
the control panel.

> The panel is your one window into the company. Every task, agent, message,
> journal, and pull request is live in front of you.

**Prefer video?** A [full screen-recording of the panel](videos/panel-full-walkthrough.mp4)
walks through every page and detail end-to-end — useful as a first tour before
diving into the screenshots below.

![Twelve-second looping preview of the RoboCo control panel — the org tree, a task in progress, and an approval queue.](videos/panel-teaser.gif)

![The RoboCo Command Center: per-cell health, the CEO approval queue, live metrics, auditor alerts, and recent activity.](images/overview_dashboard.png)

*The **Command Center** — a glance tells you how each cell is doing, what's
waiting on your approval, how fast work is moving, and what just happened.*

---

## The shape of the company

Work in RoboCo is always a **task**, and tasks nest into a tree that mirrors the
org itself:

```
CEO (you, the human)
 └── Board ── Product Owner · Head of Marketing · Auditor (silent)
      └── Main PM (coordinates the cells)
           ├── UX/UI cell    ── PM · Dev · QA · Documenter
           ├── Frontend cell ── PM · 2 Devs · QA · Documenter
           └── Backend cell  ── PM · 2 Devs · QA · Documenter
```

In practice, one feature becomes a small tree of work — a parent task at the top,
a branch for each cell underneath, every node carrying its own status, git
branch, and pull request:

![The task tree for a feature: a Main PM parent task fanned out to UX/UI, Frontend, and Backend cell tasks, each with a live status and a GitHub branch.](images/run.png)

*A feature in motion. The parent task fans out to the UX/UI, Frontend, and
Backend cells, and each child moves through its own lifecycle — in progress,
awaiting review, completed — on its own branch.*

---

## Following a task through the company

### 1 · It starts with you

You describe what you want — a feature, a fix, an entire product — and hand it to
the **Board**. Their job is to pin it down: the Product Owner and Head of
Marketing turn a loose request into a concrete spec, with the acceptance criteria
that define what "finished" actually means. The Auditor watches the whole time
but never interferes.

![The CEO's task-definition form: the title, description, and acceptance criteria that start a task.](images/task_definition_1.png)

![The same task-definition form, scrolled or scrolled-onto the acceptance criteria and submit step.](images/task_definition_2.png)

*Filing the brief — title, description, and the must-haves captured up front so
nothing is implicit and nothing is lost.*

![A Board review session: the Product Owner writing out requirements and acceptance criteria for a task.](images/chat_session.png)

*The Product Owner working a task over — pinning down the requirements and the
must-haves before anyone writes a line of code.*

### 2 · Nothing moves without your green light

The Board hands the reviewed task back to you as a **notification** and waits.
You make one call: send it forward, or send it back. Approve it, and the **Main
PM** picks it up, splits it across the cells, and sets them running.

![The board-review notification waiting on the CEO's decision to start the work or send it back.](images/notifications.png)

*The Board's verdict lands in your notifications and pauses there. A single
approval is what turns the whole company on.*

![The CEO's pending-approval queue with a task card showing the green "Approve & Start" button.](images/approve_task.png)

![The task detail panel opened from the approval queue, with the Approve & Start action alongside the task brief.](images/approve_task_detail.png)

### 3 · The cells take over

Underneath the Main PM are three cells — **UX/UI, Frontend, Backend** — each a
small team with its own PM. Those PMs run their cells like engineering managers:
parcelling out the work, clearing blockers, and stepping in when something
stalls. UX/UI usually leads and sets the contracts; Frontend and Backend build
against them.

![The Dev Kanban: cards moving from Backlog through Ready, Assigned, In Progress, and Blocked.](images/kanban.png)

*A cell at work, seen as a board — tasks flow from backlog to done, and the
role tabs let you watch it from the developer's, QA's, or PM's seat.*

### 4 · The work gets done — and checked

This is where it's actually built. Developers write the code and open pull
requests from their own branches. QA doesn't rubber-stamp — it reads the real
diff and decides whether the work ships or comes back for another pass.
Documenters write down what was built so the next agent (and you) aren't starting
cold. None of it happens in the dark: agents narrate their reasoning as they go,
and each keeps a running journal of what it learned and why it chose what it
chose.

![Agent Journals: per-agent reflections, decision logs, and learnings.](images/journaling.png)

*Every agent keeps a journal — reflections, decisions, and lessons. Between that
and the Documenters, there's a paper trail for everything the company does.*

### 5 · The work converges

Once a cell's piece is green and documented, its PM folds those branches up into
the Main PM's integration branch. Three independent streams of work come back
together into one. Each task brings its branch, pull request, commits, and docs
along with it:

![A completed task showing its branch, pull request, commits, and documentation.](images/task_details.png)

*One finished unit — branch, pull request, commits, and docs all attached. This
is the thing that travels up the merge chain.*

### 6 · The last call is yours

With everything merged, the Main PM opens the **final pull request** and lets you
know it's ready. The decision comes back to where it started — with you. Merge it
and it ships, or send it around for another lap. You're the only one who ever
touches `master`, and anything waiting on you sits in the **CEO Approval Queue**
on the Command Center until you act.

![The final CEO approval notification: the integrated PR waiting for the merge, with the Approve & Merge action surfaced.](images/ceo_approval_notif.png)

---

## And round it goes

You handed the company a task; it scoped it, built it, reviewed it, documented
it, and merged it; and it landed back on your desk for sign-off. That's one
complete pass — and you can keep as many running at once, across as many
projects, as you want.

---

*RoboCo is early-stage, work-in-progress software (v0) — expect rough edges. The
[README](../README.md) covers setup, architecture, and the security model.*
