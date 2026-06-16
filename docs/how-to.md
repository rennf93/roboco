# How RoboCo works

![Twelve-second looping preview of the RoboCo control panel — the org tree, a task in progress, and an approval queue.](videos/panel-teaser.gif)

RoboCo is a virtual software company — 22 AI agents and one human: you. Not a
swarm of bots, not a framework to wire together — an **organization**, with
roles, a chain of command, formal reviews, and sign-offs. You don't micromanage
it; you run it like a CEO. Drop work in at the top and the company carries it all
the way through planning, building, review, and documentation, then brings it
back to your desk for the final word. You act at the two ends; the organization
fills in everything between.

What keeps twenty-two agents from dissolving into noise is that RoboCo is
relentlessly opinionated about *how* work happens: everything is a task, no task
moves without acceptance criteria, and every task walks the same strict lifecycle
— built, QA'd, documented, PM-reviewed, approved — each step gated by role. The
structure is the point. It is what turns a roster of models into a company that
actually ships.

And the proof is this page. The screenshots below aren't a mock-up: they follow
RoboCo building one of its *own* features — the **Prompter**, the task-authoring
page now living in this very control panel. RoboCo's agents scoped it, built it
across three cells, failed and re-ran its QA, documented it, and opened the real
pull request you'll see at the end. RoboCo builds RoboCo — that is the whole
proof of concept.

> The panel is your one window into the company. Every task, agent, message,
> journal, and pull request is live in front of you.

**Prefer video?** A [full screen-recording of the panel](videos/panel-full-walkthrough.mp4)
walks through every page and detail end-to-end — useful as a first tour before
diving into the screenshots below.

![The RoboCo Command Center: per-cell health, the CEO approval queue, live metrics, auditor alerts, and recent activity.](images/overview_dashboard.png)

*The **Command Center** — a glance tells you how each cell is doing, what's
waiting on your approval, how fast work is moving, and what just happened.*

---

## The shape of the company

Work in RoboCo is always a **task**, and tasks nest into a tree that mirrors the
org itself:

