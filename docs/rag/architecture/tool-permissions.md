# Tool Permissions by Role

## Overview

Agents call gateway verbs through three MCP servers, scoped per role:

| MCP server | Provides |
|------------|----------|
| `roboco-flow` | Lifecycle verbs (give_me_work, i_will_work_on, open_pr, complete, …) |
| `roboco-do` | Content/write verbs (commit, note, say, dm, notify, evidence) |
| `roboco-git-readonly` | Read-only git inspection (status, log, diff, branch_list) |

Native shell git is blocked by the bash-guard hook for everyone. There is **no** `roboco_git_commit / _push / _create_pr / _merge_pr / _checkout` tool — write operations happen through the lifecycle verbs and the choreographer handles git as a side-effect.

The canonical source of role → verb mapping is `roboco/services/gateway/role_config.py`. The tables below summarise it.

## Developer

**Flow verbs (roboco-flow):** `give_me_work`, `i_will_work_on`, `open_pr`, `i_am_done`, `i_am_blocked`, `unclaim`, `resume`, `i_am_idle`

**Content verbs (roboco-do):** `commit`, `note`, `say`, `dm`, `evidence`

**Read-only git (roboco-git-readonly):** all 4 (`status`, `log`, `diff`, `branch_list`)

**Workspace writes:** `Write` / `Edit` in `/data/workspaces/{project}/{team}/{agent-id}/` only.

## QA

**Flow verbs:** `give_me_work`, `claim_review`, `pass`, `fail`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `say`, `dm`, `evidence` (no `commit` — QA does not write code)

**Read-only git:** all 4

**Workspace writes:** none — QA reviews only.

## Documenter

**Flow verbs:** `give_me_work`, `claim_doc_task`, `i_documented`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `commit`, `note`, `say`, `dm`, `evidence`

**Read-only git:** all 4

**Workspace writes:** docs files inside the agent's own workspace (`/data/workspaces/{project}/{team}/{agent-id}/`).

## Cell PM

**Flow verbs:** `give_me_work`, `i_will_plan`, `delegate`, `submit_up`, `triage`, `unblock`, `complete`, `escalate_up`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `say`, `dm`, `notify`, `evidence` (no `commit` — PMs delegate code; merging the leaf PR happens automatically inside `complete`)

**Read-only git:** all 4

**Workspace writes:** none.

## Main PM

**Flow verbs:** `give_me_work`, `i_will_plan`, `delegate`, `triage_all`, `unblock`, `complete`, `escalate_up`, `escalate_to_ceo`, `unclaim`, `resume`, `i_am_idle`

**Content verbs:** `note`, `say`, `dm`, `notify`, `evidence`

**Read-only git:** all 4

**Workspace writes:** none. `complete` on a root parent task opens the master PR via the choreographer and escalates to CEO.

## Board (Product Owner, Head of Marketing)

**Flow verbs:** `triage`, `escalate_to_ceo`, `i_am_idle`

**Content verbs:** `note`, `say`, `dm`, `notify`, `evidence`

**Read-only git:** none.

## Auditor

**Flow verbs:** `triage`, `i_am_idle`  (read-only)

**Content verbs:** `note` (scope=reflect), `evidence`  (no `say` / `dm` — Auditor observes silently)

**Read-only git:** none.

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
| `say` / `dm` (channel / A2A) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `note` (journal entry) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (reflect) |
| `roboco_git_*` (read-only) | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| `Write` / `Edit` (own workspace) | ✓ | ✓ | — | — | — | — | — |

**CEO** is human and never inside an agent container; the panel runs as the CEO via `X-Agent-Role: ceo` against the orchestrator API directly.
