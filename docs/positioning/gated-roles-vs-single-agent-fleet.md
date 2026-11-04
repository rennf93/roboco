# RoboCo vs. Devin: Gated Roles vs. a Single-Agent Fleet

Devin's homepage now sells fleet-style delegation, review, automations, triage and CI fixes. Each is a capability of one agent product. RoboCo ships the same jobs as separate roles. Each role has its own lifecycle gate, and a task cannot skip a gate.

Source: [Devin — the autonomous software engineer](https://devin.ai/) (fetched 2026-11-03)

**Response decision:** no product change is warranted. This note states the structural difference and maps each Devin use case to the RoboCo role or gate that covers it. Where RoboCo's coverage is partial or off by default, the table says so.

## What devin.ai states

Quotes are verbatim from https://devin.ai/ as fetched on 2026-11-03.

1. Fleet: "Delegate a big task, then Devin splits it across parallel cloud agents, manages them, and reports back".
2. Done condition: Devin "won't stop until the PR is ready to merge".
3. Review: "Devin Review finds bugs and vulnerabilities before they reach production."
4. Own-browser testing: "Devin spins up its own Windows, Mac, and Linux virtual machines as needed to build, test in its browser, and record itself clicking through what it built".
5. Scheduled chores: "Create scheduled workflows and trigger-based automations for repetitive work." The page shows an example automation: "Every morning, find stale Linear tickets and clean them up or nudge the owner."
6. Triage and CI: the page lists "Triage bugs as they land", "Fix CI when it fails" and "Triage issues when they get posted". It also states "Set up automations so Devin handles bugs, failed CI, and other alerts as they land".

Not found on the fetched page: the phrase "team of Devins", and the terms "visual QA", "auto-generated documentation" and "release notes". The intake brief quoted those. This note does not quote them. The page does show a pinned-session title, "Document service architecture", and a Wiki feature that "updates diagrams and code maps as quickly as the codebase changes". That is the only documentation-related text we cite.

## The structural difference

Devin's page describes one agent product doing the work and reviewing it ("Devin Review"). The fleet is parallel copies of that agent, managed by that agent.

RoboCo splits the work across roles that cannot act as each other. The gateway gives each role its own verb list. A developer has `i_am_done`. QA has `pass_review` and `fail_review`. A documenter has `i_documented`. A PR reviewer has `pr_pass` and `pr_fail`. A PM has `complete`. (`roboco/foundation/policy/lifecycle.py`, `intents_for_role`; `docs/rag/lifecycle/intent-verbs.md`.)

Each gate is a lifecycle status with role-restricted transitions:

1. `awaiting_qa`: only QA passes or fails. (`roboco/foundation/policy/lifecycle.py`; `docs/rag/lifecycle/status-transitions.md`.)
2. `awaiting_documentation`: a documenter or developer completes it. `docs_complete=True` is required to move on. (`roboco/foundation/policy/lifecycle.py`, the `docs_complete` action; `docs/rag/lifecycle/status-transitions.md`.)
3. `awaiting_pr_review`: only the PR reviewer passes or fails the assembled PR. (`.claude/rules/task-lifecycle.md`, "In-path PR-review gate"; `docs/map/pr-gate-review.md`.)
4. `awaiting_pm_review`: only a PM role completes, i.e. merges. Root work goes on to a CEO-only approval. (`roboco/foundation/policy/lifecycle.py`; `docs/positioning/devin-2.2-2026-08.md`, facts 1 to 4.)

The agent that wrote the code cannot pass its own PR. `docs/positioning/devin-2.2-2026-08.md` (fact 1) records the `self_review_block=True` rule and its source in `roboco/foundation/policy/lifecycle.py`.

## Use-case map

| Devin use case (devin.ai) | RoboCo role or gate | Receipt | Status |
|---|---|---|---|
| Parallel fleet for a big task | Main PM and cell PMs decompose a task into dev subtasks. Each subtask has its own branch and PR. Independent subtasks run in parallel. | `.claude/rules/task-lifecycle.md`, "Sequence is the bar"; `docs/rag/workflows/task-planning.md` | Shipped |
| PR review ("Devin Review") | Dev work passes QA (`be-qa`), then the cell PM's assembled PR passes a separate PR reviewer before the PM merges. | `docs/rag/roles/qa.md`; `docs/rag/roles/pr-reviewer.md`; `docs/map/pr-gate-review.md` | Shipped |
| Browser testing / visual checks | `fe-qa` and `ux-qa` get structured Playwright browser tools: navigate, snapshot, evaluate, screenshot. | `docs/positioning/devin-2.2-2026-08.md`, fact 5 | Shipped. No GUI desktop session, no screen recording. |
| Documentation | The documenter role (`be-doc`, `fe-doc`) owns the `awaiting_documentation` gate. | `docs/rag/roles/documenter.md`; `roboco/foundation/policy/lifecycle.py`, the `docs_complete` action | Shipped |
| Docs kept current after a release | `DocsSyncEngine` opens one docs-update task when the docs site has drifted. It never auto-merges. | `docs/map/engine-docs-sync.md`; `.claude/rules/autonomy-engines.md`, "Docs-divergence sync" | Off by default (`ROBOCO_DOCS_SYNC_ENABLED`) |
| Scheduled chores | Board Programs run on cron cadences, for example Sentinel weekly. The dependency-update bot runs weekly. Every output is held for the CEO. | `.claude/rules/board-programs-and-x.md`, "Board Program registry"; `.claude/rules/autonomy-engines.md`, "Dependency-update bot" | Per-program, off by default |
| Release notes | The gated release manager drafts the CHANGELOG entry and holds the release for CEO approval. It never publishes without the CEO. | `docs/map/release-manager.md`; `.claude/rules/autonomy-engines.md`, "Gated release manager" | Off by default (`ROBOCO_RELEASE_MANAGER_ENABLED`) |
| Issue triage | PM roles carry a `triage` verb. Board roles carry `triage` and `escalate_to_ceo`. | `roboco/foundation/policy/lifecycle.py`, `intents_for_role` | Shipped. No external issue-tracker intake is documented. |
| CI-failure fixes | The CI-watch loop opens one fix task on a red default branch. The task rides the normal delivery flow and the PR-review gate. | `docs/map/engines-heal-ciwatch-depupdate.md`; `.claude/rules/autonomy-engines.md`, "Multi-repo CI-watch" | Off by default (`ROBOCO_CI_WATCH_ENABLED`) |

## What RoboCo does not claim

1. No GUI desktop or virtual-machine computer-use. See `docs/positioning/devin-2.2-2026-08.md`, fact 5.
2. No Slack, Teams or PagerDuty trigger surface is documented in this repo's docs. devin.ai shows those integrations; we cite none of them.
3. No speed, cost or quality figures. devin.ai states "up to 60% less" for its Fusion model mix. We have no comparable measurement and make no comparison.
4. The autonomy engines above are dormant until an operator arms them. They never auto-merge.

## Position

One agent that writes, reviews and ships is one set of eyes.

RoboCo uses separate roles. Each role has its own verbs and its own gate. The agent that wrote the change cannot pass it.

## Public-site note

The public docs site lives in `roboco-website`, which is not reachable from this task. This page is the internal positioning record. The public page is a follow-up on that project.