```
CEO (you, the human)
 ├── Intake (on-demand interviewer — drafts a task with you)
 └── Board ── Product Owner · Head of Marketing · Auditor (silent)
      └── Main PM (coordinates the cells)
           ├── UX/UI cell    ── PM · 2 Devs · QA · Documenter
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

You describe what you want — a feature, a fix, an entire product. The way in is
the **Task Assistant** — which is the **Prompter** itself, the very feature whose
build the rest of this page follows. (You're about to use the tool RoboCo built
for itself; further down, you'll watch the company build it.) Instead of filling
a form from memory, you give it a rough idea and it reads your *actual* codebase,
asks a few sharp questions, and hands back a properly-formed task — an objective,
a per-cell breakdown, and the acceptance criteria that define what "finished"
really means.

![The Task Assistant's scope form: pick the project or product to work in, then describe what you want to build.](images/start_prompter.png)

*Where it starts — point the assistant at a project (one repo) or a product
(several), drop in a rough idea, and it spins up an agent that reads that code
before it says a word.*

![The Task Assistant chat opening: the idea is in, and the agent is cloning the repo and reading the code before it answers.](images/prompter_run_1.png)

*No canned questions. The agent clones the scope and reads the real surface
first, so everything it asks and proposes is grounded in what your code actually
does.*

<!-- Optional: re-capture prompter_run_2 after the markdown-rendering fix ships (its headers will render cleanly instead of as raw ###). -->
![The agent's grounded analysis: a read of the existing surface, what's missing, where the feature should live, and a proposed shape — citing real files and pages.](images/prompter_run_2.png)

*It comes back having done the homework — naming the real pages, services, and
files, laying out what to build and where, and refining with you over a couple of
turns until the spec is right.*

<!-- prompter_draft_card.png is the captured smoke shot; optionally re-capture after the draft-card cell-badge dedupe ships, for cleaner "Board-led across Backend Frontend" badges. -->
![The draft proposal card: the finished task — objective, per-cell work, and acceptance criteria — with three choices: Keep chatting, Board review & Start, or Approve & Start.](images/prompter_draft_card.png)

*The proposal, ready to launch. Keep chatting to refine it, send it to the
**Board** for review, or approve it straight to the Main PM — your call, on one
card.*

![The Task Assistant's confirmation: the task has been created and handed to the company.](images/prompter_task_accepted.png)

![The created task, live: its objective, the per-cell breakdown, status, and assignment — exactly as the company will work it.](images/prompter_task_created.png)

*From a rough sentence to a real, scoped task in a single chat — acceptance
criteria and all, already moving through the company.*

From here, every task follows the path you chose for it. To show that journey end
to end, the rest of this page follows the **Prompter's own** trip through the
company — from this same starting point to a merged pull request. Send a task to
the **Board** and their job is to pin it down: the Product Owner and Head of
Marketing turn the draft into a settled spec, sharpening the requirements and the
acceptance criteria before anyone writes a line of code. The Auditor watches the
whole time but never interferes.

![A Board review session: the Product Owner writing out requirements and acceptance criteria for a task.](images/chat_session.png)

*The Product Owner working a task over — pinning down the requirements and the
must-haves before anyone writes a line of code.*

![A Board review session for the Prompter feature, with the Product Owner and Head of Marketing each recording their take — positioning, naming, the model-selector UX — against the task.](images/po_hom_review.png)

*Two seats at the table. The Product Owner and the Head of Marketing review the
same task from their own angles and put their reasoning on the record — this is
the Board building the actual spec for the Prompter, the feature this whole
walkthrough follows.*

### 2 · Nothing moves without your green light

The Board hands the reviewed task back to you as a **notification** and waits.
You make one call: send it forward, or send it back. Approve it, and the **Main
PM** picks it up, splits it across the cells, and sets them running.

![The board-review notification waiting on the CEO's decision to start the work or send it back.](images/notifications.png)

*The Board's verdict lands in your notifications and pauses there. A single
approval is what turns the whole company on.*

![The board-review-complete notification for the Prompter task: the Product Owner and Head of Marketing have both reviewed it, and it is now ready for the CEO's Approve & Start decision or rejection.](images/ceo_review_notif.png)

*The notification itself, spelled out: the Board has finished, the task is
recorded, and nothing happens until you say so — Approve & Start hands it to the
Main PM; reject it and it goes back. This is the first of the only two moments
the company needs you.*

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

![A task's notes after a QA review: the developer's notes, a QA section marked Failed, and a silent Auditor column — all on the same task.](images/qa_fail.png)

*QA earning its seat. On this Prompter task it read the work, marked it
**failed**, and sent it back — the developer's notes and the QA verdict sit side
by side on the record, with the Auditor watching the whole exchange. Real review,
not a rubber stamp; the gate only opens when the work is right.*

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

![The integrated pull request's commit list: one verified commit per cell — the UX/UI design specs and the backend chat endpoint — each co-authored by the agent that wrote it.](images/opened_final_pr_commits.png)

*Three streams becoming one history. Each cell's work lands as its own
**verified** commit, co-authored by the agent that wrote it — the UX/UI design,
the backend endpoint, the frontend page — folded together into the single pull
request that comes back to you.*

### 6 · The last call is yours

The cells' work is folded up, the Main PM opens the **final pull request** into
`master`, and the company goes quiet. The decision comes back to exactly where it
started — with you. You're the only one who ever touches `master`, and anything
waiting on you sits in the **CEO Approval Queue** until you act.

![The final CEO approval notification: the integrated Prompter PR is ready, all three cells delivered and QA-passed, awaiting the CEO's review and merge.](images/ceo_approval_notif.png)

*The hand-off back to you. The integrated PR is open, every cell has delivered,
QA is green — and it waits. Nothing reaches `master` without your word.*

![The integrated pull request open on GitHub, in the repository's Pull Requests list.](images/opened_final_pr.png)

*And it is a real pull request, on the real repository — not a simulation. The
company's work shows up exactly where any engineer would look for it.*

![The pull request's description: the objective, what the task builds, the per-cell breakdown, and the notes the company wrote for it.](images/opened_final_pr_body.png)

*Open it and the whole brief is there — the objective, what was built, the
board-led split across the three cells, and the company's own notes — written by
RoboCo, for you to read before you decide.*

![The pull request's Files changed tab: the actual diff — migrations, API, and panel components — the company is asking to merge.](images/opened_final_pr_changes.png)

*The real diff, laid out for you to inspect — the migrations, the endpoints, the
panel components. This is the substance you are signing off on.*

![The CEO's actions on the awaiting-approval task: Approve & Merge, Request Changes, or Cancel.](images/approve_button_merge_rework.png)

*Your two words. **Approve & Merge** and it ships to `master`; **Request Changes**
and it goes around for another lap. The last call has the same shape as the first
— one decision, yours alone.*

---

## And round it goes

You handed the company a task; it scoped it, built it, failed and re-ran its own
QA, documented it, and brought it back as a single pull request for your
sign-off. That's one complete pass.

![The full task table for the Prompter feature: a parent task awaiting CEO approval over its completed UX/UI, Frontend, and Backend child tasks.](images/all_tasks_final_state.png)

*The whole tree in its final state — the parent waiting on your approval, every
cell's task done beneath it. One feature, start to finish, with you at only the
two ends.*

And the feature in these screenshots is the proof. The **Prompter** wasn't built
for a demo — it's a real page RoboCo's agents shipped to RoboCo's own control
panel. A company building its own product, in front of you, is the whole point of
RoboCo. What makes that hold together isn't a clever model or a lucky run; it's
the **organization** — the roles, the gated lifecycle, the reviews and the
sign-offs that keep twenty-two agents moving as a company instead of a crowd. Run
as many of these passes as you like, across as many projects as you like.

---

*RoboCo is early-stage, work-in-progress software (v0) — expect rough edges. The
[README](../README.md) covers setup, architecture, and the security model.*
