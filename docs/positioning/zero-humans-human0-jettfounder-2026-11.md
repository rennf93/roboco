# RoboCo vs. the "Zero Humans" AI-Company Pitch (human0.ai, JettFounder)

Two products now sell an AI-agent company with no human in it. human0.ai builds companies run end-to-end by AI agents and advertises "zero humans required," "no human employees in the loop," and that "approved work merges on its own." JettFounder says "14 AI agents. Every role in the company. No humans." Both frame human oversight as the thing to remove.

Sources: [human0.ai](https://human0.ai/), [JettFounder](https://jettfounder.com)

**Response decision:** no product change is warranted. The "AI company" framing is becoming common, and these entrants position against human oversight, which leaves governance as the open ground. RoboCo already runs the opposite model: one human CEO approves every merge, and nothing auto-posts or auto-starts. The response is copy only. The README and the panel state that governance model plainly, and every claim in that copy is pinned below to code that enforces it. RoboCo does not argue against autonomy in general; it states what its own gates do.

## Verified facts

Checked against the branch head by reading each symbol and line range below.

1. **Only the CEO can approve or reject a merge-bound task.** The `ceo_approve` action (`AWAITING_CEO_APPROVAL -> COMPLETED`) and the `ceo_reject` action (`AWAITING_CEO_APPROVAL -> NEEDS_REVISION`) each set `allowed_roles=frozenset({Role.CEO})` in the `ActionSpec` table (`roboco/foundation/policy/lifecycle.py:687-706`). The status-transition table repeats the CEO-only restriction (`lifecycle.py:370-382`). No agent role, PM roles included, can run either transition. This backs the README line "Only the CEO merges to `master`" (`README.md:69`).
2. **Held work does not start itself.** A MegaTask sent to the Board route is created held for review, and the CEO releases it with Approve & Start on the umbrella task. The comment and toast copy in `usePrompter`'s batch-confirm path says so (`panel/src/hooks/use-prompter.ts:1230-1241`, with the "Approve & Start" text at `:1233` and `:1239`). This backs "A task begins only after your *Approve & Start*" (`README.md:69`).
3. **Nothing auto-posts to X.** Drafts are listed as held (`XPostService.list_open_posts`, `roboco/services/x_post_service.py:110`). `XPostService.approve` (`x_post_service.py:119`) is the entry point for posting, and `_post` is the only place that calls `x_client.post_tweet` (`x_post_service.py:225`). The route that reaches it, `approve_x_post` (`roboco/api/routes/x.py:54-70`), calls `_require_ceo(agent)` first. This backs "Every X draft is held in a panel queue until you approve it" (`README.md:69`).
4. **The README states the model in three places.** The tagline "One human CEO approves every merge. Nothing auto-posts. Nothing auto-starts." (`README.md:5`), the "Human governance" section (`README.md:67-69`), and the principle "Human-in-the-loop for critical decisions" (`README.md:343`).
5. **The panel carries the same claim.** The page description reads "The human CEO approves every merge." (`panel/src/app/layout.tsx:11`). The feature-flag copy for X says "Drafts X posts for CEO review; nothing auto-posts." (`panel/src/components/settings/feature-flags-card.tsx:186`).

## Does not claim

- That RoboCo has 14 agents, or that its agent count or role list matches either competitor's.
- That human0.ai or JettFounder lacks any approval step of its own. This note quotes their public copy only and has not audited their products.
- That RoboCo needs no human work. The CEO is a required participant by design.
- That every action in RoboCo waits on a human. Agents plan, build, review, and open PRs without a human present; the human gates are the merge to master, task start for held work, and posting.
- Any speed, cost, or quality comparison. No metrics are cited.

## Reflow lesson for README and docs prose

`scripts/reflow_md.py --check` (run by `make reflow-check` and by CI) rejects hard-wrapped prose: a paragraph must be one line, not wrapped at a fixed column. This applies to the README and every markdown doc. If the check fails, run `make reflow-docs` to rewrite the files, then re-run `make reflow-check` before submitting.
