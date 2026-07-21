# Tool Permissions by Role

## Overview

Agents call gateway verbs through up to five MCP servers, scoped per role:

| MCP server | Provides |
|------------|----------|
| `roboco-flow` | Lifecycle verbs (give_me_work, i_will_work_on, open_pr, complete, …) |
| `roboco-do` | Content/write verbs (commit, note, dm, notify, evidence) |
| `roboco-git-readonly` | Read-only git inspection (status, log, diff, branch_list) |
| `roboco-search` | Web research (`web_search`, `web_fetch`) — `cell_pm`/`main_pm`/`product_owner`/`head_marketing` only, and only when `ROBOCO_RESEARCH_ENABLED` (default on) |
| `roboco-optimal` | RAG (`roboco_ask_mentor`, `roboco_kb_search`) |
| `roboco-docs` | Project docs file management (selected roles) |

Native shell git is blocked by the bash-guard hook for everyone. There is **no** `roboco_git_commit / _push / _create_pr / _merge_pr / _checkout` tool — write operations happen through the lifecycle verbs and the choreographer handles git as a side-effect.

The canonical source of role → verb mapping is `roboco/services/gateway/role_config.py`. The tables below summarise it.

## Developer

**Flow verbs (roboco-flow):** `give_me_work`, `i_will_work_on`, `open_pr`, `i_am_done`, `i_am_blocked`, `unclaim`, `resume`, `i_am_idle`

**Content verbs (roboco-do):** `commit`, `note`, `dm`, `evidence`

**Read-only git (roboco-git-readonly):** all 4 (`status`, `log`, `diff`, `branch_list`)

**Workspace writes:** `Write` / `Edit` in `/data/workspaces/{project}/{team}/{agent-id}/` only.

## QA

**Flow verbs:** `give_me_work`, `claim_review`, `pass`, `fail`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `dm`, `evidence` (no `commit` — QA does not write code)

**Read-only git:** all 4

**Workspace writes:** none — QA reviews only.

## Documenter

**Flow verbs:** `give_me_work`, `claim_doc_task`, `i_documented`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `commit`, `note`, `dm`, `evidence`

**Read-only git:** all 4

**Workspace writes:** docs files inside the agent's own workspace (`/data/workspaces/{project}/{team}/{agent-id}/`).

## Cell PM

**Flow verbs:** `give_me_work`, `i_will_plan`, `delegate`, `submit_up`, `triage`, `unblock`, `complete`, `escalate_up`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `dm`, `notify`, `evidence` (no `commit` — PMs delegate code; merging the leaf PR happens automatically inside `complete`)

**Read-only git:** all 4

**Web research (conditional):** `roboco-search`'s `web_search` / `web_fetch`, when `ROBOCO_RESEARCH_ENABLED` (default on).

**Workspace writes:** none.

## Main PM

**Flow verbs:** `give_me_work`, `i_will_plan`, `delegate`, `triage_all`, `unblock`, `complete`, `escalate_up`, `escalate_to_ceo`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `dm`, `notify`, `evidence`

**Read-only git:** all 4

**Web research (conditional):** `roboco-search`'s `web_search` / `web_fetch`, when `ROBOCO_RESEARCH_ENABLED` (default on).

**Workspace writes:** none. `submit_root` on a root parent task opens the root→master PR (entering the `awaiting_pr_review` gate); after the main reviewer `pr_pass`es it, `complete` escalates to the CEO. The Main PM never merges to master — only the CEO does.

## Board (Product Owner, Head of Marketing)

Both share the same flow verbs and read-only git (none), but their content verbs now diverge — the Product Owner is the sole author of the board roadmap engine's cycles.

**Flow verbs (both):** `triage`, `escalate_to_ceo`, `i_am_idle`

**Content verbs — Product Owner:** `note`, `pitch`, `propose_roadmap`, `dm`, `notify`, `evidence`

**Content verbs — Head of Marketing:** `note`, `pitch`, `dm`, `notify`, `evidence` (no `propose_roadmap`)

**Read-only git (both):** none.

**Web research (both, conditional):** `roboco-search`'s `web_search` / `web_fetch`, mounted only when `ROBOCO_RESEARCH_ENABLED` (default on).

## Auditor

**Flow verbs:** `triage`, `i_am_idle`  (read-only)

**Content verbs:** `note` (scope=reflect), `evidence`, `dm`, `read_a2a` (dm/read_a2a exist only so it can reply in-thread to a CEO-opened DM — it never initiates to a peer, so it still observes silently)

**Read-only git:** none.

## PR Reviewer

**Flow verbs:** `give_me_work`, `claim_pr_review`, `post_pr_review` (inbound external/fork + internal PRs), `claim_gate_review`, `pr_pass`, `pr_fail` (in-path assembled-PR gate), `unclaim`, `i_am_idle`  (read-only)

**Content verbs:** `note`, `evidence`, `dm`, `read_a2a`, plus notification reads (`notify_list`, `notify_get`) — the change-request itself is still posted server-side on the PR; `dm`/`read_a2a` exist so it can reply in-thread to a CEO-opened DM, and its only INITIATION target is its owning cell_pm/main_pm (the in-path gate verdict).

**Read-only git:** none.

**Workspace writes:** none — reviews inbound PRs and the org's own assembled cell→root / root→master PRs read-only; never merges.

## Prompter (Intake) & Secretary

Both are human-only roles — they chat with the CEO, not other agents.

**Flow verbs:** `i_am_idle` only.

**Content verbs:** `note`, `evidence` only (no `dm` / `notify`).

**Read-only git / workspace writes:** none.

## Tool Permissions Summary

| Capability | Dev | Doc | QA | Cell PM | Main PM | Board | Auditor |
|---|---|---|---|---|---|---|---|
| `commit` (writes code) | ✓ | ✓ | — | — | — | — | — |
| `open_pr` (opens PR) | ✓ | — | — | — | — | — | — |
| `pass` / `fail` (QA verdict) | — | — | ✓ | — | — | — | — |
| `i_documented` | — | ✓ | — | — | — | — | — |
| `delegate` (creates subtasks) | — | — | — | ✓ | ✓ | — | — |
| `complete` (merges PR) | — | — | — | ✓ | ✓ | — | — |
| `escalate_to_ceo` | — | — | — | — | ✓ | ✓ | — |
| `notify` (ack-required) | — | — | — | ✓ | ✓ | ✓ | — |
| `dm` (A2A) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `note` (journal entry) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (reflect) |
| `roboco_git_*` (read-only) | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `Write` / `Edit` (own workspace) | ✓ | ✓ | — | — | — | — | — |

**CEO** is human and never inside an agent container; the panel runs as the CEO via `X-Agent-Role: ceo` against the orchestrator API directly.
