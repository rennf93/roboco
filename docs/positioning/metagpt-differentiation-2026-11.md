# RoboCo vs. MetaGPT / MGX: README Differentiation

In November 2026, MetaGPT was flagged as the open-source project that already holds the "First AI Software Company" name. It drives PM, architect, project-manager and engineer roles from SOPs. The market signal cites it as MIT-licensed with 70k stars. Its commercial spinoff, MGX, is billed as "the world's first AI agent development team."

Source: [MetaGPT on GitHub](https://github.com/FoundationAgents/MetaGPT)

**Verification caveat:** the star count, license and MGX tagline above come from the market-signal task. The authoring agent had no outbound web access and could not re-fetch them. The README therefore states no star count. Re-check the repo page before quoting any number.

## What changed

`README.md` gained a short "RoboCo vs. MetaGPT" section above "Overview". It makes four comparisons. No other file changed besides this note.

1. **Governance.** A human CEO approves every merge to `master`.
2. **Gates per cell.** Each cell carries its own QA, Documenter and PR Reviewer.
3. **License and hosting.** Self-hosted, AGPL-3.0 only.
4. **Model access.** Subscription CLIs instead of metered API keys.

## Why

OSS searchers who land on the README need to see the difference at a glance. The section is short and factual. It does not criticize MetaGPT. MetaGPT claims are cited to its repository. RoboCo claims point at repo files.

## Receipts verified in this checkout

- `require_ceo_role` exists in `roboco/api/deps.py` and guards CEO-only routes.
- `AWAITING_CEO_APPROVAL` is a lifecycle status in `roboco/foundation/policy/lifecycle.py`.
- `roboco/services/gateway/choreographer/pr_gate.py` holds the in-path PR reviewer gate (`pr_pass` / `pr_fail`) and states that only the CEO merges the root→master PR.
- `pass_review` / `fail_review` live in `choreographer/qa.py`. `i_documented` lives in `choreographer/doc.py`.
- `LICENSE` is the GNU AGPL, version 3.
- `roboco/llm/providers/` holds `codex.py`, `gemini.py`, `grok.py` and `kimi.py`, each wrapping the provider's official CLI.

## Sources

- [MetaGPT](https://github.com/FoundationAgents/MetaGPT)
