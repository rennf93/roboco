# External & Internal PR Review

Not every pull request against your repos comes from inside the company. A contributor or a fork can open one, or a branch can land that no agent task owns. These two opt-in loops let the read-only **PR Reviewer** look at those PRs, post a single change-request, and surface them to you — without ever chatting, merging, or pushing to anyone else's fork. This is the inbound counterpart to the in-path PR gate that already reviews assembled cell→root and root→master PRs.

## Default state

Both loops are **off by default**:

| Env var | Default | Purpose |
|---------|---------|---------|
| `ROBOCO_EXTERNAL_PR_ENABLED` | `false` | Inbound external/fork PR review. Off → the poll loop never runs and no inbound GitHub call is made. |
| `ROBOCO_INTERNAL_PR_ENABLED` | `false` | Read-only safety reviewer for org-repo PRs not tied to a task. Off → those PRs are never picked up. |

When both are off, no inbound discovery happens at all. The Settings → Feature Flags card exposes both ("Discover and review inbound external/fork pull requests" and "Run the read-only safety reviewer on internal branch PRs").

## Enable it

1. Set the flag(s) you want (`ROBOCO_EXTERNAL_PR_ENABLED` and/or `ROBOCO_INTERNAL_PR_ENABLED`), or flip the matching **Settings → Feature Flags** toggles.
2. **Restart the backend.**

## What changes when it's on

A background poll lists open PRs per active project and routes them to the read-only PR Reviewer agent, which reads the diff against your standards and posts **one** change-request comment on the PR. It never converses, never decides, and never merges.

**External PRs** (from outside contributors or forks) then land in the **PR Review Queue** on the Command Center, where the decision is yours:

- **Supersede** — the company cuts its own branch from the contributor's commits, hardens it, opens its own PR, and links back to the original once that merges.
- **Dismiss** — close the review and move on.

Either way the org never pushes to someone else's fork, and untrusted contributor code is never fetched, checked out, or executed until a human confirms it.

**Internal PRs** are org-repo (non-fork) PRs whose branch is **not** owned by an active task — branches pushed outside the agent task-flow. The reviewer runs the same read-only safety pass on them. The org's own in-flight integration PRs (whose branch a live task owns) are deliberately skipped, since those already pass QA and PM review in the normal lifecycle.

!!! note "Read-only by design"
    The PR Reviewer's only output is the change-request it posts on the PR. It holds no merge authority — see [How agents are sandboxed](../company/agent-gateway.md). The accept/supersede/dismiss decision is always yours.

## Tuning external review

| Env var | Default | Purpose |
|---------|---------|---------|
| `ROBOCO_EXTERNAL_PR_POLL_INTERVAL_SECONDS` | `300` | Seconds between inbound external-PR discovery passes (minimum 60). |
| `ROBOCO_EXTERNAL_PR_AUTHOR_ALLOWLIST` | (empty) | GitHub usernames trusted as known contributors. Empty → no author is auto-trusted; every external PR needs human confirmation. |
| `ROBOCO_EXTERNAL_PR_REQUIRE_HUMAN_CONFIRM` | `true` | Require explicit human confirmation before any agent fetches, checks out, or executes external contributor code. |

Leave `ROBOCO_EXTERNAL_PR_REQUIRE_HUMAN_CONFIRM` on unless you fully trust the inbound source — it is the gate that keeps untrusted code from running.

## Required extra config

The repos you want watched must be registered Projects with a valid git token (so the poll can list their PRs). No migration or provider key is needed beyond that.

## Next

→ **[The merge model](../company/merge-model.md#pull-requests-you-didnt-open)** — the operator-facing narrative of the Supersede / Dismiss flow. → **[How agents are sandboxed](../company/agent-gateway.md)** — why the reviewer cannot merge.
