# Change note: Devin fleet signal (2026-11)

Devin's homepage sells review, docs, scheduled chores, triage and CI fixes as use cases of one agent product. RoboCo ships each as a first-class role behind a lifecycle gate. This note records what changed in response and why.

Source: [devin.ai](https://devin.ai/), fetched 2026-11-03.

**Response decision:** positioning change only. No code change. No new capability.

## What devin.ai states

Quoted from the page as fetched:

1. "Devin Review finds bugs and vulnerabilities before they reach production."
2. "Create scheduled workflows and trigger-based automations for repetitive work. Devin swarms can proactively find novel vulnerabilities, performance issues, and gaps in test coverage."
3. "Set up automations so Devin handles bugs, failed CI, and other alerts as they land." Listed examples include "Triage bugs as they land", "Fix CI when it fails" and "Triage issues when they get posted".
4. A Wiki feature that maps the codebase and keeps diagrams and code maps current.

Not found: the line "Devin can spin up a team of Devins for large tasks" did not appear in the page text fetched on 2026-11-03. The intake signal quotes it. It is not repeated here as a devin.ai claim. Visual QA and release notes were not found by name either.

## What changed

- New positioning page: [`gated-roles-vs-single-agent-fleet.md`](gated-roles-vs-single-agent-fleet.md) (`docs/positioning/gated-roles-vs-single-agent-fleet.md`). It contrasts separate roles, each with its own lifecycle gate, against one agent product covering the same use cases.
- [`CHANGELOG.md`](../../CHANGELOG.md) gets an `[Unreleased]` entry.

## Why

The use cases Devin lists map onto roles RoboCo already ships:

| Use case on devin.ai | RoboCo role |
|---|---|
| PR review | `pr_reviewer`, gate `awaiting_pr_review` |
| Testing and QA | `qa`, gate `awaiting_qa` |
| Documentation | `documenter`, gate `awaiting_documentation` |
| Triage | `triage` verb on PM and Board roles |

Receipt: `roboco/foundation/policy/lifecycle.py` restricts each gate transition to its role. The author of a change cannot pass it. Roles and gates are described in `.claude/rules/task-lifecycle.md` and `docs/rag/lifecycle/status-transitions.md`.

## Scope

- Positioning change. No code change.
- No generated prompt artifacts touched.
- The public-site MDX page, route wrapper and `nav.ts` entry are not in this repo. They are tracked by root `e4b3e337` on `roboco-website`.
- No metrics are claimed.
