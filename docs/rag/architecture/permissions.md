# Permissions Reference

What each role can do in the system.

## Permission Levels

| Level | Roles |
|-------|-------|
| CEO | ceo, system |
| BOARD | product_owner, head_marketing |
| AUDITOR | auditor |
| MAIN_PM | main_pm |
| CELL_PM | cell_pm |
| CELL_MEMBER | developer, qa, documenter |
| (read-only reviewer) | pr_reviewer |
| (human-only) | prompter, secretary |

`pr_reviewer` is a read-only role (QA level): the main reviewer (`pr-reviewer-1`) is board-adjacent and the three cell reviewers are team-scoped. It claims and posts inbound-PR reviews (`claim_pr_review` / `post_pr_review`) and runs the in-path assembled-PR gate (`claim_gate_review` / `pr_pass` / `pr_fail`), but creates, assigns, merges, and notifies nothing. `prompter` (intake) and `secretary` are **human-only** — they chat with the CEO and have only `note` + `evidence`, with no task or notification permissions; they don't appear in the action tables below.

## Task Permissions

| Action | CEO | Board | Auditor | Main PM | Cell PM | Dev | QA | Doc |
|--------|-----|-------|---------|---------|---------|-----|----|----|
| View All | Yes | Yes | Yes | Yes | - | - | - | - |
| View Own | - | - | - | - | Yes | Yes | Yes | Yes |
| Create (`delegate`) | - | - | - | Yes | Yes | - | - | - |
| Assign | - | - | - | Yes | Yes | - | - | - |
| Cancel | Yes | - | - | Yes | Yes | - | - | - |
| Complete (`complete`) | - | - | - | Yes | Yes | - | - | - |
| Claim | - | - | - | Yes | Yes | Yes | Yes | Yes |
| Pass QA (`pass`) | - | - | - | - | - | - | Yes | - |
| Fail QA (`fail`) | - | - | - | - | - | - | Yes | - |
| Docs Complete (`i_documented`) | - | - | - | - | - | - | - | Yes |

Notes (verified against `roboco/foundation/policy/lifecycle.py`):
- **Create / Assign** (`create_subtask`, `delegate`) are PM-only: `cell_pm` and `main_pm`. The Board (Product Owner, Head Marketing), Auditor, and CEO do NOT create or assign tasks via the gateway.
- **Cancel** is allowed to PM roles + CEO (`cell_pm`, `main_pm`, `ceo`) for all non-terminal states **except** `awaiting_ceo_approval → cancelled`, which is **CEO-only** (a PM cannot cancel a task already in the CEO's approval queue — that would bypass the human gate). The Board and Auditor CANNOT cancel.
- **Complete** (final approve/merge) is PM-only (`cell_pm`, `main_pm`). The CEO acts only on tasks escalated to `awaiting_ceo_approval`.
- **Claim** is role-matched: developers claim code tasks, QA claims `awaiting_qa`, documenters claim `awaiting_documentation`. PMs can claim the planning/coordination work assigned to them.

## Index Permissions

| Action | CEO | Board | Auditor | Main PM | Cell PM | Dev | QA | Doc |
|--------|-----|-------|---------|---------|---------|-----|----|----|
| Index Code | Yes | - | - | Yes | Yes | Yes | - | - |
| Index Docs | Yes | Yes | - | Yes | Yes | Yes | - | Yes |
| Search/Query | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| View Stats | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Clear Index | Yes | - | - | Yes | - | - | - | - |
| Refresh Index | Yes | - | - | Yes | - | - | - | - |

Note: Board (Product Owner, Head Marketing) can only index docs, not code.

## Notification Permissions

Sending notifications means calling the `notify(target, text, priority)` content tool. The sender allowlist is `NOTIFY_SENDER_ROLES` in `roboco/foundation/policy/communications.py`.

| Role | Can Send (`notify`) | Scope |
|------|---------------------|-------|
| ceo | Yes | All |
| product_owner | Yes | Management chain |
| head_marketing | Yes | Management chain |
| auditor | No | - (silent observer) |
| main_pm | Yes | All |
| cell_pm | Yes | Own cell |
| developer | No | - |
| qa | No | - |
| documenter | No | - |

Non-senders (developer, qa, documenter, auditor) still communicate via `say(channel, text)` for channel posts and `dm(recipient, text)` for direct agent-to-agent messages — those are not ack-required notifications. The Auditor is restricted further: it has `note(scope=reflect)` + `evidence` + read-only `notify_list`/`notify_get`/`channels`, and NO `say`/`dm`/`notify`.

## Task-Creator Roles

These roles can create/assign tasks (`create_subtask`, `delegate` — PM-only per `lifecycle.py`):
- `main_pm`
- `cell_pm`

The Board (`product_owner`, `head_marketing`), the Auditor, and the CEO do NOT create or assign tasks through the gateway.

## Cancellation Roles

These roles can cancel tasks from most non-terminal states (`cancel` action in `lifecycle.py`):
- `cell_pm`
- `main_pm`
- `ceo`

Exception: `awaiting_ceo_approval → cancelled` is **CEO-only** (`cell_pm` and `main_pm` are excluded from that source state).

Note: the Board and Auditor CANNOT cancel (observe/approve only).

## View Scope

| Role | Can View |
|------|----------|
| CEO | All tasks |
| Board | All tasks |
| Auditor | All tasks (silent) |
| Main PM | All tasks |
| Cell PM | Own cell + cross-cell |
| Cell Member | Own cell |
