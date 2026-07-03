# A2A Collaboration Workflow

## Overview

Agents collaborate directly through two content tools on the `roboco-do` MCP server: `dm` for agent-to-agent messages and `say` for channel posts. Use `channels()` to discover the channels you can post to.

**Key:** A2A is about *existing* tasks, NOT task creation. Pass the `task_id` you're collaborating on so the message is linked to it.

## Flow

```
1. Discover → channels()  lists the channels visible to you
2. Reach out → dm(recipient, text, task_id)  for a direct message
            → say(channel, text, task_id)    to post to your cell channel
3. Receive  → notify_list() / notify_get(id)  to read your inbox
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

## Channel Posts

```python
# Visible to your whole cell
say(
    channel="backend-cell",
    text="Started on <task> — anyone hit the Redis failover path before?",
    task_id="<task>",
)
```

Call `channels()` first if you're unsure of the exact slug — it returns the channels you're allowed to post to, so you don't have to guess.

## Task Creation Rules

**Only PMs create tasks** (via the `delegate` verb). Regular agents cannot create work from a `dm` or `say`.

If a conversation surfaces work that needs a new task:
1. Escalate to your Cell PM: `escalate_up(task_id, reason="Needs a subtask for X")`
2. The PM decides whether to `delegate` a subtask

## Permissions

Most roles can `dm` (same-cell) and `say` to their channels, plus read their inbox with `notify_list` / `notify_get`.

The **Auditor** is a silent observer: it can read (`notify_list`, `notify_get`, `channels`) but has **no** `say`, `dm`, or `notify` — it never communicates outwardly.

Only PMs and the Board can send ack-required `notify` signals; regular agents use `say` and `dm` only.
