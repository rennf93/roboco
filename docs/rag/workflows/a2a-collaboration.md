# A2A Collaboration Workflow

## Overview

Agents collaborate directly through the `dm` content tool on the `roboco-do` MCP server, with `read_a2a` to read what you were sent. There is no `roboco_agent_*` or `roboco_a2a_*` tool — A2A is just `dm` + `read_a2a`.

**Key:** A2A is about *existing* tasks, NOT task creation. Pass the `task_id` you're collaborating on so the message is linked to it.

## Flow

```
1. Reach out → dm(recipient, text, task_id)      for a direct message
2. Receive   → read_a2a()                        to read incoming A2A message bodies
             → notify_list() / notify_get(id)    to read your notify inbox
```

## Direct Messages (same cell only)

```python
# Direct A2A inside your cell (same team — no policy gate)
dm(
    recipient="be-qa",
    text="Quick sanity check on the rate-limit boundary before I open the PR?",
    task_id="<task>",
)
```

Cross-cell `dm` is **denied by policy**. If you need something from another cell, route it through your Cell PM via `escalate_up(task_id, reason)` — the PM coordinates across cells.

## Messaging the CEO

`dm(recipient="ceo", ...)` follows a different rule than same-cell DM: you can never *open* a CEO conversation (only reply inside one the CEO already started), and once it's open you get at most one reply per CEO message before you must wait for the CEO to post again. See `docs/rag/tools/a2a-tools.md` for the full contract and the exact refusal messages.

## Receiving Messages — `read_a2a`

Your claim briefing surfaces incoming A2A under `unread_a2a` — each entry shows the sender and a preview of their latest message. To read the full bodies (and clear them):

```python
read_a2a()      # -> {"messages": [{from_agent, content, created_at}, ...]}
```

`read_a2a()` returns only INCOMING messages (never your own sends) and marks them read. It also clears `i_am_idle()`'s unread-A2A soft-block.

## Task Creation Rules

**Only PMs create tasks** (via the `delegate` verb). Regular agents cannot create work from a `dm`.

If a conversation surfaces work that needs a new task:
1. Escalate to your Cell PM: `escalate_up(task_id, reason="Needs a subtask for X")`
2. The PM decides whether to `delegate` a subtask

## Permissions

Most roles can `dm` (same-cell) and read incoming messages with `read_a2a`, plus check their notify inbox with `notify_list` / `notify_get`.

The **Auditor** is a silent observer: it can read (`notify_list`, `notify_get`) but has **no** `dm` or `notify` — it never communicates outwardly.

Only PMs and the Board can send ack-required `notify` signals; regular agents use `dm` only.
