# RoboCo and the ETR Cursor Signal: Orchestration Is the Bar

In June 2026, ETR surveyed 21 enterprise technology leaders on Cursor spend. 12 planned to increase it. 9 planned to cut it. 7 of the 9 who plan to cut named Claude Code as the destination. The sharpest risk ETR quotes: agentic development is "making any IDE less valuable than ever" unless Cursor delivers multi-agent orchestration.

Source: [ETR — The Cursor Trade: Seat Growth Meets the Agentic Shift](https://research.etr.ai/etr-data-drop/the-cursor-trade-seat-growth-meets-the-agentic-shift)

**Response decision:** no product change is warranted. Only the messaging changed: the README lede and Overview now lead with multi-agent orchestration (that edit ships in a sibling task, owned by fe-dev-1). RoboCo is not an IDE and does not compete as one. This note records what the signal says, what changed, and why, checked against this codebase rather than assumed.

## What changed

1. The README lede and Overview are orchestration-first. They name the coordinated agent workforce before anything else.
2. This note and the `[Unreleased]` CHANGELOG entry record the change and link the ETR source.
3. No code, API, or feature changed. Every claim below is already shipped.

## Why

1. Buyers name multi-agent orchestration as the bar. ETR's own quote ties the value of an IDE to whether it delivers it.
2. RoboCo already ships it. The orchestration is the product, not an add-on.
3. We are not in the IDE war. RoboCo has no editor surface to defend. It coordinates the agents that do the work.

## Verified facts

1. **A structured workforce, not a single assistant.** The README documents 26 AI agents plus one human CEO: Intake, Secretary, PR Reviewer, DevOps, a three-agent Board, a Main PM, and three six-agent cells (Backend, Frontend, UX/UI), each with 2 developers, 1 QA, 1 PM, 1 documenter, and 1 PR reviewer.
2. **Orchestration is enforced in code.** The task lifecycle lives in `roboco/foundation/policy/lifecycle.py`. Every status transition is role-checked there, so a developer implements, QA reviews, and a PM merges, and no role can skip a stage.
3. **Parallel work with dependency ordering.** Delegation stamps each sibling task's wave from the collision graph, so independent tasks tie and run in parallel while colliding tasks run in order (`stamp_wave_sequence`; sequence-drift fix #681 in the CHANGELOG). MegaTask batches sequence several tasks across projects the same way (`roboco/services/sequencing.py`).
4. **Independent review before merge.** A separate QA role and a separate `pr_reviewer` role review each task, and the in-path PR-review gate (`awaiting_pr_review`) sits before the PM merge. Only the CEO approves a root PR into master (`awaiting_ceo_approval`).
5. **Model choice is not locked to one vendor.** `roboco/llm/providers/` carries Claude Code, Grok, Gemini, Codex, Kimi, OpenRouter, Nebius, and Hummin provider implementations. Claude is the default.
6. **Self-hosted under AGPL-3.0.** The repository `LICENSE` is GNU AGPLv3.

## What we do not claim

1. RoboCo does not replace an IDE and ships no editor.
2. RoboCo makes no claim about Cursor's roadmap or about ETR's panel beyond the figures quoted above.
3. The sample is 21 leaders. We cite it as a signal, not a market measurement.
