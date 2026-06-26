# CI post-mortems

Per-incident post-mortems for regressions that broke RoboCo's CI quality gate and the work that fixed them. Each post-mortem is a **stand-alone, time-stamped record**: the failing CI job, the offending commit (pinned by message + file-coverage inspection, not interactive `git bisect run`, which is denied at the agent layer), the structural reason the gate went red, and the ranked options for the follow-up fix task.

These documents are **diagnostic artifacts**, not user guides — they are the canonical record of a CI incident and the path back to green. The fix work that follows them is tracked as separate leaf tasks (`be-dev-*`), each with its own PR.

## Index

| Date | Post-mortem | Failing job | Offending commit | Status |
|------|-------------|-------------|------------------|--------|
| 2026-06-26 | [CI coverage-gate diagnosis](CI_DIAGNOSIS_2026-06-26.md) | `quality` (`pytest --cov-fail-under=80`, total 70.93%) | `1537234 Feat/autonomous maintenance (#264)` | Fixed (PR #278 — unit tests for the new modules; coverage back to 94.93%) |

## How to read a post-mortem

Each post-mortem follows the same shape so they can be read end-to-end:

1. **TL;DR** — failing job, failing sub-step, error excerpt, offending commit, one-sentence reason.
2. **Reproducing the failure locally** — exact commands and per-sub-step results so anyone can replay the gate on their branch.
3. **Pinning the offending commit** — by commit-message inspection + post-hoc per-file coverage analysis (raw git is denied at the agent layer, so interactive `git bisect run` is unavailable).
4. **Why it broke — root cause** — the structural reason in the code or in the gate's policy, not just "this commit added lines".
5. **Suggested fix direction** — ranked options for the follow-up fix task, with cross-cell concerns flagged for `main-pm`.
6. **What the diagnosis did NOT do** — masking suppressions, cross-cell changes, and policy changes are explicitly out of scope.
7. **Reproducibility** — the exact `make quality` sequence and env vars so a future agent can re-verify.

## When to write a new post-mortem

A new entry belongs here when a `make quality` regression is diagnosed in a standalone investigation task and the fix work is split into a separate follow-up task. Inline fixes that do not need a public record live in their PR description and journal only; the post-mortem is for incidents that are worth explaining — either because the root cause was non-obvious, or because the fix-direction trade-off benefits from being recorded for future reference.

## Next

→ [Common issues](../troubleshooting/common-issues.md) for the user-facing symptom → cause → fix guide · back to the [docs home](../index.md).
