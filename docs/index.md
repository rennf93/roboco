---
title: RoboCo
hide:
  - navigation
---

# RoboCo

**RoboCo is a self-hosted AI software company.** Twenty-five AI agents and one human — you — organized as a real engineering org: roles, a chain of command, formal reviews, and sign-offs. You don't wire up a swarm of bots or babysit a prompt loop. You run a company. You drop work in at the top, and the organization carries it all the way through planning, building, QA, review, and documentation, then brings it back to your desk for the final word.

You act at the two ends. The company fills in everything between.

![The RoboCo control panel: the org tree, a task in progress, and the CEO approval queue.](videos/panel-teaser.gif){ loading=lazy }

!!! warning "Early-stage software (v0)"
    RoboCo is a working prototype under active development. It runs in a homelab, has rough edges, and its API and database schema are not stable yet. It is **not production-ready** — don't expose it to the public internet as-is. Issues and pull requests are very welcome.

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Get Started**

    ---

    Go from a cloned repo to a running company looking back at you from the Command Center.

    [:octicons-arrow-right-24: Install & first run](get-started/index.md)

-   :material-sitemap:{ .lg .middle } **Understand the company**

    ---

    The org, the roles, the strict task lifecycle, and how every agent is structurally sandboxed.

    [:octicons-arrow-right-24: The Company](company/index.md)

-   :material-movie-open-play:{ .lg .middle } **Take the tour**

    ---

    Follow one feature — RoboCo building its own Prompter — from a rough idea to a merged pull request.

    [:octicons-arrow-right-24: The Tour](how-to/README.md)

-   :material-github:{ .lg .middle } **Read the source**

    ---

    RoboCo is open source under AGPL-3.0. The whole stack, agents included, is on GitHub.

    [:octicons-arrow-right-24: rennf93/roboco](https://github.com/rennf93/roboco)

</div>

## What you're running

A single `docker compose up` brings up the whole stack behind one address — `http://localhost:3000` — and that one window is the company. Underneath it:

- **A virtual organization.** A Board sets direction, a Main PM coordinates three delivery cells (Backend, Frontend, UX/UI), and an Auditor watches everything. You sit above all of it as CEO. [See the org →](company/org-and-roles.md)
- **A strict lifecycle.** Every piece of work is a task, no task moves without acceptance criteria, and each task walks the same gated path — built, QA'd, documented, reviewed, approved. The structure is what turns a roster of models into a company that ships. [See the lifecycle →](company/task-lifecycle.md)
- **Real git, real pull requests.** Each agent works in its own clone; work flows up a cell → root → master merge chain as actual pull requests on your repository. **Only you ever merge to `master`.** [See the merge model →](company/merge-model.md)
- **A hard trust boundary.** Agents never touch your API or your shell directly. They act only through a narrow set of role-scoped verbs, so a developer can't merge, QA can't commit, and the Auditor can't even speak. [See how agents are sandboxed →](company/agent-gateway.md)

## Watch it first

If you'd rather see it than read about it:

- [**The 26-minute intro**](https://www.youtube.com/watch?v=t1QNqJgBmkM) — what RoboCo is, a walkthrough, and how to use it.
- [**The 2.5-hour build session**](https://www.youtube.com/watch?v=xige_EUIjIA) — a single conversation taken all the way to a shipped feature.
- [**The 2:33 panel walkthrough**](videos/panel-full-walkthrough.mp4) — every page of the control panel, end to end.

## How models are powered

Agents run on **Anthropic Claude** by default, authenticated from the Claude Code session you already have on the host — no metered API key to wire up. You can also run the whole workforce on **xAI Grok** (the official `grok` CLI on a SuperGrok subscription), or point roles at **local / self-hosted** models. [Choosing & running models →](get-started/index.md)

---

RoboCo is licensed under the [GNU Affero General Public License v3.0](https://github.com/rennf93/roboco/blob/master/LICENSE). Contributions are welcome and require a signed [Contributor License Agreement](https://github.com/rennf93/roboco/blob/master/CLA.md), automated on your first pull request.
